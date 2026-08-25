from __future__ import annotations

import json
import os
import secrets
import stat
import tempfile
import threading
from datetime import datetime
from pathlib import Path
from typing import Any

from .paths import DATA_ROOT, LOCAL_CONFIG_DIR, ensure_project_dirs

ALLOWED_API_KEY_NAMES = {
    "DEEPSEEK_API_KEY",
    "ARK_API_KEY",
    "DASHSCOPE_API_KEY",
    "SENSENOVA_API_KEY",
    "BAI_API_KEY",
    "OPENROUTER_API_KEY",
    "LINGSUAN_OPENAI_API_KEY",
    "LINGSUAN_IMAGE_API_KEY",
    "LINGSUAN_GOOGLE_API_KEY",
    "LINGSUAN_XAI_API_KEY",
    "LINGSUAN_ANTHROPIC_API_KEY",
}

# 0.9.0 used one shared Lingsuan key.  Its default model family was OpenAI,
# so migrate that value only to the OpenAI slot; copying it to every supplier
# would recreate the cross-supplier key routing bug this split is fixing.
LEGACY_API_KEY_ALIASES = {
    "LINGSUAN_API_KEY": "LINGSUAN_OPENAI_API_KEY",
}

API_KEY_FILE = LOCAL_CONFIG_DIR / "api_keys.json"
_API_KEY_FILE_LOCK = threading.RLock()
_LOADED_LOCAL_KEY_NAMES: set[str] = set()


class ApiKeyConfigDamaged(ValueError):
    pass


class ApiKeyConfigUnavailable(RuntimeError):
    """Safe public marker for recoverable local API configuration failures."""

    public_error_code = "api_configuration_unavailable"
    public_message = "API 配置暂时无法加载，请重试。"
    suggested_action = "请在 API 配置页面就地重试；若仍失败，请根据诊断编号检查运行日志。"

    def __init__(self, message: str, *, recovery_allowed: bool = False) -> None:
        super().__init__(message)
        self.recovery_allowed = recovery_allowed


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ApiKeyConfigDamaged("API 配置结构不受支持")
    if "keys" in value and not isinstance(value["keys"], dict):
        raise ApiKeyConfigDamaged("API 配置 keys 结构不受支持")
    return value


def _safe_recovery_source(path: Path) -> None:
    if path.is_symlink() or path.parent.is_symlink():
        raise ApiKeyConfigUnavailable(ApiKeyConfigUnavailable.public_message)
    try:
        mode = path.lstat().st_mode
    except OSError as exc:
        raise ApiKeyConfigUnavailable(ApiKeyConfigUnavailable.public_message) from exc
    if not stat.S_ISREG(mode):
        raise ApiKeyConfigUnavailable(ApiKeyConfigUnavailable.public_message)


def _copy_private_backup(source: Path) -> Path:
    timestamp = datetime.now().astimezone().strftime("%Y%m%dT%H%M%S")
    backup = source.parent / f".{source.name}.damaged-{timestamp}-{secrets.token_hex(4)}.bak"
    source_flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    target_flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    source_fd = os.open(source, source_flags)
    target_fd = -1
    try:
        if not stat.S_ISREG(os.fstat(source_fd).st_mode):
            raise ApiKeyConfigUnavailable(ApiKeyConfigUnavailable.public_message)
        target_fd = os.open(backup, target_flags, 0o600)
        os.fchmod(target_fd, 0o600)
        while True:
            chunk = os.read(source_fd, 64 * 1024)
            if not chunk:
                break
            view = memoryview(chunk)
            while view:
                written = os.write(target_fd, view)
                view = view[written:]
        os.fsync(target_fd)
        os.close(target_fd)
        target_fd = -1
        return backup
    except Exception:
        if target_fd >= 0:
            os.close(target_fd)
        backup.unlink(missing_ok=True)
        raise
    finally:
        os.close(source_fd)


def _clean_keys(value: Any) -> dict[str, str]:
    if not isinstance(value, dict):
        return {}
    cleaned = {
        str(key): str(secret).strip()
        for key, secret in value.items()
        if str(key) in ALLOWED_API_KEY_NAMES and str(secret).strip()
    }
    for legacy_name, current_name in LEGACY_API_KEY_ALIASES.items():
        legacy_secret = str(value.get(legacy_name) or "").strip()
        if legacy_secret and current_name not in cleaned:
            cleaned[current_name] = legacy_secret
    return cleaned


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
    content = json.dumps(_payload(keys), ensure_ascii=False, indent=2) + "\n"
    descriptor, temporary_name = tempfile.mkstemp(
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
    )
    temporary = Path(temporary_name)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            descriptor = -1
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        path.chmod(0o600)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        try:
            temporary.unlink(missing_ok=True)
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
        if key in ALLOWED_API_KEY_NAMES or key in LEGACY_API_KEY_ALIASES:
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
        target_name = LEGACY_API_KEY_ALIASES.get(env_name, env_name)
        if target_name in ALLOWED_API_KEY_NAMES and secret:
            values[target_name] = secret
    return _clean_keys(values)


