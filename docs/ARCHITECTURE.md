# Platform Architecture

## 统一任务平台边界（V9）

平台现在使用统一任务读模型，但不把业务规则强行合并。三类工作流分别保留自己的输入、阶段和控制语义：

| 工作流 | 业务目标 | 独有约束 | 允许的控制 |
|---|---|---|---|
| `exam_analysis` 真题解析 | 从真实试卷生成教材证据链、解析文档并完成最终验收 | 题号/教材页码/图件/Word 验收必须逐层闭环 | 排队可开始，执行中可暂停/取消，失败可从检查点重跑 |
| `practice_by_question` 按题出题 | 围绕选中的原题生成平行题或变式题 | 必须保留原题来源与变式关系，范围确认和蓝图审查不可跳过 | 按后台阶段推进；不虚构真题的暂停/开始语义 |
| `practice_by_knowledge` 知识点出题 | 从知识材料拆分知识单元并生成练习 | 不得把教材材料当成真题；知识单元选择和题量策略独立校验 | 与按题出题共享生题生命周期，但保留自己的来源和质量规则 |

公共模块只承载稳定的横切能力：任务 ID/状态、读模型、错误分类、质量摘要、文件引用、API 序列化。业务差异通过 `WorkflowType` adapter 和能力契约表达，修改公共模块后由三类 adapter 的场景测试共同兜底。

### 生命周期与阶段

```text
queued -> running -> needs_input -> running -> completed
                         |             |
                         v             v
                      paused        failed
                         |             |
                         +--> cancelled / retry-from-checkpoint
```

`completed_with_issues` 表示生成物存在但最终验收或局部题目仍有问题，不能等同于 `completed`。真题解析在确定性交付门禁通过、仅剩图片科学/语义风险时，可下载附带风险报告的交付包，但不能宣称“最终验收通过”；生题局部失败仍禁止整套导出。每个任务的 `capabilities` 由工作流和状态共同计算，前端只根据能力显示“开始、暂停、取消、重跑、查看结果、下载”等按钮。

### 真实场景审计矩阵

测试和人工验收必须按以下维度组合，而不是只测首页或单一成功样本：

| 场景 | 真题解析 | 按题出题 | 知识点出题 | 必查结果 |
|---|---:|---:|---:|---|
| 排队/开始 | ✓ | ✓ | ✓ | 开始按钮与重复启动 409 |
| 执行中轮询 | ✓ | ✓ | ✓ | 当前阶段、进度、后台步骤 |
| 人工确认 | 题目/教材/页码 | 范围/蓝图 | 知识单元/蓝图 | `needs_input` 与继续按钮 |
| Provider 超时/524 | ✓ | ✓ | ✓ | 错误分类、保留检查点 |
| JSON/策略不匹配 | 结构/计划 | 蓝图/题型 | 蓝图/知识单元 | 不得误判为同一错误 |
| 图件或最终验收失败 | ✓ | 适用时 | 适用时 | 具体题号、修复建议、禁止错误导出 |
| 取消/恢复/重启中断 | ✓ | ✓ | ✓ | 状态转换和重跑入口 |
| 完成与交付 | 验收报告/文件 | 自动检查/有界 AI 复检 | 自动检查/有界 AI 复检 | 结果页按钮、导出门禁、风险提示 |

场景测试应同时验证 API 聚合结果、任务详情、执行页和结果页；仅验证 `/api/tasks` 或首页不算完成。

## 文件治理

- `practice_history/` 只保存规范的用户历史记录；`*_repaired.json`、`*_semantic_candidate.json` 等派生候选不得进入任务中心。
- `practice_jobs/`、`tasks/`、`outputs/`、`logs/`、`cache/`、`runtime/` 是运行时目录，禁止提交；清理前先按 `history_id`、checksum 和引用关系建立清单。
- 教材 ZIP、共享教材和渲染产物不直接删除；后续迁移采用内容寻址（checksum）+ 元数据索引 + 双读兼容，确认无引用后再归档。
- 平台提交不包含桌面 App 冻结路径（见 `AGENTS.md`），也不把临时截图、浏览器状态或本地密钥加入版本库。

## 分层

```text
Local Web UI
  -> Platform HTTP Server
    -> Task Store
    -> Environment Check
    -> LLM Provider Adapter
    -> v4 Structured Output Validator
    -> Formula Leak Auditor
    -> Formula Converter
    -> Pipeline Runner
```

### 当前模块边界

