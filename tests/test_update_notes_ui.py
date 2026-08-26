from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / "web" / "update-notes.js"


def run_javascript(body: str) -> dict:
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js is required for the update-note UI contract tests")
    script = f"""
const updateNotes = require({json.dumps(str(MODULE))});
const result = (() => {{
{body}
}})();
process.stdout.write(JSON.stringify(result));
"""
    completed = subprocess.run(
        [node, "-e", script], cwd=ROOT, check=True, capture_output=True, text=True
    )
    return json.loads(completed.stdout)


def test_release_notes_are_reduced_to_user_facing_sections() -> None:
    result = run_javascript(
        r"""
const notes = `## [0.9.24]
### 模型接入与兼容性
- 修复 DeepSeek Responses 地址和 store 参数，qwen-vl-ocr 限制 4096 tokens。
### 生题稳定性与重试
- 每道题拥有 4 次请求机会，熔断后进行恢复探测。
### Word 下载与公式修复
- 修复下载到空 Word 文件以及公式包装问题。
### 验证与升级说明
- 全量回归通过 1588 项。`;
return {
  items: updateNotes.summarizeReleaseNotes(notes),
  message: updateNotes.formatReleaseSummary(notes)
};
"""
    )

    assert result["items"] == [
        "优化模型调用兼容性",
        "提升生题等待与重试稳定性",
        "修复 Word 下载和公式显示问题",
    ]
    assert "DeepSeek" not in result["message"]
    assert "4096" not in result["message"]
    assert "1588" not in result["message"]


def test_update_dialog_always_keeps_close_actions_inside_viewport() -> None:
    app = (ROOT / "web" / "app.js").read_text(encoding="utf-8")
    css = (ROOT / "web" / "platform-theme.css").read_text(encoding="utf-8")
    index = (ROOT / "web" / "index.html").read_text(encoding="utf-8")

    assert 'cancelText: "关闭"' in app
    assert 'confirmText: status.action === "download_installer" ? "下载更新" : "立即更新"' in app
    assert "if (event.target === elements.overlay) finishPlatformDialog(false);" in app
    assert "max-height: min(720px, calc(100dvh - 48px));" in css
    assert "overflow-y: auto;" in css
    assert '/update-notes.js?v=20260826-update-dialog-1' in index
