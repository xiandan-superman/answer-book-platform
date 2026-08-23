from __future__ import annotations

from app.task_diagnostics import _compact_issue, _error_label, _recommendations


def test_figure_acceptance_issue_keeps_real_question_id() -> None:
    issue = _compact_issue(
        "figure_visual_qa: qa_s01_01_01 / figure_01 failed: figure image missing",
        default_stage="final_acceptance",
        severity="issue",
    )

    assert issue["question_id"] == "qa_s01_01_01"
    assert issue["code"] == "figure_visual_qa"


def test_final_acceptance_figure_failure_gets_figure_specific_recovery() -> None:
    issues = [{"message": "figure_visual_qa: qa_01 / fig_01 failed: figure image missing"}]

    recommendations = _recommendations("final_acceptance", "最终验收未通过", issues)

    assert any("配图阶段" in item for item in recommendations)
    assert all("文档工具链正常" not in item for item in recommendations)


def test_hybrid_header_encoding_failure_is_explained_without_exposing_python_error() -> None:
    error = _error_label("'latin-1' codec can't encode characters in position 0-3")

    assert error == "上传任务标识编码失败；请更新客户端与混合云服务端后重试。"
    recommendations = _recommendations("uploading", error, [])
    assert any("同步更新客户端和混合云服务端" in item for item in recommendations)
    assert any("无需修改中文任务名" in item for item in recommendations)
