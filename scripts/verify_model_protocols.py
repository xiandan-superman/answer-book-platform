from __future__ import annotations

import argparse
import json
import re
import sys
import tempfile
import time
from dataclasses import asdict, dataclass, replace
from datetime import datetime
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.llm_client import create_llm_client, parse_json_content_with_result  # noqa: E402
from app.provider_errors import classify_provider_error  # noqa: E402
from app.settings import ProviderConfig, list_providers  # noqa: E402


TEXT_PROTOCOLS = ("responses", "chat_completions")
IMAGE_PROTOCOL = "images_generations"


@dataclass(frozen=True)
class VerificationResult:
    provider: str
    model: str
    kind: str
    protocol: str
    configured_protocol: str
    attempt: int
    status: str
    elapsed_seconds: float
    structured_output: bool | None = None
    streaming_complete: bool | None = None
    error_kind: str = ""
    error_title: str = ""
    responsibility: str = ""
    retryable: bool | None = None
    http_status: int | None = None


def _models(provider: ProviderConfig, *, image: bool) -> list[str]:
    values = (
        (provider.image_model, *provider.image_model_options)
        if image
        else (provider.default_model, provider.vision_model, *provider.model_options, *provider.vision_model_options)
    )
    return list(dict.fromkeys(str(value).strip() for value in values if str(value).strip()))


def _configured_protocol(provider: ProviderConfig, model: str) -> str:
    profile = dict((provider.model_profiles or {}).get(model) or {})
    return str(profile.get("api_protocol") or provider.api_protocol or "chat_completions").strip().lower()


def _pin_protocol(provider: ProviderConfig, model: str, protocol: str) -> ProviderConfig:
    profiles = {key: dict(value) for key, value in (provider.model_profiles or {}).items()}
    profile = dict(profiles.get(model) or {})
    profile["api_protocol"] = protocol
    profile["messages_fallback_to_chat"] = False
    profiles[model] = profile
    return replace(
        provider,
        api_protocol=protocol,
        default_model=model,
        model_profiles=profiles,
        responses_fallback_to_chat=False,
    )


def _responsibility(error_kind: str) -> str:
    if error_kind in {
        "provider_missing_api_key",
        "provider_authentication",
        "provider_quota_exhausted",
        "provider_permission",
        "provider_target_not_found",
    }:
        return "user_or_account_configuration"
    if error_kind in {"provider_invalid_request", "provider_gateway_client_blocked"}:
        return "platform_route_configuration"
    if error_kind.startswith("provider_"):
        return "provider_service"
    if error_kind == "output_contract_invalid":
        return "model_output_contract"
    return "undetermined"


def _error_result(
    *,
    provider: ProviderConfig,
    model: str,
    kind: str,
    protocol: str,
    configured_protocol: str,
    attempt: int,
    started: float,
    error: BaseException,
) -> VerificationResult:
    info = classify_provider_error(
        error,
        status_code=getattr(error, "status_code", None),
        transport_phase=str(getattr(error, "transport_phase", "") or ""),
        retry_after_seconds=getattr(error, "retry_after_seconds", None),
    )
    error_kind = info.kind
    if error_kind == "provider_unknown" and re.search(
        r"json|structured|empty response|finish_reason|output", str(error), re.IGNORECASE
    ):
        error_kind = "output_contract_invalid"
    return VerificationResult(
        provider=provider.name,
        model=model,
        kind=kind,
        protocol=protocol,
        configured_protocol=configured_protocol,
        attempt=attempt,
        status="failed",
        elapsed_seconds=round(time.monotonic() - started, 3),
        structured_output=False if kind == "text_generation" else None,
        streaming_complete=False if protocol == "responses" else None,
        error_kind=error_kind,
        error_title=info.title,
        responsibility=_responsibility(error_kind),
        retryable=info.retryable,
        http_status=info.status_code,
    )


def _verify_text(
    provider: ProviderConfig, model: str, protocol: str, timeout: int, *, attempt: int
) -> VerificationResult:
    configured_protocol = _configured_protocol(provider, model)
    started = time.monotonic()
    if not provider.api_key:
        return VerificationResult(
            provider=provider.name,
            model=model,
            kind="text_generation",
            protocol=protocol,
            configured_protocol=configured_protocol,
            attempt=attempt,
            status="not_tested_missing_key",
            elapsed_seconds=0.0,
            responsibility="user_or_account_configuration",
        )
    pinned = _pin_protocol(provider, model, protocol)
    client = create_llm_client(pinned)
    try:
        result = client.chat_json(
            [
                {"role": "system", "content": "Return exactly one compact JSON object."},
                {
                    "role": "user",
                    "content": (
                        '{"ok":true,"probe":"model_protocol_verification"}. '
                        "Return those exact keys and values as JSON."
                    ),
                },
            ],
            model=model,
            max_tokens=128,
            thinking="low",
            timeout=timeout,
            task_stage="model_protocol_verification",
        )
        parsed = parse_json_content_with_result(result)
        valid = parsed.get("ok") is True and parsed.get("probe") == "model_protocol_verification"
        if not valid:
            raise ValueError("structured output did not satisfy the verification contract")
        request = result.raw.get("_request", {}) if isinstance(result.raw, dict) else {}
        used = str(request.get("protocol_used") or protocol).strip().lower()
        if used != protocol:
            raise ValueError("protocol fallback occurred during a no-fallback verification")
        return VerificationResult(
            provider=provider.name,
            model=model,
            kind="text_generation",
            protocol=protocol,
            configured_protocol=configured_protocol,
            attempt=attempt,
            status="passed",
            elapsed_seconds=round(time.monotonic() - started, 3),
            structured_output=True,
            streaming_complete=(True if protocol == "responses" else None),
        )
    except BaseException as exc:
        return _error_result(
            provider=provider,
            model=model,
            kind="text_generation",
            protocol=protocol,
            configured_protocol=configured_protocol,
            attempt=attempt,
            started=started,
            error=exc,
        )