- `app/server.py`：HTTP 路由和协议边界；公开错误由 `app/http_errors.py` 统一脱敏。
- `app/task_runner.py`：后台任务启动、运行边界和重启续跑。
- `app/task_store.py`：原子 JSON 持久化、并发锁和中断任务恢复。
- `app/pipeline_telemetry.py`：统一记录流水线阶段时长、`pipeline_status.json`、任务健康进度和心跳；业务主流程只保留阶段顺序与决策。
- `app/pipeline_checkpoints.py`：集中管理上游、答案和图形路由检查点的版本兼容性，以及失败回修的事务回滚；失败占位答案不得成为后续复用输入。
- `app/pipeline_delivery.py`：在内容质量通过后，统一编排 Word 生成/回修、图片尺寸审计、审查文档、PDF/PNG 渲染、Shadow 报告与最终验收；文件名和阶段事件保持兼容。
- `app/practice_queue.py`：Huey + SQLite 持久出题队列；队列仅保存任务 ID，任务正文继续由任务记录管理。
- `app/practice_worker.py`：与 HTTP 层解耦的出题任务执行入口。
- `app/practice_batch_contracts.py`：统一约束生题模型批次返回的临时序号、重复/越界/null 处理、缺失题占位和蓝图顺序恢复，防止少题、多题或乱序回包导致串题。
- `app/practice_result_assembly.py`：在题目内容质量检查后，统一装配来源分组、同蓝图变式分组、批次错误、运行诊断和模型元数据；不参与题目内容生成，也不改变题号、答案或导出格式。
- `web/platform-api.js`：浏览器 API 调用、网络重试和结构化错误。
- `web/task-contract-ui.js`：任务状态、阶段文案和待处理筛选规则。
- `web/icon-compat.js`：本地 Lucide 图标兼容层；MathJax 也从 `web/vendor/` 离线加载。

大文件仍按业务域渐进拆分；新增跨页面能力不得继续直接堆入 `server.py` 或 `app.js`。

### 综合题与文档路由

题目的顶层 `question_type` 决定 Word 文档骨架；小问和 requirement 的题型只决定局部能力，例如是否需要绘图、计算契约或选项分析。文档层不得因某个小问含“作图题”就把整道计算综合题改走作图题模板，否则会丢失解题步骤与公式。

```text
顶层题型 -> 文档区块和排版顺序
小问题型 -> 局部能力（图、公式、证据、选项）
```

自动修复也必须分层：`app/content_quality_repair.py` 只修复能从已验证结构确定性推导的内容缺项；`app/fragment_repair.py` 只修复公式占位符、行内表达等 DOCX 输入结构。两者不得互相代用，每次修复后必须回到对应审计器复检。DOCX 链路固定为“初次审计 → 本地确定性修复 → 复审 → 仅对白名单剩余问题做一次受限模型回修 → 复审”；本地修复器自身异常必须写入回修报告，不得隐式重试或直接跳过剩余问题。

## 模型职责

模型只能做局部内容判断：

- 判断题目考点。
- 根据候选教材证据组织解析。
- 输出严格 JSON。
- 给出需要插入的公式对象请求。

模型不得：

- 决定任务流程。
- 写入文件系统。
- 修改程序脚本。
- 在普通正文中直接写公式。
- 自行发明教材文件或页码。

## 无人值守质量治理

质量闭环不依赖人工逐条复核，也不允许规则因命中次数多就自动升级为硬门禁。`app/capabilities/quality_governance.py` 统一声明规则的证据类型、动作上限、复检方式和降级策略：

- 结构、文件存在性等可机器证明的后置条件可以阻断交付。
- 可修复问题必须先修复再用确定性后置条件复检，不接受“模型说已修好”作为证明。
- 符号风格、专业表达、图片语义等启发式或模型判断最高只能触发有界修复、告警或降级，不能在无人值守模式下单独阻断。
- 未登记规则默认只观察，必须通过治理目录才能获得更高动作权限。

`app/capabilities/quality_budget.py` 统一限定单任务内容回修题数、图片回修轮数和每图候选数。视觉 QA 使用内容指纹复用未变化的结果；跨任务统计为增量旁路，不增加主链路时延。

### 学术表达中间层

`app/capabilities/academic_expressions.py` 把公式、方程、数值单位、向量、矩阵、反应式和学科符号统一投影为 `AcademicExpression`。中间层保留原文，只生成用于比较、缓存和审计的规范化值，不在审计阶段改写答案。

识别规则由 `CapabilityManifest.expression_rules` 注册：核心包只保留跨学科、低争议结构；晶面/晶向等规则属于材料能力包，且只在相应题目上下文中启用。新的数学、物理、化学或其他学科可以按相同契约注册，无需修改主流程。

每个真题任务在内容回修完成后生成 `academic_expression_audit.json`。当前版本完全本地运行，`remote_model_calls=0`；报告作为 Shadow 质量输入和交付追溯文件，不增加远程请求时延。

