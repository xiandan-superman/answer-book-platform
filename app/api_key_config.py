from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from .paths import DATA_ROOT, LOCAL_CONFIG_DIR, ensure_project_dirs


ALLOWED_API_KEY_NAMES = {
    "DEEPSEEK_API_KEY",
    "ARK_API_KEY",
    "DASHSCOPE_API_KEY",
    "YUNWU_API_KEY",
    "LINGSUAN_API_KEY",
}

API_KEY_FILE = LOCAL_CONFIG_DIR / "api_keys.json"


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path.name} 顶层必须是 JSON 对象")
    return value


def _clean_keys(value: Any) -> dict[str, str]:
    if not isinstance(value, dict):
        return {}
    return {
        str(key): str(secret).strip()
        for key, secret in value.items()
        if str(key) in ALLOWED_API_KEY_NAMES and str(secret).strip()
    }


def _payload(keys: dict[str, str]) -> dict[str, Any]:
    ordered = {name: keys.get(name, "") for name in sorted(ALLOWED_API_KEY_NAMES)}
    return {
        "_说明": [
            "这是平台内部保存的 API Key 配置文件，更新或替换程序不会覆盖它。",
            "请通过平台中的“API 配置”页面测试并保存，不需要手动编辑本文件。",
            "不要把包含真实 Key 的文件上传到 GitHub、网盘公开链接或发送给无关人员。",
        ],
        "keys": ordered,
    }


def _write_payload(path: Path, keys: dict[str, str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(_payload(keys), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    try:
        temporary.chmod(0o600)
    except OSError:
        pass
    temporary.replace(path)
    try:
        path.chmod(0o600)
    except OSError:
        pass


def _legacy_dotenv_keys() -> dict[str, str]:
    path = DATA_ROOT / ".env"
    if not path.exists():
        return {}
    values: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if key in ALLOWED_API_KEY_NAMES:
            values[key] = value.strip().strip('"').strip("'")
    return _clean_keys(values)


def _legacy_provider_keys() -> dict[str, str]:
    path = LOCAL_CONFIG_DIR / "providers.local.json"
    try:
        providers = _read_json(path).get("providers", {})
    except (OSError, ValueError, json.JSONDecodeError):
        return {}
    values: dict[str, str] = {}
    if not isinstance(providers, dict):
        return values
    for provider in providers.values():
        if not isinstance(provider, dict):
            continue
        env_name = str(provider.get("api_key_env") or "").strip()
        secret = str(provider.get("api_key") or "").strip()
        if env_name in ALLOWED_API_KEY_NAMES and secret:
            values[env_name] = secret
    return values


def ensure_api_key_file() -> Path:
    ensure_project_dirs()
    if API_KEY_FILE.exists():
        return API_KEY_FILE
    migrated: dict[str, str] = {}
    migrated.update(_legacy_provider_keys())
    migrated.update(_legacy_dotenv_keys())
    migrated.update(
        {
            name: str(os.environ.get(name) or "").strip()
            for name in ALLOWED_API_KEY_NAMES
            if str(os.environ.get(name) or "").strip()
        }
    )
    _write_payload(API_KEY_FILE, migrated)
    return API_KEY_FILE


def read_api_keys() -> dict[str, str]:
    path = ensure_api_key_file()
    raw = _read_json(path)
    return _clean_keys(raw.get("keys", raw))


def load_api_keys() -> dict[str, str]:
    values = read_api_keys()
    for key, value in values.items():
        os.environ.setdefault(key, value)
    return values


def update_api_key_values(values: dict[str, str]) -> dict[str, Any]:
    current = read_api_keys()
    changed = False
    for key, value in values.items():
        name = str(key)
        if name not in ALLOWED_API_KEY_NAMES:
            continue
        secret = str(value).strip()
        if secret:
            changed = changed or current.get(name) != secret
            current[name] = secret
            os.environ[name] = secret
        elif name in current:
            changed = True
            current.pop(name, None)
            os.environ.pop(name, None)
    if changed:
        _write_payload(API_KEY_FILE, current)
    return {
        "updated": changed,
        "config_exists": API_KEY_FILE.exists(),
        "config_path": str(API_KEY_FILE),
        "configured_keys": sorted(current),
    }


def api_key_file_info() -> dict[str, Any]:
    values = read_api_keys()
    return {
        "path": str(API_KEY_FILE),
        "exists": API_KEY_FILE.exists(),
        "configured_keys": sorted(values),
        "configured_count": len(values),
    }