def _verify_image(provider: ProviderConfig, model: str, timeout: int, *, attempt: int) -> VerificationResult:
    protocol = (
        _configured_protocol(provider, model)
        if provider.name == "wawapi_image_google"
        else IMAGE_PROTOCOL
    )
    started = time.monotonic()
    if not provider.api_key:
        return VerificationResult(
            provider=provider.name,
            model=model,
            kind="image_generation",
            protocol=protocol,
            configured_protocol=protocol,
            attempt=attempt,
            status="not_tested_missing_key",
            elapsed_seconds=0.0,
            responsibility="user_or_account_configuration",
        )
    try:
        with tempfile.TemporaryDirectory(prefix="answer-book-model-verification-") as directory:
            output = Path(directory) / "probe.png"
            client = create_llm_client(provider)
            result = client.generate_image(
                "A plain black circle centered on a white background. No text.",
                output,
                model=model,
                size=provider.image_size,
                timeout=timeout,
            )
            if not result.path.exists() or result.path.stat().st_size < 100:
                raise ValueError("image endpoint did not produce a usable file")
        return VerificationResult(
            provider=provider.name,
            model=model,
            kind="image_generation",
            protocol=protocol,
            configured_protocol=protocol,
            attempt=attempt,
            status="passed",
            elapsed_seconds=round(time.monotonic() - started, 3),
        )
    except BaseException as exc:
        return _error_result(
            provider=provider,
            model=model,
            kind="image_generation",
            protocol=protocol,
            configured_protocol=protocol,
            attempt=attempt,
            started=started,
            error=exc,
        )


def run_verification(
    *,
    timeout: int,
    image_timeout: int,
    include_images: bool,
    provider_filters: set[str] | None = None,
    model_filters: set[str] | None = None,
    protocol_filters: set[str] | None = None,
    repeats: int = 1,
) -> dict[str, Any]:
    results: list[VerificationResult] = []
    providers = list_providers()
    for provider in providers.values():
        if provider_filters and provider.name not in provider_filters:
            continue
        if provider.supports_text_generation:
            for model in _models(provider, image=False):
                if model_filters and model not in model_filters:
                    continue
                configured = _configured_protocol(provider, model)
                protocols = list(TEXT_PROTOCOLS)
                if configured in {"anthropic_messages", "messages"}:
                    protocols.append("anthropic_messages")
                for protocol in protocols:
                    if protocol_filters and protocol not in protocol_filters:
                        continue
                    for attempt in range(1, repeats + 1):
                        print(
                            f"VERIFY {provider.name}/{model} {protocol} attempt={attempt}",
                            file=sys.stderr,
                            flush=True,
                        )
                        results.append(_verify_text(provider, model, protocol, timeout, attempt=attempt))
        if include_images and provider.supports_image_generation:
            for model in _models(provider, image=True):
                if model_filters and model not in model_filters:
                    continue
                if protocol_filters and IMAGE_PROTOCOL not in protocol_filters:
                    continue
                for attempt in range(1, repeats + 1):
                    print(
                        f"VERIFY {provider.name}/{model} {IMAGE_PROTOCOL} attempt={attempt}",
                        file=sys.stderr,
                        flush=True,
                    )
                    results.append(_verify_image(provider, model, image_timeout, attempt=attempt))

    rows = [asdict(result) for result in results]
    statuses = sorted({row["status"] for row in rows})
    return {
        "schema_version": 1,
        "kind": "model_protocol_verification",
        "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "scope": "all_configured_catalog_models",
        "credentials_recorded": False,
        "request_or_response_content_recorded": False,
        "fallback_allowed": False,
        "repeats": repeats,
        "text_protocols_tested": list(TEXT_PROTOCOLS),
        "image_protocol_tested": IMAGE_PROTOCOL if include_images else None,
        "summary": {
            "total": len(rows),
            "by_status": {status: sum(row["status"] == status for row in rows) for status in statuses},
            "passed_configured_routes": sum(
                row["status"] == "passed" and row["protocol"] == row["configured_protocol"] for row in rows
            ),
            "failed_configured_routes": sum(
                row["status"] == "failed" and row["protocol"] == row["configured_protocol"] for row in rows
            ),
        },
        "results": rows,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Verify every catalog model/protocol route without storing credentials or response content."
    )
    parser.add_argument("--timeout", type=int, default=45)
    parser.add_argument("--image-timeout", type=int, default=240)
    parser.add_argument("--skip-images", action="store_true")
    parser.add_argument("--provider", action="append", default=[])
    parser.add_argument("--model", action="append", default=[])
    parser.add_argument("--protocol", action="append", default=[])
    parser.add_argument("--repeats", type=int, default=1)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if not 10 <= args.timeout <= 180:
        parser.error("--timeout must be between 10 and 180 seconds")
    if not 30 <= args.image_timeout <= 600:
        parser.error("--image-timeout must be between 30 and 600 seconds")
    if not 1 <= args.repeats <= 3:
        parser.error("--repeats must be between 1 and 3")
    report = run_verification(
        timeout=args.timeout,
        image_timeout=args.image_timeout,
        include_images=not args.skip_images,
        provider_filters=set(args.provider) or None,
        model_filters=set(args.model) or None,
        protocol_filters=set(args.protocol) or None,
        repeats=args.repeats,
    )
    rendered = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    print(rendered, end="")
    return 1 if report["summary"]["failed_configured_routes"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
