from __future__ import annotations

import argparse
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.model_capability_registry import (  # noqa: E402
    ensure_provider_registry_sync,
    load_model_capability_registry,
    render_model_capability_markdown,
)


TARGET = ROOT / "docs" / "MODEL_CAPABILITY_REGISTRY.md"


def main() -> int:
    parser = argparse.ArgumentParser(description="校验模型能力注册表并生成审阅文档")
    parser.add_argument("--check", action="store_true", help="只检查，不修改文件")
    args = parser.parse_args()

    provider_config = __import__("json").loads(
        (ROOT / "config" / "providers.example.json").read_text(encoding="utf-8")
    )
    registry = load_model_capability_registry()
    ensure_provider_registry_sync(provider_config)
    expected = render_model_capability_markdown(registry)
    current = TARGET.read_text(encoding="utf-8") if TARGET.exists() else ""
    if args.check:
        if current != expected:
            print("模型能力审阅文档未同步，请运行：python3 scripts/sync_model_capability_docs.py")
            return 1
        print("模型配置、能力注册表和审阅文档已同步")
        return 0
    TARGET.write_text(expected, encoding="utf-8")
    print(f"已生成 {TARGET.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
