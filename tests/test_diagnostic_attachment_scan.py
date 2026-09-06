import gzip
import json

from app import model_diagnostics as diagnostics


def test_no_attachments_never_reads_historical_traces(tmp_path, monkeypatch):
    (tmp_path / "attachments").mkdir()

    def forbidden(_root):
        raise AssertionError("history should not be scanned without attachments")

    monkeypatch.setattr(diagnostics, "_trace_files", forbidden)
    diagnostics._remove_orphan_attachments(tmp_path)


def test_latest_reference_stops_scan_and_keeps_attachment(tmp_path, monkeypatch):
    attachments = tmp_path / "attachments"
    attachments.mkdir()
    asset = attachments / ("a" * 64 + ".png")
    asset.write_bytes(b"fixture image")
    latest = tmp_path / "latest.json.gz"
    with gzip.open(latest, "wt") as handle:
        json.dump({"relative_path": "attachments/" + asset.name}, handle)
    missing_old = tmp_path / "must-not-open.json.gz"
    monkeypatch.setattr(diagnostics, "_trace_files", lambda _: [missing_old, latest])
    opened = []
    original = gzip.open

    def tracked(path, *args, **kwargs):
        opened.append(path)
        return original(path, *args, **kwargs)

    monkeypatch.setattr(diagnostics.gzip, "open", tracked)
    diagnostics._remove_orphan_attachments(tmp_path)
    assert opened == [latest]
    assert asset.exists()


def test_corrupt_trace_is_not_proof_attachment_is_orphaned(tmp_path, monkeypatch):
    attachments = tmp_path / "attachments"
    attachments.mkdir()
    asset = attachments / ("b" * 64 + ".png")
    asset.write_bytes(b"fixture image")
    trace = tmp_path / "corrupt.json.gz"
    trace.write_bytes(b"invalid gzip")
    monkeypatch.setattr(diagnostics, "_trace_files", lambda _: [trace])
    diagnostics._remove_orphan_attachments(tmp_path)
    assert asset.exists()
