from scripts.probe_provider_capacity import ProbeResult, _parse_levels, _parse_route, summarize


def test_capacity_probe_parses_routes_and_levels() -> None:
    assert _parse_route("wawapi_openai:gpt-5.6-sol") == ("wawapi_openai", "gpt-5.6-sol")
    assert _parse_levels("4,1,2,2") == [1, 2, 4]


def test_capacity_probe_summary_is_sanitized_and_grouped() -> None:
    rows = [
        ProbeResult("wawapi_openai", "gpt-5.6-sol", "responses", 2, 1, 1, True, 1.0, 10, 2, 12),
        ProbeResult("wawapi_openai", "gpt-5.6-sol", "responses", 2, 1, 2, False, 2.0, error_kind="http_524"),
    ]
    summary = summarize(rows)
    assert summary == [
        {
            "provider": "wawapi_openai",
            "model": "gpt-5.6-sol",
            "protocol": "responses",
            "concurrency": 2,
            "attempts": 2,
            "successes": 1,
            "success_rate": 0.5,
            "latency_p50_seconds": 1.0,
            "latency_p95_seconds": 1.0,
            "latency_max_seconds": 1.0,
            "errors": ["http_524"],
        }
    ]
