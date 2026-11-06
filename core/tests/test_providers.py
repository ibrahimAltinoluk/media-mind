from types import SimpleNamespace

import pytest
from openai.types import Model
from openai.types.chat import ChatCompletionMessage

from mediamind import assistant as assistant_mod
from mediamind import settings
from mediamind.assistant import Assistant, AssistantError

from .conftest import HAVE_EXIFTOOL


def msg(content=None, calls=None, **extra):
    d = {"role": "assistant", "content": content, **extra}
    if calls:
        d["tool_calls"] = [{"id": cid, "type": "function", "function": {"name": n, "arguments": a}} for cid, n, a in calls]
    return ChatCompletionMessage.model_validate(d)


class FakeOpenAI:
    def __init__(self, script):
        self.script = list(script)
        self.calls = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))

    def create(self, **kw):
        self.calls.append({**kw, "messages": [dict(m) for m in kw["messages"]]})
        m, finish = self.script.pop(0)
        return SimpleNamespace(choices=[SimpleNamespace(message=m, finish_reason=finish)])


def test_provider_settings_and_keys():
    s = settings.update({"assistant": {"provider": "gemini", "gemini_model": " gemini-x "}})
    assert settings.active_model(s) == ("gemini", "gemini-x")
    with pytest.raises(ValueError):
        settings.update({"assistant": {"provider": "openai"}})
    settings.set_api_key("sk-or-v1-abcdefghijklmnopqrstuvwxyz", "openrouter")
    settings.set_api_key("AIzaSyTestKey1234567890abcdef", "gemini")
    assert settings.get_api_key("openrouter").startswith("sk-or-")
    assert settings.get_api_key("gemini").startswith("AIza")
    assert settings.get_api_key("anthropic") is None  # anahtarlar birbirinden ayrı
    with pytest.raises(ValueError):
        settings.validate_key("openrouter", "sk-ant-yanlis-saglayici-anahtari")
    assert settings.validate_key("gemini", " AIzaSyTestKey1234567890abcdef ") == "AIzaSyTestKey1234567890abcdef"


@pytest.mark.skipif(not HAVE_EXIFTOOL, reason="exiftool gerekli")
def test_openai_compatible_loop(tracked_repo, monkeypatch):
    settings.update({"assistant": {"provider": "gemini", "gemini_model": "gemini-test"}})
    settings.set_api_key("AIzaSyTestKey1234567890abcdef", "gemini")
    fake = FakeOpenAI([
        (msg(None, [("c1", "search_media", '{"year": 2024}'), ("c2", "list_facets", "{bozuk")],
             extra_content={"google": {"thought_signature": "imza-1"}}), "tool_calls"),
        (msg(None, [("c3", "create_plan", '{"kind": "trash", "file_ids": [1]}')]), "tool_calls"),
        (msg("1 dosyayı çöpe taşıyacak plan hazır."), "stop"),
    ])
    a = Assistant(lambda ref: tracked_repo)
    monkeypatch.setattr(a, "_openai_client", lambda provider: fake)
    res = a.chat(tracked_repo.id, "2024'ten bir dosyayı sil")
    assert res["provider"] == "gemini" and res["model"] == "gemini-test"
    assert res["reply"] == "1 dosyayı çöpe taşıyacak plan hazır."
    assert [e["name"] for e in res["events"]] == ["search_media", "list_facets", "create_plan"]
    assert res["events"][1]["error"] == "geçersiz argüman"
    assert len(res["plans"]) == 1 and res["files"]
    first = fake.calls[0]
    assert first["model"] == "gemini-test" and first["messages"][0]["role"] == "system"
    assert {t["function"]["name"] for t in first["tools"]} >= {"search_media", "create_plan"}
    assert "get_preview_image" not in {t["function"]["name"] for t in first["tools"]}
    second = fake.calls[1]["messages"]
    # Gemini düşünce imzası asistan mesajında olduğu gibi geri gönderilir
    asst = next(m for m in second if m["role"] == "assistant")
    assert asst["extra_content"] == {"google": {"thought_signature": "imza-1"}}
    tool_msgs = [m for m in second if m["role"] == "tool"]
    assert [m["tool_call_id"] for m in tool_msgs] == ["c1", "c2"]

    # Sağlayıcı değişince yeni konuşma başlar
    settings.update({"assistant": {"provider": "openrouter", "openrouter_model": "vendor/model"}})
    settings.set_api_key("sk-or-v1-abcdefghijklmnopqrstuvwxyz", "openrouter")
    fake2 = FakeOpenAI([(msg("Merhaba."), "stop")])
    monkeypatch.setattr(a, "_openai_client", lambda provider: fake2)
    res2 = a.chat(tracked_repo.id, "selam", conv_id=res["conversation_id"])
    assert res2["conversation_id"] != res["conversation_id"]
    assert len(fake2.calls[0]["messages"]) == 2  # sistem + kullanıcı


@pytest.mark.skipif(not HAVE_EXIFTOOL, reason="exiftool gerekli")
def test_missing_model_or_key(tracked_repo):
    settings.update({"assistant": {"provider": "openrouter", "openrouter_model": ""}})
    a = Assistant(lambda ref: tracked_repo)
    with pytest.raises(AssistantError, match="model seçilmedi"):
        a.chat(tracked_repo.id, "selam")
    settings.update({"assistant": {"openrouter_model": "vendor/model"}})
    with pytest.raises(AssistantError, match="anahtarı tanımlı değil"):
        a.chat(tracked_repo.id, "selam")


def test_list_models_filters(monkeypatch):
    def fake_openai(**kw):
        base = kw["base_url"]
        if "openrouter" in base:
            data = [Model.model_validate({"id": "a/araclı", "object": "model", "created": 0, "owned_by": "x",
                                          "name": "Araçlı", "supported_parameters": ["tools", "temperature"]}),
                    Model.model_validate({"id": "b/aracsiz", "object": "model", "created": 0, "owned_by": "x",
                                          "supported_parameters": ["temperature"]})]
        else:
            data = [Model.model_validate({"id": "models/gemini-pro-test", "object": "model", "created": 0, "owned_by": "google"}),
                    Model.model_validate({"id": "models/text-embedding-004", "object": "model", "created": 0, "owned_by": "google"}),
                    Model.model_validate({"id": "models/gemini-embedding-001", "object": "model", "created": 0, "owned_by": "google"})]
        return SimpleNamespace(models=SimpleNamespace(list=lambda: data))

    monkeypatch.setattr(assistant_mod.openai, "OpenAI", fake_openai)
    assistant_mod._model_cache.clear()
    assert [m["id"] for m in assistant_mod.list_models("openrouter")] == ["a/araclı"]
    with pytest.raises(AssistantError):
        assistant_mod.list_models("gemini")  # anahtar yok
    settings.set_api_key("AIzaSyTestKey1234567890abcdef", "gemini")
    assert [m["id"] for m in assistant_mod.list_models("gemini")] == ["gemini-pro-test"]
    assert [m["id"] for m in assistant_mod.list_models("anthropic")][0] == "claude-opus-5-5"
