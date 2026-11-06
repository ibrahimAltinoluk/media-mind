import asyncio
import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from mediamind import plans, settings
from mediamind.assistant import Assistant, AssistantError

from .conftest import HAVE_EXIFTOOL

pytestmark = pytest.mark.skipif(not HAVE_EXIFTOOL, reason="exiftool gerekli")


def test_mcp_stdio_session(tracked_repo, mm_home):
    from mcp.client.session import ClientSession
    from mcp.client.stdio import StdioServerParameters, stdio_client
    import mcp.types as types

    exe = str(Path(sys.executable).parent / "mediamind")
    params = StdioServerParameters(command=exe, args=["mcp"], env={**os.environ, "MEDIAMIND_HOME": str(mm_home)})

    async def run():
        async with stdio_client(params) as (r, w):
            async with ClientSession(r, w, client_info=types.Implementation(name="test-istemci", version="1")) as s:
                await s.initialize()
                names = {t.name for t in (await s.list_tools()).tools}
                assert {"search_media", "create_plan", "apply_plan"} <= names
                assert "get_preview_image" not in names  # izin kapalıyken listelenmez
                repos = json.loads((await s.call_tool("list_repos", {})).content[0].text)
                assert repos["repos"][0]["name"] == "Aile"
                res = json.loads((await s.call_tool("search_media", {"year": 2024})).content[0].text)
                assert res["total"] == 3
                ids = [i["file_id"] for i in res["items"]]
                plan = json.loads((await s.call_tool("create_plan", {"kind": "trash", "file_ids": ids[:1]})).content[0].text)
                applied = json.loads((await s.call_tool("apply_plan", {"plan_id": plan["plan_id"]})).content[0].text)
                assert applied["applied"] is False
                bad = await s.call_tool("create_plan", {"kind": "nope"})
                assert bad.is_error
                return plan["plan_id"]

    pid = asyncio.run(run())
    p = plans.get(tracked_repo, pid)
    assert p["status"] == "pending" and p["source"] == "mcp:test-istemci"


# ---------------------------------------------------------------- asistan (sahte Claude istemcisi)

def block(type_, **kw):
    return SimpleNamespace(type=type_, **kw)


class FakeClient:
    def __init__(self, script):
        self.script = list(script)
        self.calls = []
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self.create))

    def create(self, **kw):
        self.calls.append({**kw, "messages": list(kw["messages"])})  # o anki görüntü
        content, stop = self.script.pop(0)
        return SimpleNamespace(content=content, stop_reason=stop)


def test_assistant_tool_loop(tracked_repo, monkeypatch):
    settings.set_api_key("sk-ant-test-1234567890abcd")
    fake = FakeClient([
        ([block("text", text="Bakıyorum."), block("tool_use", id="t1", name="search_media", input={"year": 2024, "folder": "DCIM"})], "tool_use"),
        ([block("tool_use", id="t2", name="create_plan", input={"kind": "move", "file_ids": [], "destination_folder": "X"}),
          block("tool_use", id="t3", name="search_media", input={"year": 2024, "folder": "DCIM"})], "tool_use"),
        ([block("tool_use", id="t4", name="create_plan", input={"kind": "move", "file_ids": [1, 2], "destination_folder": "Bodrum 2024"})], "tool_use"),
        ([block("text", text="2 fotoğrafı taşıyacak bir plan hazırladım.")], "end_turn"),
    ])
    a = Assistant(lambda ref: tracked_repo)
    monkeypatch.setattr(a, "_client", lambda: fake)
    res = a.chat(tracked_repo.id, "2024 DCIM fotoğraflarını Bodrum 2024'e taşı", context={"page": "library"})
    assert res["reply"] == "Bakıyorum.\n\n2 fotoğrafı taşıyacak bir plan hazırladım."
    assert [e["name"] for e in res["events"]] == ["search_media", "create_plan", "search_media", "create_plan"]
    assert "error" in res["events"][1]  # boş file_ids → is_error sonucu, döngü sürdü
    assert len(res["plans"]) == 1 and len(res["files"]) == 2
    first = fake.calls[0]
    assert first["model"] == "claude-opus-5-5" and first["fallbacks"] == "default"
    assert first["output_config"] == {"effort": "medium"}
    # İkinci turdaki paralel iki araç sonucu tek kullanıcı mesajında döner
    tool_msg = fake.calls[2]["messages"][-1]
    assert tool_msg["role"] == "user" and [r["tool_use_id"] for r in tool_msg["content"]] == ["t2", "t3"]
    assert tool_msg["content"][0]["is_error"] is True
    # Konuşma devam eder: geçmiş korunur
    fake.script.append(([block("text", text="Tamam.")], "end_turn"))
    res2 = a.chat(tracked_repo.id, "teşekkürler", conv_id=res["conversation_id"])
    assert res2["conversation_id"] == res["conversation_id"]
    assert len(fake.calls[-1]["messages"]) == 9


def test_assistant_without_key(tracked_repo):
    a = Assistant(lambda ref: tracked_repo)
    with pytest.raises(AssistantError):
        a.chat(tracked_repo.id, "merhaba")
