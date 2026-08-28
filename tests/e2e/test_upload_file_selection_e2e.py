from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from urllib.parse import urlparse

import pytest

pytestmark = pytest.mark.e2e


def _file(name: str, content: bytes, mime_type: str = "text/plain") -> dict:
    return {"name": name, "mimeType": mime_type, "buffer": content}


def _wait_for_count(page, expression: str, expected: int) -> None:
    page.wait_for_function(f"() => {expression}.length === {expected}")


def _wait_for_draft(page, mode: str) -> None:
    page.evaluate(
        """async (mode) => {
          await uploadFileReadChains[mode === 'knowledge' ? 'knowledge' : 'practice'];
          await practiceWorkspaceWriteChains[mode];
        }""",
        mode,
    )


def test_legacy_draft_explicit_restore_finishes_before_new_selection_and_keeps_identity() -> None:
    base_url = os.getenv("ANSWER_BOOK_E2E_URL", "").strip()
    if not base_url:
        pytest.skip("set ANSWER_BOOK_E2E_URL to an already running local platform")

    playwright = pytest.importorskip("playwright.sync_api")
    with playwright.sync_playwright() as runtime:
        launch_options = {} if Path(runtime.chromium.executable_path).is_file() else {"channel": "chrome"}
        browser = runtime.chromium.launch(headless=True, **launch_options)
        page = browser.new_page(viewport={"width": 1440, "height": 1000})
        page.goto(base_url, wait_until="networkidle")
        page.evaluate(
            """async () => practiceWorkspaceDatabaseOperation('exam', 'put', {
              mode: 'exam', schema: 'practice_workspace_draft.v1', stage: 'input',
              practice_batch_id: 'legacy_batch', updated_at: 1,
              input: {
                question_text: '旧版四字段草稿', count: '5', difficulty: '基础到进阶',
                question_types: [], focus: '', include_source_content_in_generation: true,
                source_files: [{
                  name: '旧草稿.txt', type: 'text/plain', size: 6,
                  data_url: 'data:text/plain;base64,bGVnYWN5'
                }]
              }
            })"""
        )

        page.evaluate("openPracticeEntry('exam')")
        page.locator("#practiceWorkspaceDraftNotice:not(.hidden)").wait_for()
        page.locator("#practiceWorkspaceDraftRestorePrevious").click()
        _wait_for_count(page, "practiceSourceFiles", 1)
        result = page.evaluate(
            """async () => {
              return readPracticeFiles([new File(['legacy'], '重新选择.txt', {type: 'text/plain'})]);
            }"""
        )
        assert len(result["files"]) == 1
        assert len(result["duplicates"]) == 1
        restored = page.evaluate("structuredClone(practiceSourceFiles[0])")
        assert restored["name"] == "旧草稿.txt"
        assert restored["upload_item_id"].startswith("upload_")
        assert "重复文件未再次加入" in page.locator("#practiceError").inner_text()

        _wait_for_draft(page, "exam")
        page.reload(wait_until="networkidle")
        page.evaluate("openPracticeEntry('exam')")
        page.locator("#practiceWorkspaceDraftNotice:not(.hidden)").wait_for()
        page.locator("#practiceWorkspaceDraftRestorePrevious").click()
        _wait_for_count(page, "practiceSourceFiles", 1)
        assert page.evaluate("practiceSourceFiles[0].upload_item_id") == restored["upload_item_id"]
        browser.close()


