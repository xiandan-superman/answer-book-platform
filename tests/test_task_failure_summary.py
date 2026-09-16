import json

from app import task_failure_summary as summary
from app.task_read_model import build_exam_run


def test_failed_exam_explains_lost_image_in_public_card(tmp_path, monkeypatch):
    monkeypatch.setattr(summary, 'TASKS_DIR', tmp_path)
    stage = tmp_path / 'example' / 'stage_outputs'
    stage.mkdir(parents=True)
    payloads = {
        'final_acceptance_report.json': {'figure_delivery_summary': {'items': [
            {'question_id': 'q1', 'answer_required': True, 'issues': ['missing']}]}, 'outputs': {'docx_exists': True}},
        'structured_exam.json': {'questions': [{'question_id': 'q1', 'section': '三、简答题', 'number': '4'}]},
        'answer_fragments.before_academic_expression_model_repair.json': {'fragments': [
            {'question_id': 'q1', 'blocks': [{'segments': [{'type': 'image_ref', 'role': 'answer_generated_figure'}]}]}]},
        'answer_fragments.json': {'fragments': [{'question_id': 'q1', 'blocks': []}]},
    }
    for name, data in payloads.items():
        (stage / name).write_text(json.dumps(data))
    row = {'task_id': 'example', 'status': 'failed', 'current_stage': 'final_acceptance', 'error': 'Final acceptance audit failed'}
    result = build_exam_run(row)
    assert '第4题' in result['error']
    assert '修复后插图引用丢失' in result['error']
    assert result['error_presentation']['responsibility'] == 'platform_defect'
    assert '旧程序' in result['error_presentation']['retry_hint']
    assert result['status'] == 'failed'
    assert not result['capabilities']['download']
    assert summary.exam_failure_summary({**row, 'status': 'running'}) is None
    (stage / 'answer_fragments.before_academic_expression_model_repair.json').unlink()
    assert summary.exam_failure_summary(row)['kind'] == 'answer_figure_missing'


def test_progress_error_is_local_and_has_no_private_path():
    result = summary.exam_failure_summary({'status': 'failed', 'error': '[WinError 5] C:/private/answer_generation_progress.json'})
    assert result['responsibility'] == 'local_environment'
    assert 'private' not in str(result)
    assert 'API' not in str(result)


def test_missing_reports_keep_existing_presentation(tmp_path, monkeypatch):
    monkeypatch.setattr(summary, 'TASKS_DIR', tmp_path)
    assert summary.exam_failure_summary({'task_id': 'empty', 'status': 'failed', 'current_stage': 'final_acceptance'}) is None