`app/capabilities/selective_review.py` 只接收中间层和内容审计明确标出的高风险候选，不再全量遍历题目调用模型。单任务默认最多 8 个候选、1 个批次，完整输入指纹未变时复用结果。模型不可用、超时、缺项或返回非法决策时自动降级为告警，不等待人工，模型意见也不得单独成为硬阻断。

### 表达渲染契约

`app/capabilities/expression_rendering.py` 把识别结果继续投影为 `ExpressionRenderPlan`：保留原文与规范化形式，声明表达类别、行内/独行呈现、全公式斜体排版、规则来源和 Word 预检结果。

普通正文中的显式表达先由 `app/capabilities/text_expression_rendering.py` 生成 `TextExpressionRenderPlan`，再进入同一 OMML 渲染入口。该规则只允许有确定性转换器的跨学科表达晋级，当前覆盖方程、热力学标准态、化学式、完整反应式和电极表示式。学科包中的晶向等规则可参与审计，但在没有学科渲染契约时不会被通用层自动改写。

真题 Word 与生题 Word 只能调用这些公共入口，不得各自实现正文扫描、LaTeX/OMML 风格或反应式转换规则。凡进入 Word 原生公式对象的内容，所有可见公式 run 必须统一使用斜体，包括数学变量、化学式、反应式、单位、状态符号以及原始 `\mathrm` 内容。同一规范化表达的 OMML 预检使用进程内有界缓存，不增加模型请求。

只有 LaTeX 结构不闭合和 OMML 无法生成这类可机器证明的问题阻断 Word 交付；符号风格、专业表达与语义判断仍按启发式/模型判断管理，不得借排版预检升级为主观硬门禁。

## 程序职责

程序负责：

- 任务 ID 生成。
- 任务隔离。
- 教材索引。
- 检索候选。
- JSON Schema 校验。
- 公式字段校验。
- Word OMML 生成。
- DOCX/PDF/PNG 输出。
- 渲染审计。
- 验收报告。

## 公式链路

Word 输入先按 `document.xml` 的正文顺序遍历段落和表格。`app/omml_input.py` 使用 Microsoft `omml2mathml.xsl` 将 OMML 保留为结构化 MathML；转换器不可用时保留可见字符并生成显式降级诊断，不允许静默丢失公式结构。Word 内嵌图片在抽取文本中保留位置锚点，锚点序号与传给视觉模型的参考图列表一致。

正式执行默认使用：

```text
LaTeX -> MathML -> Microsoft mathml2omml.xsl -> Word OMML
```

依赖项：

- `latex2mathml`
- `lxml`
- Microsoft Word 安装包内的 `mathml2omml.xsl`

如果上述链路不完整，正式流水线在环境阶段失败。内置最小转换器只作为排查环境时的临时降级，不作为正式生产默认路径。

## Provider Adapter

当前支持：

- `deepseek`
- `ark`
- `bailian`
- `yunwu`

已验证的 DeepSeek、百炼、云雾文本与视觉调用优先走 Responses API；生图继续使用各供应商原生接口。火山方舟保留 OpenAI-compatible Chat Completions。

后续新增其他兼容接口时，只新增 provider 配置或 adapter，不改业务流程。

真题解析把模型职责分为知识点/证据、结构化答案、高风险正确性复核、读图和生图。高风险正确性路由只处理计算题、作图综合题的证据复核，以及已被确定性校验拒绝的单题回修；不会无条件重写普通题或整份答案。未单独配置时复用结构化答案模型，显式配置后才使用独立服务商和模型。无论使用哪一路由，分区加和、父子守恒、公式结果一致性和 Word 交付门禁保持相同，备用模型不能绕过本地硬校验。

## 多学科能力架构

平台是通用的多学科内容处理系统，当前材料学数据只是测试样本。通用核心不得根据测试语料固化学科边界，也不得为未知专业图选择一个语义不匹配的默认图形。

能力扩展遵循以下边界：

```text
Question
  -> per-question capability resolution
    -> core capability / optional discipline capability
      -> schema + renderer + validator
        -> quality findings
          -> central quality policy (ignore / warn / block)
```

