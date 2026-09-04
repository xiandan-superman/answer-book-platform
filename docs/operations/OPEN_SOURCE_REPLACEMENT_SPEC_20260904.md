# 开源替换实施规格（给技术实现）

- 日期：2026-09-04
- 目标仓库：`answer-book-platform`（本地源码 ZIP 分发、单进程多线程）
- 本文用途：消除「规划 ↔ 实现」转译歧义。2026-09-04 已确认运行时升级为 Python 3.11；MinerU 不做 A/B、直接作为主解析链；LiteLLM 先仅用于灵算影子流量。

---

## 0. 不可违背的平台不变量

实现任何替换时必须保持下列不变量（摘自 `docs/ARCHITECTURE.md` / `docs/V4_FORMULA_CHAIN.md` / 现行代码）：

1. **程序主控，模型只做局部判断**
   模型不得决定任务流程、写文件系统、改脚本、发明教材页码、在普通正文写公式。
2. **三类工作流边界不合并**
   `exam_analysis` / `practice_by_question` / `practice_by_knowledge` 各自输入、阶段、控制语义保留；公共模块只承载横切能力。
3. **本地桌面分发约束**
   - 默认用户安装包依赖必须保持轻量（见 `requirements.txt` / `THIRD_PARTY_NOTICES.md`）。
   - 禁止把 Redis / Postgres 集群 / Temporal Server / Milvus 集群 / RAGFlow 整站 打进默认安装。
   - 重型依赖只能：可选安装、按需下载+SHA-256 校验、或仅开发机启用。
4. **v4 公式链路不可破坏**
   `LaTeX → latex2mathml → MathML → OMML`；公式不得进入普通 `<w:t>`；`AnswerFragmentV4` / `FormulaV4` 契约保留。
5. **确定性硬门禁 > 模型自证**
   分区守恒、父子守恒、公式数值一致、DOCX/渲染/最终验收，备用模型不得绕过。
6. **Word 工具 A/B 规则**（已落地 OPT-20260904-01）
   - A：Python/OMML（`python-docx` 等）
   - B：钉死 `iOfficeAI/OfficeCLI@1.0.147`（`app/officecli_word.py`）
   - 默认 B；失败不静默回退 A；升级 OfficeCLI 前必须重验 SHA-256 与真实文档回归。
7. **队列语义**
   `app/practice_queue.py` 使用 `SqliteHuey`；队列只存 job id，正文在任务记录里。

---

## 1. 总策略（给实现的一句话）

> **在现有模块边界内换“引擎”，不换“平台骨架”。**
> 允许：解析引擎、验算引擎、结构化重试、模型路由库、轻量向量召回、Word 渲图加深。
> 禁止：用 LangGraph/DeepSeek Harness/Codex/RAGFlow/Temporal/Django 重写任务平台。

### 1.1 替换分级

| 级别 | 含义 | 默认动作 |
|---|---|---|
| **A 立即做** | 质量杠杆大，且可贴现有边界 | 本迭代落地 |
| **B 条件做** | 需要适配层，不可整仓迁入 | 有 POC + 门禁后再合 |
| **C 不做/缓做** | 与本地分发或主控原则冲突 | 明确拒绝或无限期延后 |

### 1.2 统一适配模式（所有 A/B 项必须遵守）

```text
现有调用方
  -> 平台稳定接口（函数/协议，保持签名或提供兼容包装）
    -> Adapter（新建，薄）
      -> 开源引擎（可替换、可版本钉死）
    -> 失败时：显式错误 / 显式降级策略（写进配置，禁止静默换引擎）
```

要求：

- 新开源库 **不得** 被业务文件直接 `import` 散落；只允许 adapter 文件 import。
- 每个 adapter 必须有：版本钉死、健康检查入口（可挂 `app/environment.py`）、失败诊断字段、至少一条真实样本回归测试。
- 默认安装路径：能进 `requirements.txt` 的才算默认依赖；GPU/大模型权重类一律可选。

---

## 2. 按环节的可执行规格

### 2.1 【A】文件解析与 OCR

#### 现状（实现必须对齐）

- 教材包契约：`app/textbook_package.py`（`TextbookPackage.content_list` / `content_list_v2` / `images_root`）。
- MinerU 结果消费：`app/mineru_content.rows_from_mineru_content_list`。
- 索引入口：`app/textbook_index.build_textbook_index` / `build_textbook_index_for_files`（已 `import rows_from_mineru_content_list`）。
- 缺 `content_list.json` 时包校验报 `missing_content_list`。
- 真题结构抽取：`app/exam_extract.extract_exam_structure`（DOCX 为主，另有页图补偿）。

