from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from app.practice_store import _compact_request

ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / "web" / "upload-file-selection.js"


def run_javascript(body: str) -> dict:
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js is required for the browser helper contract tests")
    script = f"""
const upload = require({json.dumps(str(MODULE))});
const result = (() => {{
{body}
}})();
process.stdout.write(JSON.stringify(result));
"""
    completed = subprocess.run(
        [node, "-e", script],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(completed.stdout)


def test_prevalidation_is_atomic_and_independent_of_file_order() -> None:
    result = run_javascript(
        """
const existing = [{name: '已选.txt', type: 'text/plain', size: 3, sha256: 'a'.repeat(64)}];
const small = {name: '正常.txt', type: 'text/plain', size: 2};
const oversized = {name: '超限.txt', type: 'text/plain', size: 12 * 1024 * 1024 + 1};
const forward = upload.validateSelection(existing, [small, oversized]);
const reverse = upload.validateSelection(existing, [oversized, small]);
return {existing, forward, reverse};
"""
    )

    assert result["existing"] == [
        {"name": "已选.txt", "type": "text/plain", "size": 3, "sha256": "a" * 64}
    ]
    assert not result["forward"]["ok"]
    assert not result["reverse"]["ok"]
    assert {item["name"] for item in result["forward"]["rejected"]} == {"正常.txt", "超限.txt"}
    assert {item["name"] for item in result["reverse"]["rejected"]} == {"正常.txt", "超限.txt"}
    assert any("同批次" in reason for reason in result["forward"]["rejected"][0]["reasons"])


def test_prevalidation_reports_empty_bad_type_count_and_total_reasons() -> None:
    result = run_javascript(
        """
const invalid = upload.validateSelection([], [
  {name: '空文件.txt', type: 'text/plain', size: 0},
  {name: '脚本.exe', type: 'application/octet-stream', size: 1},
  {name: '伪装.txt', type: 'image/png', size: 1}
]);
const existing = Array.from({length: 11}, (_, index) => ({name: `${index}.txt`, type: 'text/plain', size: 3 * 1024 * 1024}));
const aggregate = upload.validateSelection(existing, [
  {name: '新增一.txt', type: 'text/plain', size: 2 * 1024 * 1024},
  {name: '新增二.txt', type: 'text/plain', size: 2 * 1024 * 1024}
]);
return {invalid, aggregate};
"""
    )

    reasons = {item["name"]: "；".join(item["reasons"]) for item in result["invalid"]["rejected"]}
    assert "文件内容为空" in reasons["空文件.txt"]
    assert "不支持此文件类型" in reasons["脚本.exe"]
    assert "不支持此文件类型" in reasons["伪装.txt"]
    aggregate_reasons = "\n".join(
        reason
        for item in result["aggregate"]["rejected"]
        for reason in item["reasons"]
    )
    assert "文件数量将超过 12 个" in aggregate_reasons
    assert "文件总大小将超过 36 MB" in aggregate_reasons


def test_content_hash_deduplication_preserves_order_and_same_name_versions() -> None:
    result = run_javascript(
        """
const first = {upload_item_id: 'upload_first', name: '同名.txt', type: 'text/plain', size: 3, sha256: 'a'.repeat(64)};
const duplicate = {upload_item_id: 'upload_duplicate', name: '副本.txt', type: 'text/plain', size: 3, sha256: 'a'.repeat(64)};
const secondVersion = {upload_item_id: 'upload_second', name: '同名.txt', type: 'text/plain', size: 5, sha256: 'b'.repeat(64)};
const last = {upload_item_id: 'upload_last', name: '最后.txt', type: 'text/plain', size: 4, sha256: 'c'.repeat(64)};
const merged = upload.mergePreparedFiles([first], [duplicate, secondVersion, last]);
return {
  ids: merged.files.map((file) => file.upload_item_id),
  duplicates: merged.duplicates.map((item) => [item.file.upload_item_id, item.duplicateOf.upload_item_id]),
  labels: merged.files.map((file) => upload.displayName(file, merged.files))
};
"""
    )

    assert result["ids"] == ["upload_first", "upload_second", "upload_last"]
    assert result["duplicates"] == [["upload_duplicate", "upload_first"]]
    assert result["labels"] == ["同名.txt · 同名版本 1/2", "同名.txt · 同名版本 2/2", "最后.txt"]


def test_deleted_content_can_be_added_again_then_is_deduplicated() -> None:
    result = run_javascript(
        """
const removedHash = 'd'.repeat(64);
const retained = {upload_item_id: 'upload_retained', name: '保留.txt', sha256: 'e'.repeat(64)};
const readded = {upload_item_id: 'upload_readded', name: '重新加入.txt', sha256: removedHash};
const once = upload.mergePreparedFiles([retained], [readded]);
const repeated = upload.mergePreparedFiles(once.files, [
  {upload_item_id: 'upload_repeat', name: '再次选择.txt', sha256: removedHash}
]);
return {
  once: once.files.map((file) => file.upload_item_id),
  repeated: repeated.files.map((file) => file.upload_item_id),
  duplicateOf: repeated.duplicates[0].duplicateOf.upload_item_id
};
"""
    )

    assert result["once"] == ["upload_retained", "upload_readded"]
    assert result["repeated"] == ["upload_retained", "upload_readded"]
    assert result["duplicateOf"] == "upload_readded"


def test_legacy_file_without_hash_is_deduplicated_by_its_data_url() -> None:
    result = run_javascript(
        """
const dataUrl = 'data:text/plain;base64,bGVnYWN5';
const legacy = {upload_item_id: 'upload_legacy', name: '旧草稿.txt', type: 'text/plain', size: 6, data_url: dataUrl};
const selectedAgain = {upload_item_id: 'upload_new', name: '再次选择.txt', type: 'text/plain', size: 6, data_url: dataUrl, sha256: 'f'.repeat(64)};
const merged = upload.mergePreparedFiles([legacy], [selectedAgain]);
return {
  ids: merged.files.map((file) => file.upload_item_id),
  duplicates: merged.duplicates.map((item) => item.file.upload_item_id)
};
"""
    )

    assert result["ids"] == ["upload_legacy"]
    assert result["duplicates"] == ["upload_new"]


def test_history_compaction_preserves_optional_upload_identity() -> None:
    compacted = _compact_request({
        "source_files": [{
            "upload_item_id": "upload_fixed",
            "sha256": "a" * 64,
            "name": "来源.txt",
            "type": "text/plain",
            "size": 2,
            "data_url": "data:text/plain;base64,b2s=",
        }]
    })

    assert compacted["source_files"] == [{
        "upload_item_id": "upload_fixed",
        "sha256": "a" * 64,
        "name": "来源.txt",
        "type": "text/plain",
        "size": 2,
        "data_url": "data:text/plain;base64,b2s=",
    }]
