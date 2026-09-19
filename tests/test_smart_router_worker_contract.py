from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKER = (ROOT / "cloudflare-smart-router" / "src" / "index.ts").read_text(encoding="utf-8")
README = (ROOT / "cloudflare-smart-router" / "README.md").read_text(encoding="utf-8")


def test_router_accepts_object_or_string_json_bindings() -> None:
    assert "function parseJsonValue(value: unknown" in WORKER
    assert 'if (typeof value !== "string") return value;' in WORKER
    assert 'parseJsonObject("CLIENT_KEYS_JSON", env.CLIENT_KEYS_JSON)' in WORKER
    assert 'JSON.parse(String(env.CLIENT_KEYS_JSON' not in WORKER


def test_all_router_configuration_uses_shared_safe_parsing() -> None:
    assert 'parseJsonObject("ROUTER_CONFIG_JSON", env.ROUTER_CONFIG_JSON)' in WORKER
    assert 'parseJsonObject("PROVIDER_CONCURRENCY_JSON", configuredJson)' in WORKER
    assert 'const parsed = parseJsonValue(value, []);' in WORKER


def test_production_deploy_preserves_remote_access_keys() -> None:
    assert "npx wrangler deploy --keep-vars" in README
    assert "CLIENT_KEYS_JSON" in README