#### 决策

| 候选 | 决策 | 理由 |
|---|---|---|
| MinerU | **主引擎（钉死版本）** | 中文教材+公式；平台已按 content_list 契约消费 |
| PaddleOCR | **不作为并列产品** | 作为 MinerU pipeline 内部/回退引擎即可 |
| Docling | **B：仅补充** | 适合出生即数字的 Office；不作中文扫描教材主路径 |

#### 要实现的事

1. 明确「MinerU 运行时」边界：
   - 生成并校验 `*content_list.json` + 图片资源；
   - 平台侧只消费包，不把 MinerU Python API 散落进 `pipeline.py`。
2. 若当前是「外部先跑 MinerU 再导入 ZIP」：可保留；若要内嵌，新建例如 `app/adapters/mineru_runtime.py`，输出必须仍是现有 `TextbookPackage` 布局。
3. Docling 若做：另建 `app/adapters/docling_runtime.py`，**先转换成同一 content_list 行模型** 再进入 `textbook_index`，禁止为 Docling 分叉第二套索引格式。
4. 扫描件极差页：允许「单页 VLM OCR 补丁」挂在包校验之后，写入同一 row schema；不得另起检索语料格式。

#### 不要做的事

- 不要用 Docling/Marker 替换 `rows_from_mineru_content_list` 的下游契约。
- 不要把 RAGFlow 的解析管线引进来「顺便解析」。
- 不要在默认 `requirements.txt` 强装 GPU 版 OCR。

#### 验收

- 现有共享教材包（含 MinerU ZIP）零改动可建索引。
- 至少 1 份中文教材 PDF（含公式+表格+双栏）与 1 份扫描件：页码映射 `build_page_map` / `audit_page_map` 通过率不低于现网基线。
- `textbook_package` 校验：缺图引用、缺 content_list 仍按现有 code 报错。

#### 对接模块

`textbook_package.py`、`mineru_content.py`、`textbook_index.py`、`shared_textbook_library.py`、教材导入 API（`server.py` 教材相关路由）。

---

### 2.2 【B】教材分段、索引与检索

#### 现状

- 索引构建：`textbook_index.py`（切块、章节、页码、CSV）。
- 召回：`retrieval.py` 的 `CorpusTextScorer`（**bm25s**，失败回落 legacy）+ 规则分（公式、source_type、邻接上下文）。
- 尚无独立向量库依赖。

#### 决策

| 候选 | 决策 |
|---|---|
| Haystack / LlamaIndex | **只允许摘组件**（切块/retriever 思路），禁止整框架接管任务 |
| RAGFlow | **C 拒绝** 作为核心替换（完整应用栈，违背本地分发） |

#### 要实现的事

1. 保持 `build_candidates` / `candidates_for_question` / `EvidenceCandidate` 对外形状稳定。
2. 在 `CorpusTextScorer` 旁增加可选 **向量通道**（见 2.3），由 `build_candidates` 做 hybrid 融合；融合权重进配置，默认关闭向量时行为与现网一致。
3. 切块策略若改：必须保留 `page_idx` / 印刷页码 / `source_file` / `bbox` / `source_type` 字段；共享教材库 ZIP 兼容（见 `docs/SHARED_TEXTBOOK_LIBRARY.md`）。

#### 不要做的事

- 不要引入 Haystack/LlamaIndex 的 pipeline/agent 运行时。
- 不要让检索直接调用模型「总结后再检索」作为默认真路径（可作可选 enrich，不得成为硬依赖）。

#### 验收

- 关闭向量开关：现有检索回归测试全绿，分数排序与基线一致（允许浮点容差）。
- 打开向量：同一批真题「页码命中 Top-K」有可量化提升或持平，并记录 A/B 报告。

---

### 2.3 【B】向量存储与混合搜索

#### 决策

| 候选 | 决策 |
|---|---|
| FAISS | **A/B 优先（进程内）** 小中型教材 |
| Qdrant | **B：仅本地单机嵌入式/本机进程**；禁止默认要求独立服务 |
| Milvus | **C 拒绝**（运维过重） |

#### 要实现的事

