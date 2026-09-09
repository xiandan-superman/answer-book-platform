# 测试问题修复记录（2026-09-09）

依据：项目外部测试摘要 `answer-book-platform-test-summary.md`

本次按推荐方案处理，重点修复共享契约和通用根因，没有按题号、年份、页码或单一模型添加特例。

## 修改摘要

| 问题 | 根因 | 修改内容 | 主要文件 |
|---|---|---|---|
| P1-01 Python 不兼容仍可创建任务 | 环境检查只在后台流水线启动后执行，API 创建入口没有前置门禁 | 出题任务创建前统一调用环境检查；Python 不满足要求时返回 409，明确当前版本、要求版本、未创建任务、未调用模型且不产生费用；保留 worker 内检查作为恢复/CLI 防线 | `app/server.py`、`web/app.js` |
| P1-02 `/api/practice/jobs` 集合接口 404 | 前端/监控可能访问集合路径，但服务端只有 POST 集合和 GET 单任务 | 增加 `GET /api/practice/jobs`，支持 `limit`、`include_history`，统一返回 `ok/jobs/count/limit/include_history` | `app/server.py` |
| P1-03 Windows LAN fallback 丢参数 | 兼容入口 fallback 调用普通启动脚本时没有传 LAN 启动参数 | fallback 统一转发 `--mode lan --autostart` | `start_platform_lan_windows.bat` |
| P1-04 最终验收与中间验收不一致 | `acceptance_report.json` 在 DOCX 构建成功时即写成 passed，最终验收阻断后未同步状态；图件计划/生成/交付状态字段也不够明确 | 最终验收阻断时同步更新 `acceptance_report.json` 为最终状态，并记录 delivery tier、最终问题和最终验收报告路径；图件交付摘要增加来源引用数、答案图件是否生成/交付等字段 | `app/pipeline_delivery.py`、`app/final_acceptance.py` |
| P2-01 任务身份容易混用 | 一个 `job_id` 同时承担逻辑任务和执行尝试含义 | 公共 `task_id` 固定为完整流程批次 ID；`run_id/job_id` 表示执行尝试，`history_id` 表示结果记录；前端操作显式使用资源 ID，复制与展示使用稳定任务 ID | `app/practice_jobs.py`、`app/task_read_model.py`、`app/server.py`、`web/app.js` |
| P2-02 无效 `image_orchestration` 被包装成内部错误 | 无效参数抛出普通 `ValueError`，公共错误层无法识别其契约性质 | 增加稳定的参数错误类型和公共错误码 `invalid_image_orchestration`，返回合法值和修复提示 | `app/image_orchestration.py` |
| P2-03 分值规则遗漏“每个/各/每空” | 分值推断规则只覆盖“每小题” | 统一支持“每个/每项/每题/各 X 分”；“每空 X 分”仅在抽取结果明确表示单空时推断，避免把每空分值错误赋给整道填空题 | `app/question_scores.py` |
| P2-04 混合 TeX/Unicode 公式预检失败 | 公式边界没有统一处理 Unicode 希腊字母和 Unicode 下标 | 在共享表达式规范化入口将 Unicode 希腊字母、上下标转换为稳定 LaTeX；例如 `C\\toα₀` 规范化为 `C\\to\\alpha_{0}` | `app/expression_normalization.py` |
| P3-01 Word 成功响应缺少 `ok` | Word 任务 payload 没有统一顶层成功字段 | Word 任务成功 payload 增加 `ok: true`，兼容统一客户端判断 | `app/word_format_tasks.py` |
| P3-03 环境提示自相矛盾 | 页面失败提示使用了通用“环境检查通过后即可继续”文案 | Python 不兼容时显示明确阻断原因和不会创建/调用模型的提示，并使用错误状态样式 | `web/app.js` |

## 重试与质量门

- 没有新增无依据的全局重试，也没有降低质量门。
- 现有按题/按阶段重试预算和瞬态错误分类保持不变；本次只补充单题状态、最终验收和交付契约的一致性。
- 缺图、空答案、上游服务失败仍会阻断正式交付，不会静默跳过。
- `job_id` 仍可用于旧客户端轮询；新增的 `task_id/run_id` 供恢复、监控和诊断区分逻辑任务与执行尝试。

## 回归验证

已执行针对性回归：

```text
57 passed
```

覆盖：

- image orchestration 参数错误契约；
- Python 不兼容创建前门禁；
- practice jobs 集合 GET 接口；
- 任务稳定身份字段；
- “每个 X 分”分值推断及“每空”边界；
- Unicode 希腊字母/下标公式规范化；
- 原有 practice job、Word、服务端 404 等相关测试。

## 未覆盖项

- 实体 Windows 双击 BAT、托盘、防火墙、真实 Word COM/PDF 导出、长路径和跨磁盘迁移；
- 真实上游模型过载条件下的单题恢复成本、Token、费用和耗时统计；
- 实体 Windows 专项仍未执行；浏览器只完成本地首页与初始化接口冒烟；
- 真实真题与真实模型的端到端重跑、可交付 DOCX 和 Word COM 验证尚未完成。

