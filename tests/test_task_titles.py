from app.task_read_model import _practice_history_run
from app.task_titles import build_display_task_title, friendly_material_title, short_model_label


def test_task_name_structure_uses_dynamic_kind_model_and_content() -> None:
    assert build_display_task_title(
        "按题出题",
        "高分子物理",
        model="gpt-5.6-terra",
    ) == "按题出题 · Terra · 高分子物理"
    assert build_display_task_title(
        "知识点出题",
        "晶体结构与中间相",
        model="gemini-3.6-flash",
    ) == "知识点出题 · Gemini · 晶体结构与中间相"


def test_model_label_is_concise_and_truthful() -> None:
    assert short_model_label("gpt-5.6-sol") == "Sol"
    assert short_model_label("deepseek-chat") == "DeepSeek"
    assert short_model_label("sensenova-6.8-flash-lite") == "SenseNova"
    assert short_model_label("hy3") == "Hy3"
    assert short_model_label("mimo-v2.5") == "MiMo"
    assert short_model_label("minimax/minimax-m3:free") == "MiniMax"
    assert short_model_label("z-ai/glm-5.2:free") == "GLM"
    assert short_model_label("stealth/ox-alpha") == "Ox Alpha"
    assert build_display_task_title("格式审查", "论文", model_label="规则引擎") == "格式审查 · 规则引擎 · 论文"


def test_filename_content_is_cleaned_without_rewriting_explicit_titles() -> None:
    assert friendly_material_title("跨年组合_高分子物理_按题生题.docx") == "跨年组合 · 高分子物理"


def test_history_title_cleanup_does_not_require_source_file_metadata() -> None:
    record = {
        "history_id": "practice_12345678",
        "task_kind": "practice",
        "title": "跨年组合_高分子物理_按题生题",
        "status": "completed",
        "request": {"provider": "lingsuan_openai", "model": "gpt-5.6-sol"},
    }

    assert _practice_history_run(record)["display_title"] == "按题出题 · Sol · 跨年组合 · 高分子物理"


def test_history_user_rename_is_preserved_when_source_basename_survives() -> None:
    record = {
        "history_id": "practice_12345678",
        "task_kind": "practice",
        "title": "我的_自定义任务名",
        "status": "completed",
        "request": {
            "provider": "lingsuan_google",
            "model": "gemini-3.6-flash",
            "source_file_names": ["跨年组合_高分子物理_按题生题.docx"],
        },
    }

    assert _practice_history_run(record)["display_title"] == "按题出题 · Gemini · 我的_自定义任务名"