1. 新建 `app/adapters/vector_store.py`，协议至少包含：`upsert(rows)`、`search(query, k)`、`delete_by_textbook(id)`。
2. 默认实现顺序：`faiss` 或 `sqlite-vec`（二选一，POC 后钉死）；索引文件落在用户数据 `cache/`，不进 git。
3. Embedding 模型：可调用现有 LLM provider 的 embedding 接口（经 2.4 网关），或本地小模型可选；必须可禁用。
4. Hybrid：`score = α * bm25_norm + β * vector_norm + rule_bonuses`；`rule_bonuses` 继续用现有 `formula_match_score` / `source_type_score_bonus`。

#### 不要做的事

- 不要把向量库当成唯一召回。
- 不要在客户端安装流程里拉起 Docker 版 Milvus/Qdrant。

#### 验收

- 无 embedding key / 无向量索引时自动降级 bm25s，任务可完成。
- 索引重建可复现；共享教材下载后的路径重定向仍有效。

---

### 2.4 【A】模型接入网关

#### 现状

- 自研多协议客户端：`app/llm_client.py`（`OpenAICompatibleClient` / `ResponsesAPIClient` / `AnthropicMessagesClient`，`create_llm_client`）。
- 能力登记：`config/model_capabilities.json` + `docs/MODEL_PROVIDER_INTEGRATION_STANDARD_DRAFT.md`。
- 已有重试、response_format 回退、用量相关逻辑。

#### 决策

| 候选 | 决策 |
|---|---|
| LiteLLM | **A：库模式（SDK）接入**，不强制独立 proxy 进程 |
| Portkey Gateway | **C/缓**：偏管控面；本地桌面默认不引入 |

#### 要实现的事

1. **不要删除** `LLMClientProtocol` / `create_llm_client` 外观。
2. 在内部增加 `app/adapters/litellm_router.py`（名称可调整），职责限：
   - provider 路由与 fallback；
   - 超时/有限重试/限流钩子；
   - token/费用字段归一到现有 telemetry。
3. 灵算等特殊 key 分离规则（README 已规定）必须保留。
4. `response_format` / tools 不能组合的现有约束保留（见 `llm_client` 注释）。
5. 若某 provider 走 LiteLLM 有回归，必须能按 provider 配置切回「直连现有 Client」。

#### 不要做的事

- 不要默认启动 `litellm --port` 常驻代理（除非明确做「实验室可选模式」）。
- 不要用 LiteLLM 替换 `model_capabilities.json` 能力登记。

#### 验收

- 现有 provider 连接测试、真题一小页、生题一小批：行为与费用字段可解释。
- 人为制造 429/超时：重试次数与现配置一致，检查点保留。

---

### 2.5 【C 为主 / 局部 B】Agent 与 Harness 编排

#### 现状

- 主控：`pipeline.py`、`task_runner.py`、`pipeline_checkpoints.py`、`pipeline_delivery.py`、`practice_worker.py`。
- 原则：程序决定阶段；模型只产出结构化局部结果。

#### 决策

| 候选 | 决策 |
|---|---|
| LangGraph | **B 仅限「生成-校验-修复」小环**，可选；不得接管任务生命周期 |
| DeepSeek Harness | **C 拒绝**（developer preview + Cordis/Node 运行时，与本 Python 平台不合） |
| OpenAI Codex | **C 拒绝**（产品/Agent，非嵌入库） |
| OpenAI Agents SDK | **C/极谨慎**：易破坏「程序主控」；默认不做 |

#### 若做 LangGraph 小环（唯一允许的编排实验）

- 作用域仅限例如：`content_quality_repair` / 单题答案回修 / 图修。
- 状态机节点必须调用现有确定性审计（`calculation_consistency`、`formula_audit`、`v4_schema`），**不得**用「模型说通过」结束。
- 任务级暂停/取消/checkpoint 仍由 `task_runner` + `pipeline_checkpoints` 负责。

#### 不要做的事

- 不要用任何 Harness「重写 pipeline」。
- 不要把 Codex/DeepSeek Harness 的技能安装脚本并进用户环境（与 OfficeCLI 不变量一致：不改 PATH/shell/skills）。

---

### 2.6 【A】结构化模型输出

#### 现状

- 契约：`app/v4_schema.py`、`practice_output_contracts.py`、`practice_batch_contracts.py`。
- 调用方已用 `model_validate`；`llm_client` 自带 JSON 模式与 schema 拼装、`StructuredOutputError` / `IncompleteOutputError`。

