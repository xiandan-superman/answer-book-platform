from scripts.audit_third_party_notices import audit_third_party_notices


def test_every_runtime_dependency_and_vendored_component_has_a_notice() -> None:
    report = audit_third_party_notices()
    assert report["ok"], report