- `app/capabilities/contracts.py`：能力清单、本地匹配证据和可选策略 hook 的稳定契约。
- `app/capabilities/registry.py`：能力、schema 注册和冲突检查；学科包不能覆盖其他能力的 schema。通用流程只按 hook 名收集规则或执行转换，不识别“相图”、“晶胞”等学科词汇。
- `app/capabilities/rendering.py`：统一 renderer 调度，业务流程不得继续增加按图形类型展开的分支链。
- `app/capabilities/expression_rendering.py`：统一学术表达到 Word OMML 的渲染计划、排版策略和确定性预检。
- `app/capabilities/text_expression_rendering.py`：统一普通正文中显式方程、反应式、化学式和电极表示式的扫描与渲染计划；学科特有符号未注册 renderer 时只审计、不自动晋级。
- `app/capabilities/quality.py`：validator 只生成带证据和置信度的 finding；是否阻断由统一策略决定。
- `app/capabilities/builtin/core_figures.py`：学科无关的曲线、比较图和流程图。
- `app/capabilities/builtin/materials.py`：材料、冶金、高分子与陶瓷能力包；除 schema/识别规则外，还拥有视觉理解、绘图质量、答案生成和视觉 QA 过滤策略，不代表平台学科边界。
- `app/figure_schema_registry.py`：迁移期兼容门面，新代码不得再向其中增加专业 schema。

新增学科能力时必须同时提供 manifest、schema/renderer 契约测试、冲突测试、策略隔离测试、失败降级方式和性能预算。策略隔离测试必须证明无关题目不会收到该学科的输出字段、提示词规则或审查过滤。重型或含原生扩展的依赖保持可选并按需加载；能力失败不得破坏无关题目的生成。

### 质量策略迁移

现有审计器继续保留原始 `ok/issues/warnings` 输出，避免在迁移期间改变生产门禁。`app/capabilities/audit_adapters.py` 将这些结果只读转换为统一 `QualityFinding`，并由 `app/capabilities/shadow_quality.py` 生成 `quality_shadow_report.json`。

Shadow 报告固定满足：

- `mode=shadow`、`enforced=false`，不得改变任务状态或导出权限。
- 每条 finding 包含稳定代码、来源、严重度、置信度、对象和原始证据。
- 策略输出 `block/warn/ignore` 仅表示假设启用后的结果，分别统计为 `would_block_count`、`would_warn_count` 和 `ignored_count`。
- 新规则先通过真实任务统计误报和漏报，再单独决定是否进入正式策略；不得因为 validator 报错而自动成为全局硬门禁。

### 跨任务质量指标

`app/capabilities/quality_metrics.py` 只读扫描各任务的 Shadow 报告，生成跨任务规则指标；缓存保存在 `cache/quality_metrics_cache.json`，不得写回或修改历史任务。可以通过 `python3 scripts/audit_quality_metrics.py` 或只读接口 `GET /api/quality/metrics` 查看。

聚合结果至少包含影响任务数、影响对象数、出现次数、重复次数、模拟动作分布、平均置信度和人工允许/拒绝次数。人工“允许继续”表示风险接受，不等同于误报；人工“拒绝”也不自动证明规则正确。

规则状态只能是数据不足或“可进入人工晋级评审”。晋级门槛用于阻止小样本规则被过早启用，并不授权程序自动改变策略；`automatic_promotion_enabled` 必须保持为 `false`。

### 文档展示投影

`app/document_presentation.py` 在不修改标准答案片段的前提下，把题目级“解析 / 图示 / 解题步骤”只读投影回原始小问顺序，使图片、说明和计算过程紧邻其所属小问。投影只有在所有正文、步骤和图片都能无歧义归属时才启用；旧任务缺少归属元数据或存在游离内容时，DOCX 生成器整体降级到原有分块布局，不允许静默漏图或丢失正文。

原有答案生成规范属于产品兼容契约，不得在重构中隐式改变。`app/document_contracts.py` 集中锁定页面尺寸、页边距、字体字号、行距、答案缩进、序号形式及各题型的基本区块顺序；DOCX 生成器引用同一套样式常量，回归测试同时验证契约声明和实际 Word XML。任何有意变更都必须按文档格式迁移处理，不能作为局部“美化”顺手修改。

`app/docx_audit.py` 会在识别到 V4 答案册总标题后自动启用当前兼容契约审计；页面、页边距、正文中西文字体、字号、行距、段前段后、页眉页脚和页码域发生漂移时直接阻断交付。`docs/真题答案与讲义格式标准.md` 是从历史成品样本抽取的目标标准；它与当前 V4 长期行为有差异时，两者必须并列保留，只有显式格式版本迁移才能改变当前兼容契约。

生题 Word 使用独立的 `app/practice_document_contracts.py`：保留 Letter 页面、“第 N 题”标题、A/B 选项悬挂缩进、题目卷/答案解析卷分离、参考答案和编号解析步骤等既有规则。`validate_docx_output` 会校验页面、Normal/编号列表样式、中文兼容字体、数学字体、总标题、区块标题和页码，不把真题答案册的 B5 页面与区块结构强加给生题系统。

每道生题的解析步骤使用独立 Word 编号实例并从 1 重新开始，不能因多题共用 `List Number` 样式而跨题延续成 3、4。导出审计会检查步骤段落具有显式编号实例，并且该实例包含 `startOverride=1`。