#### 决策

| 候选 | 决策 |
|---|---|
| Pydantic | **保留，契约真相源** |
| Instructor | **A：在 adapter 内增强「校验失败→回喂重试」** |
| BAML | **C/缓**：引入 DSL 与生成客户端，分发成本高 |

#### 要实现的事

1. 新建薄封装（例如 `app/adapters/structured_completion.py`）：
   输入：`client`、messages、`type[BaseModel]`、max_retries；
   输出：已 validate 的 model 或平台标准错误。
2. 优先接入高失败面：答案 fragment、生题批次、蓝图 JSON。
3. 截断/不完整输出：继续映射到现有 `IncompleteOutputError` 语义，便于 checkpoint 重跑。
4. **不**让 Instructor 绕过 `practice_batch_contracts` 的序号/占位规则。

#### 验收

- 故意破坏 JSON：有界重试后失败，错误分类不被吞成「网络错误」。
- v4 公式泄漏审计仍失败（不得因结构化包装变松）。

---

### 2.7 【A】数学计算与公式正确性校验

#### 现状

- 数值/合同：`app/calculation_consistency.py`
  关键入口含 `formula_numeric_consistency_issues`、`calculation_draft_consistency_issues`、`calculation_contract_issues`、`reconcile_calculation_reference_structure`。
- 当前是自研解析/容差（`_eval_simple_expression` 等），**未**依赖 SymPy。
- 泄漏审计：`formula_audit.py`（防正文公式），职责不同，勿合并。

#### 决策

| 候选 | 决策 |
|---|---|
| SymPy | **A：作为符号/数值后端** |
| latex2sympy2_extended | **A：LaTeX → SymPy** |
| Math-Verify | **A：判等/验证辅助** |

#### 要实现的事

1. 在 `calculation_consistency` 内用 adapter 替换「能符号化的子集」；保留现有函数签名，避免全库改调用点。
2. 单位换算、百分数等价、多结果 multiset 匹配等现有规则必须保留测试。
3. 解不出 / 转换失败：返回 issue 字符串（现有风格），不得抛未捕获异常中断整任务，除非配置为硬失败。
4. 与 `academic_expressions` / OMML 链路解耦：验算失败不等于排版失败。

#### 不要做的事

- 不要用 Math-Verify 替换 `formula_audit`（泄漏审计）。
- 不要在验算里调用远程模型。

#### 验收

- 现有 calculation 相关单测全绿。
- 新增：至少 10 道真实计算题（含单位、分数、科学计数）对照人工答案。
- 回归：非计算题路径零行为变化。

---

### 2.8 【B】教学图示与程序作图

#### 现状

- Schema 驱动：`figures.py`（大量 `draw_*`）+ `figure_schema_*` + `capabilities/builtin/*`。
- 代码作图沙箱：`drawing_code.py`（`validate_drawing_code` / `run_drawing_code`，matplotlib）。
- 依赖已有 matplotlib。

#### 决策

| 候选 | 决策 |
|---|---|
| Matplotlib | **保留主力** |
| Graphviz | **B：结构/流程图渲染后端可选** |
| Mermaid | **B：需稳定 PNG 渲染链路后再用** |
| Manim | **C 拒绝默认**（过重，不适合批量题图） |

#### 要实现的事

- 新后端必须挂 `capabilities` registry（schema + renderer + validator），禁止在 `figures.py` 无限加学科分支（架构已规定）。
- `drawing_code` 白名单依赖不变；若加 graphviz，必须可选安装。

#### 不要做的事

- 不要用 Manim 替换 matplotlib 批量出图。
- 不要让模型直接输出「不可审计」的自由绘图文件而不经 schema/program_check。

---

### 2.9 【保持加深，不替换栈】Word 文档生成与编辑

#### 现状

- A：`python-docx` / OMML 工具链。
- B：`app/officecli_word.py`（`build_answer_book_with_officecli`、`build_practice_with_officecli`）。
- 入口：`docx_v4.py`、`practice_export.py` 按 `selected_word_tool_variant` 分流。
- vendor 存在 `docx-mcp-server`，**不是**主生产路径。

#### 决策

| 候选 | 决策 |
|---|---|
| OfficeCLI | **保持 B 默认，继续加深** |
| AIOffice | **C 不引入第二套并行引擎**（除非 OfficeCLI 无解缺陷且单独立项） |
| docx-mcp-server | **C 不作生产主路径**（MCP 面向 Agent；本平台程序主控） |
| python-docx | **A 路径保留** |

