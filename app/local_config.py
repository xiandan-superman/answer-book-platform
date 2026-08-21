from __future__ import annotations

from .api_key_config import ALLOWED_API_KEY_NAMES, update_api_key_values


ALLOWED_SECRET_KEYS = ALLOWED_API_KEY_NAMES


def update_dotenv_values(values: dict[str, str]) -> dict[str, object]:
    result = update_api_key_values(values)
    return {
        "updated": bool(result.get("updated")),
        "env_exists": bool(result.get("config_exists")),
        "config_exists": bool(result.get("config_exists")),
        "config_path": str(result.get("config_path") or ""),
    }
