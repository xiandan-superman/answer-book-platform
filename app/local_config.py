from __future__ import annotations

import os
from pathlib import Path

from .paths import DATA_ROOT, ensure_project_dirs


PROJECT_ROOT = DATA_ROOT


ALLOWED_SECRET_KEYS = {
    "OPENAI_API_KEY",
    "DEEPSEEK_API_KEY",
    "ARK_API_KEY",
    "DASHSCOPE_API_KEY",
    "YUNWU_API_KEY",
}


def read_env_lines(path: Path) -> list[str]:
    if not path.exists():
        return []
    return path.read_text(encoding="utf-8").splitlines()


def update_dotenv_values(values: dict[str, str]) -> dict[str, bool]:
    ensure_project_dirs()
    env_path = PROJECT_ROOT / ".env"
    cleaned = {k: str(v).strip() for k, v in values.items() if k in ALLOWED_SECRET_KEYS and str(v).strip()}
    if not cleaned:
        return {"updated": False, "env_exists": env_path.exists()}

    lines = read_env_lines(env_path)
    seen = set()
    output: list[str] = []
    for line in lines:
        if "=" not in line or line.strip().startswith("#"):
            output.append(line)
            continue
        key = line.split("=", 1)[0].strip()
        if key in cleaned:
            output.append(f"{key}={cleaned[key]}")
            seen.add(key)
        else:
            output.append(line)
    for key, value in cleaned.items():
        if key not in seen:
            output.append(f"{key}={value}")
    env_path.write_text("\n".join(output).rstrip() + "\n", encoding="utf-8")
    for key, value in cleaned.items():
        os.environ[key] = value
    return {"updated": True, "env_exists": True}