#### 要实现的事（允许）

- 加强 B：`create → atomic batch --stop-on-error → save → validate → close`。
- 渲染验收环（见 2.10）与 OfficeCLI HTML/PNG 能力结合时，仍须过现有 DOCX/公式/最终验收，不得「只看图通过」。

#### 不要做的事

- 不要「A 生成 + OfficeCLI 只校验」冒充 B（OPT 不变量）。
- 不要升级 OfficeCLI 版本而不更新 SHA-256 与 `THIRD_PARTY_NOTICES.md`。

---

### 2.10 【保持】Word / PDF / 页面渲染

#### 现状

- `pdf_render.py`：主路径 `pypdfium2`，可选 `pdftoppm` 回退。
- 交付编排：`pipeline_delivery.py`。

#### 决策

| 候选 | 决策 |
|---|---|
| pypdfium2 / PDFium | **保持** |
| LibreOffice | **B：仅 docx→pdf 兜底转换（可选）** |
| MuPDF | **C 除非 pypdfium2 有不可修缺陷** |
| OfficeCLI | 参与 Word 侧渲图可以，但不替代 PDF 页渲染主库 |

#### 验收门禁（不可放松）

空白页、裁切、重叠、字体、公式、图片、页数、内容一致性——继续走现有 audit；新渲染器只是后端。

---

### 2.11 【C】长任务队列与断点恢复

#### 现状

- 出题队列：`practice_queue.py`（`SqliteHuey`）。
- 检查点：`pipeline_checkpoints.py`。
- 任务存储：`task_store.py`。

#### 决策

| 候选 | 决策 |
|---|---|
| Huey | **保持** |
| Temporal / Prefect / Celery / DBOS | **C 拒绝本阶段替换** |

#### 允许做的增强（自制，不开源换栈）

- 加强阶段 checkpoint 字段与「失败占位不得复用」规则（已有方向）。
- 文档化恢复矩阵（取消/重启/断网）并补测试。

---

### 2.12 【B】模型调用与任务观测

#### 现状

- `pipeline_telemetry.py`、`model_usage_report.py`、`runtime_monitor.py`。

#### 决策

| 候选 | 决策 |
|---|---|
| OpenTelemetry | **B：埋点标准，先内部** |
| Langfuse / Phoenix | **B：仅开发/内网可选**，不进默认用户包 |

#### 要实现的事

- span 对齐：provider 调用、工具调用、pipeline stage、修复轮次。
- 导出可接现有 `pipeline_status.json` / 用量报告，避免两套真相。

---

### 2.13 【B】RAG 与生成质量评测

#### 决策

| 候选 | 决策 |
|---|---|
| Ragas / DeepEval / Promptfoo | **B：离线门禁与 A/B**，不进热路径 |

#### 强制评测维度（实现清单）

1. 检索：Top-K 页码命中、教材名命中。
2. 答案：v4 校验通过率、公式泄漏率、计算一致率。
3. 生题：批次缺题/串题率（对照 `practice_batch_contracts`）。
4. 交付：DOCX validate、渲染审计、最终验收通过率。
5. 成本：模型调用次数、修复轮次、token。

评测不得改为「模型给自己打分即通过」；能确定性测的必须确定性测。

---

### 2.14 【C】本地服务与 API 层

#### 现状

- `app/server.py` HTTP 边界 + `http_errors.py`。
- 架构明确：稳定则低优先级替换。

#### 决策

| 候选 | 决策 |
|---|---|
| 现有 server | **保持** |
| FastAPI / Litestar | 仅当单独立项「HTTP 层重写」 |
| Django | **C 拒绝** |

---

## 3. 推荐实施波次（给排期）

### Wave 0（准备，0.5–1 天）

- 冻结基线：`scripts/run_quality_gates.py --full` + 选定 3 套真实教材/真题金样。
- 建 `app/adapters/` 包与「禁止业务直接 import 新依赖」的 lint/约定。
- 输出本文到团队：实现不得扩到 C 项。

### Wave 1（质量上限，优先）

1. MinerU 运行时边界钉死 + 包契约测试加强
2. SymPy / latex2sympy2_extended / Math-Verify 接入 `calculation_consistency`
3. structured_completion（Instructor 或等价）接入高失败 JSON 面
4. LiteLLM 库模式挂到 `create_llm_client` 内部（可回切）