## 工作区说明

修改前工作区已有未提交改动。本记录只描述本次针对测试报告追加或调整的修复；未对其他已有 dirty 改动做重置、覆盖或归因。

## 独立环境审计报告复核追加项

针对项目外部审计报告 `answer-book-platform-审计报告.md`，在当前工作区复核后追加以下修复：

| 审计问题 | 处理结果 | 主要修改 |
|---|---|---|
| 纯 Markdown/TXT 教材索引成功但证据检索无页码 | 已修复 | 教材产品不再接受 Markdown/TXT；前后端教材扩展名白名单统一为 PDF、DOCX、JSON、ZIP，旧文件不再出现在教材选择列表 |
| 英文 `ValueError` 被归为内部错误 | 已修复 | 用户安全错误不再强制要求中文；英文参数错误返回 `invalid_request` |
| 上传错误中的 `.json` 被误判为模型 JSON 输出错误 | 已修复 | 模型输出错误改为基于上下文标记判断，不再对 `json` 子串宽泛匹配 |
| 创建任务时路径错误被 Key 错误掩盖 | 已修复 | `/api/tasks` 在解析模型配置前校验 `exam_path` 非空且文件存在 |
| Windows 保留字符导致教材/真题上传失败 | 已修复 | `library_files._safe_filename` 统一过滤 `< > : " / \\ | ? *` 及尾部点/空格 |
| 生图模型说明前后矛盾 | 已修复 | legacy 程序生图从合法请求集合和用户界面正式移除；新建及恢复任务统一为主模型自主生图闭环 |
| Markdown/TXT “可用于文字检索”造成能力误解 | 已修复 | 删除该产品定位，教材上传入口直接拒绝 Markdown/TXT |

本批次针对性回归：25 passed。上述测试与前一批测试存在部分重叠，未将通过数量简单相加作为全量结果。

## 用户确认后的设计收口

- P2-5：字符相似度、同义词、上下位概念和知识点覆盖等语义范围判断只产生警告，并要求用户在蓝图页人工确认；缺少计划项、ID 重复、非法题型/难度、必填字段缺失等确定性结构与身份错误仍硬阻断。
- P2-6：整套 Word 保持严格质量门；成功题目仍可按稳定题目 ID 选择导出，真题解析已保存的有效分题成果仍可下载版本化 unit ZIP。
- P2-7：`completed_with_issues` 只用于真实存在且通过文件完整性检查的待复核 DOCX；没有候选 Word 的短路交付和历史异常状态统一视为失败/需处理。
- 生图链路：用户不再选择程序绘图；`legacy_figure_pipeline` 请求会被明确拒绝，恢复记录迁移到 `main_model_tool_loop`，不会静默回退。

经代码复核，P2-6/P2-7 所需的共享契约已存在，未重复增加降级特例：

- P2-6：前端会过滤 `generation_status=failed` 的题目；后端通过稳定题目 ID 重建 `selected` 导出载荷。正式“全部题目”导出仍执行完整质量门，只有选定的成功题目可生成待复核/正式 Word。
- P2-7：答案解析流水线仅在最终验收允许候选交付且 DOCX 通过完整审计时原子发布 `answer_book_review_candidate.docx`；阻断级失败不会伪造或提供候选文件。`/api/tasks/{id}/files` 会按任务目录列出该候选文件，下载时沿用统一安全路径校验。

因此本轮保留正式整套 Word 的严格门禁，只调整语义误判、部分成果可见性和任务完成状态的真实性。

## 发布范围拆分

- 审查修复批次：稳定任务 ID、教材格式收口、蓝图语义审计、legacy 生图退役、部分交付/完成状态、错误分类和文件名兼容。
- Windows/MinerU 基础设施批次：运行时安装路径、检查点、SQLite、读取快照、字体 MIME、进程锁、UTF-8 门禁、LAN 启动参数。
- 两个批次在当前工作树中分别列明并独立验证；合并为 `0.9.50` 源码发布候选，提交、推送和公开发布结果在发布收尾后确认。

### 本轮验证记录

- `.venv/bin/python scripts/run_quality_gates.py --full`：全部通过，包含 PyCompile、版本一致性、公式、许可、项目完整性、受控 Ruff、Mypy、整库覆盖率门禁；pytest 结果为 2258 passed、17 deselected、12 warnings。
- `node --check web/app.js`、`git diff --check`：通过。
- 隔离数据目录启动本地服务，Playwright 覆盖上传、任务中心、失败恢复、暂停/继续和稳定 ID 界面：16 passed；未发起模型请求。
- Git 暂存索引候选包含 407 个白名单文件，反向验证 0 问题；解压后使用隔离数据目录启动，`/api/version` 返回 `0.9.50`且首页 HTTP 200。
- 仍未执行实体 Windows、真实模型和 Word COM；推送与正式发布状态在收尾后更新。
