from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.adapters.math_verifier import verify_math_equivalence
from app.adapters.structured_completion import structured_completion
from app.llm_client import LLMResult
from app.model_output_contracts import PracticeGenerationOutput


class _FakeStructuredClient:
    def __init__(self) -> None:
        self.calls: list[list[dict]] = []

    def chat_json(self, messages, **_kwargs):
        self.calls.append(messages)
        content = '{"wrong": true}' if len(self.calls) == 1 else '{"exercises": [{"question": "1+1"}]}'
        return LLMResult(provider="fake", model="fake-model", content=content, raw={})


def test_instructor_reasks_with_pydantic_validation_error() -> None:
    client = _FakeStructuredClient()

    result = structured_completion(
        client,
        [{"role": "user", "content": "return practice JSON"}],
        response_model=PracticeGenerationOutput,
        model="fake-model",
        max_validation_retries=1,
    )

    assert result.exercises == [{"question": "1+1"}]
    assert len(client.calls) == 2
    assert "validation" in json.dumps(client.calls[1], ensure_ascii=False).lower()


def test_math_stack_verifies_symbolic_equivalence_in_isolated_worker() -> None:
    equivalent = verify_math_equivalence(r"$x+x$", r"$2x$")
    different = verify_math_equivalence(r"$x+x$", r"$3x$")

    assert equivalent.available and equivalent.equivalent is True
    assert different.available and different.equivalent is False


def test_mineru_runtime_discovers_native_content_list(tmp_path: Path, monkeypatch) -> None:
    from app.adapters import mineru_runtime

    source = tmp_path / "exam.pdf"
    source.write_bytes(b"%PDF")
    runner = tmp_path / "fake_mineru.py"
    runner.write_text(
        "import json, pathlib, sys\n"
        "out=pathlib.Path(sys.argv[sys.argv.index('-o')+1])/'exam'\n"
        "out.mkdir(parents=True, exist_ok=True)\n"
        "(out/'exam_content_list.json').write_text(json.dumps([{'type':'text','page_idx':0,'text':'第一题'}]), encoding='utf-8')\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(mineru_runtime, "CACHE_DIR", tmp_path / "cache")
    monkeypatch.setattr(mineru_runtime, "mineru_command", lambda: [sys.executable, str(runner)])

    package = mineru_runtime.parse_document(source)

    assert package.content_list is not None
    assert json.loads(package.content_list.read_text(encoding="utf-8"))[0]["text"] == "第一题"
    assert json.loads(package.audit_path.read_text(encoding="utf-8"))["mineru_version"] == "3.4.5"


def test_mineru_rejects_incompatible_python_before_running_pip(tmp_path: Path, monkeypatch) -> None:
    from app.adapters import mineru_runtime

    monkeypatch.setattr(mineru_runtime, "runtime_python_supported", lambda: False)
    monkeypatch.setattr(mineru_runtime.sys, "version_info", (3, 14, 6))
    monkeypatch.setattr(mineru_runtime.subprocess, "run", lambda *_args, **_kwargs: pytest.fail("pip must not run"))

    with pytest.raises(mineru_runtime.MinerURuntimeError, match="必须由 Python 3.11 运行"):
        mineru_runtime._install_runtime(tmp_path / "mineru" / "Scripts" / "python.exe")

def test_mineru_managed_runtime_path_is_python_311_specific(tmp_path: Path, monkeypatch) -> None:
    from app.adapters import mineru_runtime

    monkeypatch.setattr(mineru_runtime, "DATA_ROOT", tmp_path)

    assert "mineru-3.4.5-pipeline-py311" in str(mineru_runtime._managed_python())


def test_mineru_requirement_installs_pipeline_extra() -> None:
    from app.adapters import mineru_runtime

    requirements = (mineru_runtime.PROJECT_ROOT / "requirements-mineru.txt").read_text(encoding="utf-8")

    assert "mineru[pipeline]==3.4.5" in requirements


def test_mineru_invocation_pins_pipeline_backend(tmp_path: Path, monkeypatch) -> None:
    from app.adapters import mineru_runtime

    source = tmp_path / "exam.docx"
    source.write_bytes(b"fake-docx")
    captured: list[str] = []

    def fake_run(command, **_kwargs):
        captured.extend(command)
        output = Path(command[command.index("-o") + 1]) / "exam"
        output.mkdir(parents=True, exist_ok=True)
        (output / "exam_content_list.json").write_text("[]", encoding="utf-8")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(mineru_runtime, "CACHE_DIR", tmp_path / "cache")
    monkeypatch.setattr(mineru_runtime, "mineru_command", lambda: ["mineru"])
    monkeypatch.setattr(mineru_runtime.subprocess, "run", fake_run)

    mineru_runtime.parse_document(source)

    assert captured[-2:] == ["-b", "pipeline"]


def test_litellm_shadow_records_comparison_without_exposing_content(tmp_path: Path, monkeypatch) -> None:
    import litellm

    from app.adapters import litellm_shadow

    class _Response:
        choices = [SimpleNamespace(message=SimpleNamespace(content='{"ok": true}'))]

        def model_dump(self):
            return {"usage": {"prompt_tokens": 2, "completion_tokens": 3, "total_tokens": 5}}

    monkeypatch.setattr(litellm, "completion", lambda **_kwargs: _Response())
    monkeypatch.setattr(litellm_shadow, "LOGS_DIR", tmp_path)
    config = SimpleNamespace(name="lingsuan_google", base_url="https://example.invalid/v1", api_key="secret")

    litellm_shadow._run(config, [{"role": "user", "content": "private prompt"}], "model-a", 100, '{"ok": true}')

    raw_log = (tmp_path / "litellm_shadow.jsonl").read_text(encoding="utf-8")
    row = json.loads(raw_log)
    assert row["status"] == "succeeded"
    assert row["primary_json_valid"] is True and row["shadow_json_valid"] is True
    assert "private prompt" not in raw_log and "secret" not in raw_log