### Wave 2（检索）

5. bm25s + 向量 hybrid（FAISS 或 sqlite-vec），默认关
6. 离线评测脚本（Ragas/DeepEval 任选其可用部分 + 自有确定性指标）

### Wave 3（交付体验）

7. OfficeCLI B 渲图验收加深（仍过现有门禁）
8. LibreOffice 可选 pdf 兜底
9. OTel 埋点（可选导出）

### 明确不排期

RAGFlow 核心化、Milvus、Temporal/Prefect/Celery 换 Huey、DeepSeek Harness/Codex/Agents SDK 接管、Django、Manim 默认化、BAML、Portkey 默认网关。

---

## 4. 接口级「完成定义」（Definition of Done）

每个 Wave 项合并前必须同时满足：

1. **兼容**：三类工作流各至少 1 条金样任务跑通。
2. **可关**：新引擎有配置开关；关闭后与 Wave 0 基线一致。
3. **可诊**：失败时 `task` 诊断 / telemetry 能指出引擎名、版本、阶段、是否降级。
4. **可回滚**：文档记录回滚开关；Word 类遵守 A/B 不静默回退规则。
5. **许可证**：更新 `THIRD_PARTY_NOTICES.md` 与 constraints。
6. **测试**：单元 + 至少 1 个真实文件集成；不得只 mock 开源引擎。

---

## 5. 转译对照表（防误解）

| 口语说法 | 正确理解 | 错误理解 |
|---|---|---|
| 「用 MinerU 替换解析」 | 钉死生成 `content_list` 的引擎，下游契约不变 | 重写 `textbook_index`/`retrieval` |
| 「上向量库」 | 可选 hybrid 通道 | 去掉 bm25s 或上 Milvus 集群 |
| 「上 LangGraph」 | 可选单题修复小环 | 替换 `pipeline`/`task_runner` |
| 「上 LiteLLM」 | SDK 统一路由 | 强制用户多跑一个 gateway 进程 |
| 「上 OfficeCLI」 | 已是 B 默认，加深 | 再引入 AIOffice 双引擎 |
| 「上 Temporal」 | 本阶段不做 | 觉得「更专业」就换掉 Huey |
| 「开源替换自制」 | 换引擎、留骨架 | 用开源项目替换整个 answer-book-platform |

---

## 6. 建议的代码落点（实现清单）

| 新/改文件（建议） | 职责 |
|---|---|
| `app/adapters/__init__.py` | 适配层包 |
| `app/adapters/mineru_runtime.py` | 可选：调用/校验 MinerU 产出包 |
| `app/adapters/structured_completion.py` | Pydantic(+Instructor) 结构化重试 |
| `app/adapters/litellm_router.py` | 路由/fallback/用量归一 |
| `app/adapters/sympy_verify.py` | LaTeX↔SymPy、判等 |
| `app/adapters/vector_store.py` | 向量 upsert/search |
| `app/calculation_consistency.py` | 调用 sympy adapter，保留 API |
| `app/llm_client.py` | 内部接 router，保留 `create_llm_client` |
| `app/retrieval.py` | hybrid 融合，默认关向量 |
| `requirements.txt` / `constraints-*.txt` / `THIRD_PARTY_NOTICES.md` | 依赖与许可证 |
| `tests/adapters/...` | 引擎契约测试 |
| `docs/operations/OPTIMIZATION_LOG.md` | 每项 OPT 记录 |

---

## 7. 给技术负责人的确认题（实现前只需答一次）

1. MinerU：继续「外部产包导入」还是要「平台内嵌运行时」？
2. 向量：默认目标是「单机教材 <N 页」还是「多人共享超大库」？（后者才考虑 Qdrant 进程）
3. LiteLLM：是否允许某 provider 长期直连、仅对其余走 LiteLLM？
4. Wave 1 四项是否同一发布列车，还是可拆 PR？

未回答时的默认假设（实现可直接用）：

1. 先钉契约与质检，内嵌运行时列为可选。
2. 按单机教材设计（FAISS/sqlite-vec）。
3. 允许按 provider 直连回退。
4. 可拆 PR，但同一 Wave 内共用金样基线。

---

## 8. 文档状态

- 本文是实施规格，不是探索笔记。
- 若实现中发现必须违反第 0 节不变量，先停写代码，升级决策后再改本文版本号。