def test_atomic_selection_deduplication_restore_and_final_request_body() -> None:
    base_url = os.getenv("ANSWER_BOOK_E2E_URL", "").strip()
    if not base_url:
        pytest.skip("set ANSWER_BOOK_E2E_URL to an already running local platform")

    playwright = pytest.importorskip("playwright.sync_api")
    with playwright.sync_playwright() as runtime:
        launch_options = {} if Path(runtime.chromium.executable_path).is_file() else {"channel": "chrome"}
        browser = runtime.chromium.launch(headless=True, **launch_options)
        context = browser.new_context(viewport={"width": 1440, "height": 1000})
        captured_requests: list[dict] = []

        def route_request(route) -> None:
            request = route.request
            parsed = urlparse(request.url)
            if parsed.hostname not in {"127.0.0.1", "localhost"}:
                route.abort()
                return
            if parsed.path == "/api/practice/jobs" and request.method == "POST":
                captured_requests.append(json.loads(request.post_data or "{}"))
                route.fulfill(
                    status=200,
                    content_type="application/json",
                    body=json.dumps({"job_id": f"fixture_{len(captured_requests)}"}),
                )
                return
            if parsed.path.startswith("/api/practice/jobs/fixture_"):
                route.fulfill(
                    status=200,
                    content_type="application/json",
                    body=json.dumps({
                        "job_id": parsed.path.rsplit("/", 1)[-1],
                        "status": "failed",
                        "error": "确定性夹具已停止任务，未调用模型。",
                        "error_presentation": {"message": "确定性夹具已停止任务，未调用模型。"},
                    }),
                )
                return
            route.continue_()

        context.route("**/*", route_request)
        page = context.new_page()
        page.goto(base_url, wait_until="networkidle")

        # 按题生题：先保留一个合法文件，再验证两种顺序都不会部分加入。
        page.evaluate("openPracticeEntry('exam')")
        page.locator("#practiceFile").set_input_files(_file("同名.txt", b"first"))
        _wait_for_count(page, "practiceSourceFiles", 1)
        initial = page.evaluate("structuredClone(practiceSourceFiles[0])")
        assert initial["sha256"] == hashlib.sha256(b"first").hexdigest()
        assert initial["upload_item_id"].startswith("upload_")

        for reverse in (False, True):
            error = page.evaluate(
                """async (reverse) => {
                  const small = new File(['accepted-only-if-whole-batch-passes'], '本批正常.txt', {type: 'text/plain'});
                  const oversized = new File([new Uint8Array(12 * 1024 * 1024 + 1)], '本批超限.txt', {type: 'text/plain'});
                  try {
                    await readPracticeFiles(reverse ? [oversized, small] : [small, oversized]);
                    return '';
                  } catch (caught) {
                    showUploadFeedback('practice', String(caught).replace(/^Error:\\s*/, ''));
                    return String(caught);
                  }
                }""",
                reverse,
            )
            assert "本次选择未加入" in error
            assert "本批正常.txt" in error
            assert "本批超限.txt" in error
            assert page.evaluate("practiceSourceFiles.length") == 1
            assert page.evaluate("practiceSourceFiles[0].upload_item_id") == initial["upload_item_id"]
            assert "本批正常.txt" not in page.locator("#practiceFilePreview").inner_text()

        read_error = page.evaluate(
            """async () => {
              const originalPrepare = prepareUploadFile;
              try {
                prepareUploadFile = async (file) => {
                  if (file.name === '模拟读取失败.txt') throw new Error('模拟读取失败.txt 读取失败。');
                  return originalPrepare(file);
                };
                await readPracticeFiles([
                  new File(['must-not-commit'], '读取成功但不应加入.txt', {type: 'text/plain'}),
                  new File(['failure'], '模拟读取失败.txt', {type: 'text/plain'})
                ]);
                return '';
              } catch (caught) {
                showUploadFeedback('practice', String(caught).replace(/^Error:\\s*/, ''));
                return String(caught);
              } finally {
                prepareUploadFile = originalPrepare;
              }
            }"""
        )
        assert "读取成功但不应加入.txt" in read_error
        assert "模拟读取失败.txt 读取失败" in read_error
        assert page.evaluate("practiceSourceFiles.length") == 1

        # 内容相同不再加入；同名异内容保留并可视化为版本。
        page.locator("#practiceFile").set_input_files(_file("完全重复但改名.txt", b"first"))
        page.locator("#practiceError").wait_for(state="visible")
        assert "重复文件未再次加入" in page.locator("#practiceError").inner_text()
        assert page.evaluate("practiceSourceFiles.length") == 1

        page.locator("#practiceFile").set_input_files(_file("同名.txt", b"second-version"))
        _wait_for_count(page, "practiceSourceFiles", 2)
        preview_text = page.locator("#practiceFilePreview").inner_text()
        assert "同名版本 1/2" in preview_text
        assert "同名版本 2/2" in preview_text
        same_name_items = page.evaluate("structuredClone(practiceSourceFiles)")
        assert [item["name"] for item in same_name_items] == ["同名.txt", "同名.txt"]
        assert len({item["sha256"] for item in same_name_items}) == 2
        assert len({item["upload_item_id"] for item in same_name_items}) == 2

        # 同一选择中重复项只跳过重复内容，合法新内容保持用户顺序。
        page.locator("#practiceFile").set_input_files([
            _file("再次重复.txt", b"first"),
            _file("顺序三.txt", b"third"),
            _file("顺序四.txt", b"fourth"),
        ])
        _wait_for_count(page, "practiceSourceFiles", 4)
        assert page.evaluate("practiceSourceFiles.map((file) => file.name)") == [
            "同名.txt", "同名.txt", "顺序三.txt", "顺序四.txt"
        ]

        # 删除后可重新加入；SHA 相同，但新的上传项 ID 不复用已删除身份。
        page.locator("#practiceFilePreview [data-practice-file-remove]").first.click()
        _wait_for_count(page, "practiceSourceFiles", 3)
        page.locator("#practiceFile").set_input_files(_file("同名.txt", b"first"))
        _wait_for_count(page, "practiceSourceFiles", 4)
        readded = page.evaluate("structuredClone(practiceSourceFiles.at(-1))")
        assert readded["sha256"] == initial["sha256"]
        assert readded["upload_item_id"] != initial["upload_item_id"]

        # 增删结果、顺序、ID 和摘要经刷新恢复；恢复后仍可去重。
        before_reload = page.evaluate("structuredClone(practiceSourceFiles)")
        _wait_for_draft(page, "exam")
        page.reload(wait_until="networkidle")
        page.evaluate("openPracticeEntry('exam')")
        page.locator("#practiceWorkspaceDraftNotice:not(.hidden)").wait_for()
        assert page.evaluate("practiceSourceFiles.length") == 0
        page.locator("#practiceWorkspaceDraftRestorePrevious").click()
        _wait_for_count(page, "practiceSourceFiles", len(before_reload))
        assert page.evaluate("structuredClone(practiceSourceFiles)") == before_reload
        page.locator("#practiceFile").set_input_files(_file("刷新后重复.txt", b"first"))
        page.locator("#practiceError").wait_for(state="visible")
        assert "重复文件未再次加入" in page.locator("#practiceError").inner_text()
        assert page.evaluate("practiceSourceFiles.length") == 4

        # 读取期间提交按钮禁用；读取完成后最终请求必须含刚选择的文件。
        page.evaluate(
            """() => {
              globalThis.__uploadPrepareOriginal = prepareUploadFile;
              prepareUploadFile = async (file) => {
                if (file.name === '延迟读取.txt') await new Promise((resolve) => setTimeout(resolve, 250));
                return globalThis.__uploadPrepareOriginal(file);
              };
            }"""
        )
        page.locator("#practiceFile").set_input_files(_file("延迟读取.txt", b"slow-content"))
        assert page.locator("#practiceGenerateBtn").is_disabled()
        _wait_for_count(page, "practiceSourceFiles", 5)
        page.evaluate("() => { prepareUploadFile = globalThis.__uploadPrepareOriginal; return true; }")
        assert not page.locator("#practiceGenerateBtn").is_disabled()

        # 失败后补文字提交，请求只能含页面可见文件，不得夹带被拒绝项。
        practice_before_submit = page.evaluate("structuredClone(practiceSourceFiles)")
        page.locator("#practiceQuestionText").fill("确定性题目材料")
        page.locator("#practiceGenerateBtn").click()
        page.locator("#practiceError:not(.hidden)").wait_for()
        assert "确定性夹具已停止任务" in page.locator("#practiceError").inner_text()
        assert captured_requests
        practice_payload = captured_requests[-1]["payload"]
        assert practice_payload["source_mode"] == "exam"
        assert practice_payload["source_files"] == practice_before_submit
        assert all(item["name"] != "本批正常.txt" for item in practice_payload["source_files"])
        assert practice_payload["practice_batch_id"]

        # 知识点生题入口执行相同的原子失败、重复、同名版本、删除重加和恢复契约。
        page.evaluate("openKnowledgeEntry()")
        page.locator("#knowledgeFileInput").set_input_files(_file("知识同名.txt", b"knowledge-one"))
        _wait_for_count(page, "knowledgeSourceFiles", 1)
        knowledge_initial = page.evaluate("structuredClone(knowledgeSourceFiles[0])")
        page.locator("#knowledgeFileInput").set_input_files([
            _file("知识重复.txt", b"knowledge-one"),
            _file("知识同名.txt", b"knowledge-two"),
        ])
        _wait_for_count(page, "knowledgeSourceFiles", 2)
        assert "重复文件未再次加入" in page.locator("#knowledgeError").inner_text()
        knowledge_preview = page.locator("#knowledgeFilePreview").inner_text()
        assert "同名版本 1/2" in knowledge_preview
        assert "同名版本 2/2" in knowledge_preview

        page.locator("#knowledgeFileInput").set_input_files([
            _file("不应暗中加入.txt", b"hidden"),
            _file("非法.bin", b"bad", "application/octet-stream"),
        ])
        page.locator("#knowledgeError").wait_for(state="visible")
        assert "本次选择未加入" in page.locator("#knowledgeError").inner_text()
        assert "不应暗中加入.txt" in page.locator("#knowledgeError").inner_text()
        assert "非法.bin" in page.locator("#knowledgeError").inner_text()
        assert page.evaluate("knowledgeSourceFiles.length") == 2

        page.locator("#knowledgeFilePreview [data-knowledge-file-remove]").first.click()
        _wait_for_count(page, "knowledgeSourceFiles", 1)
        page.locator("#knowledgeFileInput").set_input_files(_file("知识同名.txt", b"knowledge-one"))
        _wait_for_count(page, "knowledgeSourceFiles", 2)
        knowledge_readded = page.evaluate("structuredClone(knowledgeSourceFiles.at(-1))")
        assert knowledge_readded["sha256"] == knowledge_initial["sha256"]
        assert knowledge_readded["upload_item_id"] != knowledge_initial["upload_item_id"]

        knowledge_before_reload = page.evaluate("structuredClone(knowledgeSourceFiles)")
        _wait_for_draft(page, "knowledge")
        page.reload(wait_until="networkidle")
        page.evaluate("openKnowledgeEntry()")
        page.locator("#knowledgeWorkspaceDraftNotice:not(.hidden)").wait_for()
        assert page.evaluate("knowledgeSourceFiles.length") == 0
        page.locator("#knowledgeWorkspaceDraftRestorePrevious").click()
        _wait_for_count(page, "knowledgeSourceFiles", len(knowledge_before_reload))
        assert page.evaluate("structuredClone(knowledgeSourceFiles)") == knowledge_before_reload

        page.evaluate(
            """() => {
              globalThis.__uploadPrepareOriginal = prepareUploadFile;
              prepareUploadFile = async (file) => {
                if (file.name === '知识延迟读取.txt') await new Promise((resolve) => setTimeout(resolve, 250));
                return globalThis.__uploadPrepareOriginal(file);
              };
            }"""
        )
        page.locator("#knowledgeFileInput").set_input_files(_file("知识延迟读取.txt", b"knowledge-slow"))
        assert page.locator("#knowledgePlanBtn").is_disabled()
        _wait_for_count(page, "knowledgeSourceFiles", 3)
        page.evaluate("() => { prepareUploadFile = globalThis.__uploadPrepareOriginal; return true; }")
        assert not page.locator("#knowledgePlanBtn").is_disabled()
        knowledge_before_submit = page.evaluate("structuredClone(knowledgeSourceFiles)")

        page.locator("#knowledgeTitleInput").fill("确定性知识点")
        page.locator("#knowledgePlanBtn").click()
        page.locator("#knowledgeError:not(.hidden)").wait_for()
        assert "确定性夹具已停止任务" in page.locator("#knowledgeError").inner_text()
        assert len(captured_requests) >= 2
        knowledge_payload = captured_requests[-1]["payload"]
        assert knowledge_payload["source_mode"] == "knowledge"
        assert knowledge_payload["source_files"] == knowledge_before_submit
        assert all(item["name"] != "不应暗中加入.txt" for item in knowledge_payload["source_files"])
        assert knowledge_payload["practice_batch_id"]

        browser.close()
