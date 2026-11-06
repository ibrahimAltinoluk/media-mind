"""Global ayarlar (repo dışı) ve sağlayıcı API anahtarları.

Ayarlar: <MEDIAMIND_HOME>/settings.json
API anahtarları: macOS Anahtar Zinciri (keyring), sağlayıcı başına ayrı kayıt.
Testlerde MEDIAMIND_NO_KEYCHAIN=1 ile dosyaya düşer.
"""

from __future__ import annotations

import json
import os
from copy import deepcopy
from typing import Any

from . import registry

# Anthropic modelleri sabit listeden seçilir; diğer sağlayıcıların listesi canlı çekilir.
MODELS = {
    "claude-opus-5-5": "Claude Opus 5.5",
    "claude-sonnet-5-5": "Claude Sonnet 5.5",
    "claude-haiku-5-5": "Claude Haiku 5.5",
}

PROVIDERS: dict[str, dict[str, Any]] = {
    "anthropic": {
        "label": "Anthropic (Claude)",
        "key_prefix": "sk-ant-",
        "key_url": "https://console.anthropic.com/settings/keys",
        "env": "ANTHROPIC_API_KEY",
    },
    "openrouter": {
        "label": "OpenRouter",
        "base_url": "https://openrouter.ai/api/v1",
        "key_prefix": "sk-or-",
        "key_url": "https://openrouter.ai/keys",
        "env": "OPENROUTER_API_KEY",
    },
    "gemini": {
        "label": "Google Gemini",
        "base_url": "https://generativelanguage.googleapis.com/v1beta/openai/",
        "key_prefix": "",
        "key_url": "https://aistudio.google.com/apikey",
        "env": "GEMINI_API_KEY",
    },
}

DEFAULTS: dict[str, Any] = {
    "assistant": {
        "provider": "anthropic",
        "model": "claude-opus-5-5",  # Anthropic modeli
        "effort": "medium",          # yalnız Anthropic
        "openrouter_model": "",      # kullanıcı seçer (canlı listeden)
        "gemini_model": "",
    },
    # Yerel ML modellerinin kalitesi: standard | high (high yalnız uygun cihazda ve denemeden sonra seçilir)
    "models": {
        "speech": "standard",
    },
    "agents": {
        "read": True,
        "tag": True,
        "require_approval": True,
        "send_previews": False,
    },
}

KEYCHAIN_SERVICE = "MediaMind"


def _file():
    return registry.home() / "settings.json"


def _merge(base: dict, over: dict) -> dict:
    out = deepcopy(base)
    for k, v in over.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = v
    return out


def load() -> dict[str, Any]:
    f = _file()
    data = {}
    if f.exists():
        try:
            data = json.loads(f.read_text("utf-8"))
        except json.JSONDecodeError:
            data = {}
    return _merge(DEFAULTS, data)


def update(patch: dict[str, Any]) -> dict[str, Any]:
    cur = load()
    for section, values in patch.items():
        if section not in DEFAULTS or not isinstance(values, dict):
            continue
        for k, v in values.items():
            if k not in DEFAULTS[section]:
                continue
            if section == "assistant":
                if k == "provider" and v not in PROVIDERS:
                    raise ValueError(f"Bilinmeyen sağlayıcı: {v}")
                if k == "model" and v not in MODELS:
                    raise ValueError(f"Bilinmeyen model: {v}")
                if k == "effort" and v not in ("low", "medium", "high"):
                    raise ValueError(f"Geçersiz düşünme düzeyi: {v}")
                if k in ("openrouter_model", "gemini_model"):
                    v = str(v).strip()[:200]
            if section == "models" and v not in ("standard", "high"):
                raise ValueError(f"Geçersiz model kalitesi: {v}")
            cur[section][k] = v
    f = _file()
    tmp = f.with_suffix(".tmp")
    tmp.write_text(json.dumps(cur, ensure_ascii=False, indent=2), "utf-8")
    tmp.replace(f)
    return cur


def active_model(cfg: dict | None = None) -> tuple[str, str]:
    """→ (sağlayıcı, model kimliği)"""
    a = (cfg or load())["assistant"]
    p = a["provider"]
    if p == "anthropic":
        return p, a["model"]
    return p, a.get(f"{p}_model") or ""


# ---------------------------------------------------------------- API anahtarları

def _account(provider: str) -> str:
    if provider not in PROVIDERS:
        raise ValueError(f"Bilinmeyen sağlayıcı: {provider}")
    return f"{provider}-api-key"


def _no_keychain() -> bool:
    return bool(os.environ.get("MEDIAMIND_NO_KEYCHAIN"))


def _secret_file():
    return registry.home() / "secrets.json"


def get_api_key(provider: str = "anthropic") -> str | None:
    acc = _account(provider)
    if _no_keychain():
        f = _secret_file()
        key = json.loads(f.read_text("utf-8")).get(acc) if f.exists() else None
    else:
        import keyring

        try:
            key = keyring.get_password(KEYCHAIN_SERVICE, acc)
        except Exception:  # noqa: BLE001 - anahtar zinciri kilitli/erişilemez
            key = None
    return key or os.environ.get(PROVIDERS[provider]["env"]) or None


def set_api_key(key: str | None, provider: str = "anthropic") -> None:
    acc = _account(provider)
    if _no_keychain():
        f = _secret_file()
        data = json.loads(f.read_text("utf-8")) if f.exists() else {}
        if key:
            data[acc] = key
        else:
            data.pop(acc, None)
        f.write_text(json.dumps(data), "utf-8")
        os.chmod(f, 0o600)
        return
    import keyring

    if key:
        keyring.set_password(KEYCHAIN_SERVICE, acc, key)
    else:
        try:
            keyring.delete_password(KEYCHAIN_SERVICE, acc)
        except Exception:  # noqa: BLE001 - zaten yok
            pass


def validate_key(provider: str, key: str) -> str:
    key = key.strip()
    prefix = PROVIDERS[provider]["key_prefix"]
    if len(key) < 20 or (prefix and not key.startswith(prefix)):
        label = PROVIDERS[provider]["label"]
        hint = f" ({prefix} ile başlamalı)" if prefix else ""
        raise ValueError(f"Bu bir {label} API anahtarına benzemiyor{hint}.")
    return key


def masked_key(provider: str = "anthropic") -> str | None:
    k = get_api_key(provider)
    if not k:
        return None
    return f"{k[:7]}••••{k[-4:]}" if len(k) > 12 else "••••"