def ensure_api_key_file() -> Path:
    if API_KEY_FILE.exists():
        return API_KEY_FILE
    with _API_KEY_FILE_LOCK:
        try:
            ensure_project_dirs()
            # The second check is deliberately inside the lock: another request
            # may have completed first-start migration while this one waited.
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
            for legacy_name, current_name in LEGACY_API_KEY_ALIASES.items():
                legacy_secret = str(os.environ.get(legacy_name) or "").strip()
                if legacy_secret and current_name not in migrated:
                    migrated[current_name] = legacy_secret
            _write_payload(API_KEY_FILE, migrated)
            return API_KEY_FILE
        except ApiKeyConfigUnavailable:
            raise
        except (json.JSONDecodeError, ApiKeyConfigDamaged) as exc:
            raise ApiKeyConfigUnavailable(ApiKeyConfigUnavailable.public_message, recovery_allowed=True) from exc
        except (OSError, ValueError) as exc:
            raise ApiKeyConfigUnavailable(ApiKeyConfigUnavailable.public_message) from exc


def read_api_keys() -> dict[str, str]:
    with _API_KEY_FILE_LOCK:
        try:
            path = ensure_api_key_file()
            _safe_recovery_source(path)
            raw = _read_json(path)
            return _clean_keys(raw.get("keys", raw))
        except ApiKeyConfigUnavailable:
            raise
        except (json.JSONDecodeError, ApiKeyConfigDamaged) as exc:
            raise ApiKeyConfigUnavailable(ApiKeyConfigUnavailable.public_message, recovery_allowed=True) from exc
        except (OSError, ValueError) as exc:
            raise ApiKeyConfigUnavailable(ApiKeyConfigUnavailable.public_message) from exc


def load_api_keys() -> dict[str, str]:
    with _API_KEY_FILE_LOCK:
        values = read_api_keys()
        for key, value in values.items():
            if key not in os.environ:
                os.environ[key] = value
                _LOADED_LOCAL_KEY_NAMES.add(key)
        return values


def update_api_key_values(values: dict[str, str]) -> dict[str, Any]:
    with _API_KEY_FILE_LOCK:
        try:
            current = read_api_keys()
            changed = False
            environment_updates: dict[str, str] = {}
            for key, value in values.items():
                name = str(key)
                if name not in ALLOWED_API_KEY_NAMES:
                    continue
                secret = str(value).strip()
                if secret:
                    changed = changed or current.get(name) != secret
                    current[name] = secret
                    environment_updates[name] = secret
                elif name in current:
                    changed = True
                    current.pop(name, None)
                    environment_updates[name] = ""
            if changed:
                _write_payload(API_KEY_FILE, current)
            # Keep process state consistent with durable state: environment
            # changes happen only after the atomic replacement succeeds.
            for name, secret in environment_updates.items():
                if secret:
                    os.environ[name] = secret
                    _LOADED_LOCAL_KEY_NAMES.add(name)
                else:
                    os.environ.pop(name, None)
                    _LOADED_LOCAL_KEY_NAMES.discard(name)
            return {
                "updated": changed,
                "config_exists": API_KEY_FILE.exists(),
                "config_path": str(API_KEY_FILE),
                "configured_keys": sorted(current),
            }
        except ApiKeyConfigUnavailable:
            raise
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            raise ApiKeyConfigUnavailable(ApiKeyConfigUnavailable.public_message) from exc


def api_key_file_info() -> dict[str, Any]:
    with _API_KEY_FILE_LOCK:
        values = read_api_keys()
        return {
            "path": str(API_KEY_FILE),
            "exists": API_KEY_FILE.exists(),
            "configured_keys": sorted(values),
            "configured_count": len(values),
        }


def recover_damaged_api_key_file() -> dict[str, Any]:
    with _API_KEY_FILE_LOCK:
        try:
            _safe_recovery_source(API_KEY_FILE)
            try:
                _read_json(API_KEY_FILE)
            except (json.JSONDecodeError, ApiKeyConfigDamaged):
                pass
            else:
                return {"recovered": False, "already_recovered": True, "backup_created": False}
            backup = _copy_private_backup(API_KEY_FILE)
            try:
                _write_payload(API_KEY_FILE, {})
            except Exception:
                # The backup is intentionally retained, while atomic replacement
                # guarantees the damaged original remains at its fixed path.
                raise
            for name in tuple(_LOADED_LOCAL_KEY_NAMES):
                os.environ.pop(name, None)
                _LOADED_LOCAL_KEY_NAMES.discard(name)
            return {
                "recovered": True,
                "already_recovered": False,
                "backup_created": backup.is_file(),
                "configured_count": 0,
            }
        except ApiKeyConfigUnavailable:
            raise
        except (OSError, ValueError) as exc:
            raise ApiKeyConfigUnavailable(ApiKeyConfigUnavailable.public_message) from exc
