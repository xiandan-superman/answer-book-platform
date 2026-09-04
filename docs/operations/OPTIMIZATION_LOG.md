# AI 优化变更账本

> 读者：后续修改本项目的 AI 代理。用途：保存不可从单个文件推断出的决策、风险和回归边界。不是用户版更新日志。

## 强制使用规则

1. 改动前完整阅读本文件，确认没有破坏既有 `invariants`。
2. 改动后在“变更记录”最上方追加一条；使用固定字段，不删除或改写历史结论。
3. 只记录实际文件改动。调查未产生改动时不记录。
4. `verification` 只能写实际运行结果；未运行写 `pending`，失败写 `failed`，禁止用计划代替结果。
5. 记录必须简洁，不保存 API Key、提示词、题目、教材内容、用户路径或其他用户数据。

## 固定记录格式

```md
### OPT-YYYYMMDD-NN｜短标题
- status: implementing | verified | reverted
- scope: 涉及的模块或用户流程
- changed: 改了什么
- trigger: 已见错误场景或考虑角度
- invariants: 必须保持的质量、失败率、计费、数据和兼容性边界
- do_not_regress: 后续禁止回退的具体行为
- verification: 实际命令及结果，或 pending
```

## 全局 invariants

- 优先提高最终 Word/PDF/题目质量和任务完成率；不得以降低质量换速度，不得提高任务失败率。
- 用户通过源码 ZIP 更新；macOS 双击 `start_platform.command`，Windows 双击 `启动平台.bat`，启动本地服务并打开网页前端。不要把桌面安装包设为必需品。
- 旧版 Pydantic 影子观察不得增加模型调用、Token、网络请求、重试、修复、降级或任务阻断；已获用户明确确认的正式输出合同由 Pydantic 阻断确定性结构错误，并允许 Instructor 做一次有预算的同路由纠错，语义风险继续由现有质量门判断。
- 用户数据位于系统用户数据目录；源码更新可替换代码目录，不得覆盖任务、教材、配置、日志和输出。

## 变更记录（最新在上）

### OPT-20260904-05｜MinerU pipeline 依赖与引擎级自检
- status: implementing
- scope: 真题/教材 PDF/DOCX 的 MinerU 隔离运行时、安装复用判定、解析后端和用户任务恢复
- changed: 从 MinerU 基础包改为固定 `mineru[pipeline]==3.4.5`，显式使用 pipeline 后端；新运行目录包含 pipeline 身份，安装指纹绑定依赖摘要，并在每次复用前实际导入 torch 和 MinerU pipeline 模块自检。
- trigger: v0.9.43 用户机已使用 Python 3.11 并安装 MinerU 基础 CLI，但真实 DOCX 任务仍因默认 `hybrid-engine` 缺少 `mineru[pipeline]`/torch 在模型调用前失败；原安装检查只验证 CLI 文件存在。
- invariants: MinerU 仍是唯一主解析器，不回退旧解析链；重型依赖仍隔离在用户数据目录；不覆盖 Key、任务、教材、日志和历史输出；确定性环境失败不通过模型重试掩盖。
- do_not_regress: 不得再用 `mineru --version` 或 CLI 文件存在代替实际后端可用性；不得依赖上游可变的默认后端；依赖档变更后不得静默复用旧环境。
- verification: 锁定 Python 3.11.15 定向回归 17 passed，完整门禁的 pytest 与 coverage 两轮均为 2002 passed、1 skipped、16 deselected，分支覆盖率 69%，编译、版本一致性、公式、第三方声明、项目完整度、Ruff 和 Mypy 全部通过；401 文件源码 ZIP 反向验证 0 问题，独立数据目录启动返回 0.9.44 且首页 HTTP 200；Windows 真实 pipeline 安装、用户 DOCX 解析、整任务交付及重启复跑待发布部署后执行。

### OPT-20260904-04｜固定 Python 3.11 并前置拦截 MinerU 不兼容环境
- status: verified
- scope: Windows/macOS 源码入口、桌面 bootstrap、运行环境创建与隔离、环境页、真题/教材 MinerU 主解析、任务失败恢复；两类生题共享运行时但不改变其生成与交付合同
- changed: 把“Python 3.11 或更高”收紧为精确 3.11；错误版本创建的 `python-env-py311` 先改名隔离保留再重建；MinerU 运行目录增加 `py311` 身份并在 pip 前检查解释器；环境页和流水线在文档解析及模型调用前阻止不兼容版本，用户错误改为简短修复指引。Windows 首次环境若缺少 pip，现会通过 `ensurepip` 自动修复并复核后继续安装。
- trigger: 0.9.41 Windows 用户机仅安装 Python 3.14，启动器错误放行并创建 3.14 环境，MinerU 3.4.5 因要求 `<3.14` 在新任务的 `extract_exam` 阶段安装失败；环境检查此前错误显示通过。
- invariants: 其他 Python 版本可并排保留；不得原地破坏旧环境，不得覆盖 API Key、任务、教材、日志或输出；不回退 MinerU 主解析，不用模型重试掩盖本地依赖错误；失败必须在模型调用前可读地停止。
- do_not_regress: 不得用目录名推断解释器版本；不得恢复 `>=3.11` 的宽松入口；不得在环境检查通过后才发现确定性的 Python/MinerU 不兼容；不得把完整 pip 候选版本列表暴露为用户主错误。
- verification: Python 3.11.15 首轮定向回归 159 passed，扩大启动、更新、环境、解析、流水线和前端回归 217 passed；0.9.42 完整门禁 1999 passed、1 skipped、16 deselected，401 文件源码 ZIP 反向验证 0 问题且独立启动 `/api/version` 返回 0.9.42、首页 HTTP 200，GitHub 质量、Windows/macOS 依赖锁、Chromium 冒烟、公开发布包及稳定源复核均成功；Windows 缺 pip 追加修复定向回归 21 passed，完整门禁 2000 passed、1 skipped、16 deselected，编译、版本一致性、公式、第三方声明、项目完整度、Ruff、Mypy 和分支覆盖率全部通过，0.9.43 的 401 文件源码 ZIP 反向验证 0 问题且独立启动返回 0.9.43/首页 HTTP 200，GitHub 质量、两平台依赖锁、Chromium 冒烟、公开包与稳定源复核成功；受影响 Windows 用户机在无运行/排队任务后已通过官方 winget 并排安装 Python 3.11.9 并保留 3.14.6，0.9.43 正式源码更新器报告 `completed` 且备份可用，平台和服务进程均实测为 `python-env-py311`/Python 3.11.9，`/api/version` 返回 0.9.43，MinerU 3.4.5 及其 `mineru-3.4.5-py311` 环境安装成功并通过 `mineru --version`；未重跑会产生付费模型调用的失败任务。

### OPT-20260904-03｜v0.9.41 源码发布准备
- status: verified
- scope: 版本元数据、用户更新日志、本地源码候选包、GitHub 自动源码发布与稳定更新源
- changed: 将 `APP_VERSION`、`VERSION` 和发布清单统一升级为 0.9.41，新增用户可见版本记录，汇总 OfficeCLI 默认 B、MinerU 主解析、Pydantic/Instructor 结构纠错、数学判等、LiteLLM 灵算影子、动态 180% 调用预算和 Python 3.11 运行基线。
- trigger: 用户要求将本轮完备项目推送并更新正式版本。
- invariants: 正式源码包只从 Git 索引生成，不包含 API Key、任务、教材、日志、输出或本机配置；必须先通过锁定 Python 3.11 完整门禁和本地源码包反向验证，只推送 `main`，标签、公开附件和稳定更新源只能由受保护工作流创建。
- do_not_regress: 不得手工创建或推送正式标签；不得把 Git push、本地 ZIP、公开源码更新版和桌面安装包混为同一状态；不得用真实付费模型或影子请求作为发布门禁。
- verification: 锁定 Python 3.11.15 独立环境按 `requirements.txt`、`requirements-dev.txt` 和 `constraints-py311.txt` 安装 107 个包且无冲突；`python scripts/run_quality_gates.py --full` 通过，pytest 与 coverage 两轮均为 1991 passed、1 skipped、16 deselected，整库分支覆盖率 70%，py_compile、版本一致性、公式 OMML、第三方声明、项目完整度、Ruff 和 Mypy 全部通过；从 Git 索引生成 401 文件的本地源码 ZIP，反向验证 0 问题，并以独立数据目录启动压缩包，`/api/version` 返回 0.9.41、首页 HTTP 200；Git 推送与公开更新源状态在发布汇报中另行记录，未发起真实模型、LiteLLM 影子或图片请求。

### OPT-20260904-02｜Python 3.11 与四类开源引擎接入
- status: verified
- scope: 源码启动/依赖/发布矩阵、真题与教材 PDF/DOCX 解析、真题答案和练习生成结构合同、计算公式一致性、灵算模型影子流量、环境诊断；按题与知识点出题共用练习链，历史产物和 Word/PDF 交付合同保持不变
- changed: 运行时最低版本和唯一 CI/发布依赖档升级为 Python 3.11，用户数据目录新建 `python-env-py311` 而不原地改坏旧 3.9 环境；MinerU 3.4.5 作为 PDF/DOCX 唯一主解析器，在用户数据目录的隔离环境按需安装并输出既有 `TextbookPackage/content_list` 合同，失败显式终止且不回退旧解析；新增正式 Pydantic 输出合同，Instructor 1.16.0 在真题单题/批量答案及练习生成、规划、来源分析、语义审查、题图修复中负责 schema 注入、错误回灌和最多一次同路由纠错，所有请求继续走平台预算/并发/账本；SymPy 1.14.0 + latex2sympy2_extended 1.11.0 + Math-Verify 0.9.0 仅对原数值规则无法判定且两侧符号集合一致的等式做隔离进程判等，进程间只传 JSON；LiteLLM 1.99.0 只对灵算做默认 10% 影子样本，单个 daemon worker、最多 4 个待处理、无重试、失败不影响主结果，日志只留摘要/耗时/JSON 可解析性/用量。实施前核验 Codex `https://github.com/openai/codex.git` 默认分支 `main@8e6a44b428e31f91b21edc97904fcdf4f0931ade` 的 `codex-rs/protocol/src/mcp.rs` 结构化工具结果合同，及 DeepSeek Harness `https://github.com/deepseek-ai/deepseek-harness.git` 默认分支 `master@76fda729799fe9b3848dbe2c211d4b231032b81e` 的会话事件、结构化错误、重试与 `packages/session/session-checkpoint-policy/tests/crash-recovery.e2e.ts`；本项目保留确定性教学规则和既有运输层，不复制 Harness。
- trigger: 用户确认统一升级到 Python 3.11，MinerU 无需 A/B，LiteLLM 先接灵算影子对照，其余按建议将结构化纠错和数学判等正式接入；目标是把模型结构错误变成可回灌错误、把解析/OCR 和数学判等交给成熟开源引擎，同时不让影子实验影响正式结果。
- invariants: MinerU 不静默切换旧解析器；Instructor 的每次纠错仍计入动态 180% 调用预算、Token、墙钟与服务商并发；LiteLLM 不参与主结果、不重试、不携带内嵌图片、不突破灵算并发且队列满时直接跳过；Math-Verify 必须按 `gold, answer` 顺序调用，SymPy 对象不得跨进程 pickle；原有分区守恒、单位/百分数规则、V4 公式链、Word A/B 和最终验收继续权威；日志不保存题目正文、模型内容或密钥。
- do_not_regress: 不得重新支持 Python 3.10 及以下或恢复多套依赖指纹；不得把 MinerU 重型依赖装进 Web 主进程；不得把 Pydantic 类型通过当作语义正确；不得让 Instructor 形成无界自修或绕过现有调用账本；不得让 LiteLLM 影子失败阻断/替换主响应或把影子量伪装成主调用；不得用符号判等替代已有单位和业务守恒规则。
- verification: Python 3.11.15 干净环境通过 `uv pip install -r requirements.txt -r requirements-dev.txt -c constraints-py311.txt`，共解算并安装 107 个包且依赖无冲突；开源适配器、依赖档、源码工作流、教材索引和计算定向回归 68 passed；第一次全量回归的 4 个旧测试兼容问题修正后，`python scripts/run_quality_gates.py --full` 最终通过：pytest 与 coverage 两轮均为 1990 passed、1 skipped、16 deselected，整库分支覆盖率 70%，py_compile、版本一致性、公式 OMML、第三方声明（101 个声明包无缺项）、项目完整性（80 个文件、0 问题）、Ruff 和 Mypy 全部通过；随后对影子 daemon、消息快照/脱敏、Python 3.11 独立运行环境与回滚隔离的最终调整完成定向回归 94 passed，Ruff、Mypy、shell 语法和 `git diff --check` 通过；未安装真实 MinerU 模型、未发起真实模型/影子请求，未部署、未发布。

### OPT-20260904-01｜OfficeCLI Word 工具 A/B 直连接入
- status: verified
- scope: 真题解析 Word、按题/知识点出题的题目卷/答案卷/合并卷、历史重建、练习缓存、文档工具遥测、DOCX/OMML/图片/表格/页码审计与最终交付
- changed: 完整保留 A 版 Python/OMML 工具链；新增 B 版，直接以固定且校验的 iOfficeAI/OfficeCLI 1.0.147 执行 `create → atomic batch --stop-on-error → save → validate → close`，命令计划覆盖段落、run、公式、图片、表格、分节、页眉页脚和页码并叠加现有教学排版合同；源码与用户配置默认 B，环境变量或用户数据配置可明确切回 A，不做静默回退；运行时按官方发布 SHA-256 校验后保存在用户数据缓存，不执行会修改 PATH/shell/Agent skills 的上游安装脚本；练习缓存键隔离 A/B，工具结果记录版本、引擎、运行时版本和摘要；新增完整 A/B 回顾文档。实施前核验 Codex `main@f46671b14aa3bc37d4ee9a67c06385cb9ec8e2d3`、DeepSeek Harness `master@76fda729799fe9b3848dbe2c211d4b231032b81e` 和 OfficeCLI `main@b94f3906fd52d450c64f8e40370e376b9e15079e`。
- trigger: 用户要求把现有优化工具保留为 A，以 OfficeCLI 的真实文档执行实现作为 B 并默认启用，同时保留平台独有的试题、答案、公式、图片和排版规则，形成可回顾、可明确回滚而不掩盖 B 缺陷的实验。
- invariants: 不用“先由 A 生成、再由 OfficeCLI 验证”冒充 B；不丢题目、答案、公式、表格或图片，不把非 `given` 公式泄露到题目卷；公式内可见字符继续全斜体；B 失败不自动切 A，未通过现有 DOCX/渲染/最终验收不交付；运行时下载必须固定版本、校验摘要且不修改源码或用户系统配置；事件不保存用户正文或密钥。
- do_not_regress: 不得删除 A 或把回滚改成隐式；不得让 A/B 共用同一练习缓存键；不得省略 resident 的显式保存/关闭；不得恢复负 `firstLine` 生成非法 OOXML；不得只认 `word/media/` 而误报 OfficeCLI 合法的包根 `media/` 图片；升级 OfficeCLI 前必须重新核验源码、版本、各平台 SHA-256、命令合同和真实文档回归。
- verification: A/B 定向回归最终 73 passed、1 skipped（无外部运行时的普通测试显式跳过 B 真实集成）；`OFFICECLI_TEST_BINARY=/tmp/.../officecli python3 -m pytest -q tests/test_word_tool_ab.py::test_officecli_b_real_integration` 1 passed，覆盖真题和练习的原生公式、图片、表格、页码及现有合同；固定运行时自动下载实测返回 1.0.147 且 SHA-256 为发布清单值；OfficeCLI `validate` 对真题、题目卷和答案卷均为 no errors，HTML contact-sheet 截图人工检查通过；`python3 scripts/run_quality_gates.py --full` 通过，pytest 与 coverage 两轮均为 1986 passed、1 skipped、16 deselected，整库分支覆盖率 70%，py_compile、版本一致性、公式 OMML、第三方声明、项目完整性、Ruff 和 Mypy 全部通过；未发起模型请求，未部署用户机，未发布。

### OPT-20260903-06｜文档工具合同与候选验收纠错闭环
- status: verified
- scope: 真题/练习 Word 构建、OMML 公式排版、Word→PDF/PNG 验收、内容质量模型回修、运行诊断证据
- changed: 新增不含正文的文档工具 `call/result` 持久事件与 `ok/data/error/meta` 结果合同，真题 Word、练习 Word 和渲染验收都返回稳定错误码、主责层、候选/产物哈希与下一步建议；保护 `cases/aligned/matrix/array` 等复合环境不在内部箭头处分割，普通长反应式拆分前逐段预验证并可原子回退；小数除法不再截成伪除零，答案摘要的除法与科学计数法提升为公式对象；内容修复首轮加入确定性算术详情，后续轮以最新完整候选和结构化验收结果继续，默认 3 轮、硬上限 4 轮。实施前动态核验 Codex `main@6d7f6dcd2285de70a3892d4f05b2a8ff44aa3350`、DeepSeek Harness `master@76fda729799fe9b3848dbe2c211d4b231032b81e`、AIOffice `main@5159cb743193fc445862c1bd450cc65686946bbf` 和 docx-mcp-server `main@9b36ea3e5aaa71af0acb13328d05e439ebeb7e1c`；未复制上游源码，未增加 .NET/Node 运行依赖。
- trigger: 完整复合 LaTeX 本可转换，但本地排版函数在内部箭头处拆成不完整环境，导致 Word 硬失败；同任务还显示内容修复首轮只收到泛化算术错误，候选失败又没有形成统一、可行动的 Harness 工具结果。
- invariants: 程序后处理不得改坏模型的正确完整公式；确定性错误不得通过增加模型调用掩盖；只有模型内容问题进入有界纠错，每轮失败调用仍受任务调用、token、耗时和服务商并发上限约束；未通过硬验收的候选不发布；诊断日志不保存题目、答案或密钥正文。
- do_not_regress: 不得恢复对复合公式的字符级盲分割；不得把文档工具失败压成不带位置/建议的单一异常；不得在内容修复重试时回到原始错误答案或只传“再试一次”；不得把未重跑的历史任务标记为已修复。
- verification: `python3 -m pytest -q tests/test_audit_model_repair_regression.py tests/test_generation_image_label_language.py tests/test_calculation_consistency.py tests/test_concurrency_limits.py tests/test_document_tool.py tests/test_docx_contracts.py tests/test_formula_notation_policy.py tests/test_pipeline_quality_routing.py tests/test_practice_export_jobs.py tests/test_zero_usable_answer_delivery.py tests/test_hybrid_local_delivery.py` 最终 128 passed；`python3 scripts/run_quality_gates.py --full` 通过（py_compile、版本一致性、全量 pytest、公式 OMML、第三方声明、项目完整性、ruff、mypy 和覆盖率门禁全部通过）；未重跑历史任务，未部署，未发布。

### OPT-20260903-05｜模型故障强制做 Harness 分层归因

- status: verified
- scope: AI 代理协作规则、模型任务故障诊断、待授权问题记录
- changed: `AGENTS.md` 增加强制诊断流程，要求按模型输入、模型输出、供应商传输、Harness 编排、本地确定性后处理和交付环境分层归因，并回答 Codex Harness + 相应模型/工具是否可完成及本项目差距；新增待授权问题记录，保存复合公式被本地拆分破坏、工具错误未形成模型可见闭环、独立内容质量问题及计划修复/验证边界。
- trigger: 已定位的 Word 失败虽然找到了异常位置，但初次汇报没有先判断原始模型输入输出是否正确，也没有清楚说明 Codex/DeepSeek Harness 如何通过工具结果回灌和继续执行避免固定流水线失去修复机会。
- invariants: 不把模型参与等同于模型责任；不让 Harness 对照替代本项目教学质量与 DOCX/OMML 确定性合同；不因诊断自动修改、重跑、部署或发布；问题记录不保存用户材料、任务标识、凭据或完整模型内容。
- do_not_regress: 后续不得只凭异常栈下结论；不得混淆供应商失败率、调用预算、内容质量和最终交付故障；模型输入输出问题必须写出当前官方 Harness 的具体处理与本项目差距；确定性程序改坏正确输出时不得通过增加模型重试掩盖。
- verification: `git diff --check` 通过；`docs/operations/KNOWN_ISSUES.md` 存在，`AGENTS.md` 的流程引用、待实施状态与账本编号检索通过。仅修改协作规则和脱敏问题记录；未修改业务代码，未重跑任务，未部署或发布。

### OPT-20260903-04｜按题量动态分配模型调用预算

- status: verified
- scope: 真题解析、教材证据链、按题/知识点生题、恢复运行、模型调用账本与流水线遥测
- changed: 题目结构确认后按任务类型、实际题量和教材证据链估算正常模型调用量，默认以预估量的 180% 生成动态上限，保留 120 下限、500 硬上限和显式环境固定值优先；预算在本次运行首次请求后冻结并写入遥测，生题链优先采用已确认练习计划题数。
- trigger: 大型教材证据任务的逐题规划、证据选择、答案生成及瞬时失败合计触达固定 120 次上限，导致后续修复和 Word 交付阶段无法继续。
- invariants: 失败请求继续计入调用量；调用、token、墙钟时间和按服务商/模型/协议熔断保持独立硬边界；不因放宽预算降低内容质量、切换模型/协议或压缩题目及教材证据；任务恢复不重复放大已完成工作。
- do_not_regress: 不得按 180% 无硬上限扩张；不得让中途环境变化重算同一运行预算；不得让小任务低于原 120 次保护值；不得用提高调用阈值代替灵算通道降并发、冷却和错误分类。
- verification: 2026-09-03 动态核对 OpenAI Codex `https://github.com/openai/codex.git` 默认分支 `main`、提交 `728cb12fe5794b0c3a8e776fb4994b1650b973a8`，阅读 `responses_retry.rs` 与 `rollout_budget.rs`；核对 DeepSeek Harness `https://github.com/deepseek-ai/deepseek-harness.git` 默认分支 `master`、提交 `76fda729799fe9b3848dbe2c211d4b231032b81e`，阅读 `llm-retry/src/index.ts`、`llm/src/retry-policy.ts` 与 `llm-retry/README.md`。动态预算、调用账本、遥测和生题定向回归 46 passed，跨真题恢复、教材证据、答案生成、生题运行、重试预算、任务准入和交付扩大回归 96 passed；`python3 scripts/run_quality_gates.py --full` 通过，pytest 与 coverage 复跑均为 1971 passed、16 deselected，分支覆盖率 70%，py_compile、版本一致性、公式 OMML、第三方声明、项目完整度、Ruff 和 Mypy 全部通过；未发起真实模型请求，未部署、未发布。

### OPT-20260903-03｜公式富文本跨业务统一与影响面强制审查

- status: verified
- scope: 真题解析、按题出题、知识点出题的公式分隔解析、生题题干/选项/答案/解析/表格/图示 Word 预检与渲染、项目协作规则
- changed: 抽取共享的 `$...$`/`$$...$$`/`\(...\)`/`\[...\]` 分隔公式解析器，在分段前保持跨行显示公式完整；生题 Word 预检扩展到所有可见文本与结构公式，渲染失败改为带题目/字段位置的明确失败，不再静默回写原始 LaTeX；`AGENTS.md` 新增三条业务线、恢复、交付与共享设施的强制影响矩阵及逐入口回归要求。
- trigger: 真题解析的多行公式修复后，全平台复查发现生题独立导出器仍会在答案分段时拆碎同类公式，预检又未覆盖答案和解析；单一业务入口的补丁不能解决平台级同类风险。
- invariants: 不改写题干、答案、解析语义和题目顺序；不增加模型请求或路由切换；公式不能转为原生 Word 对象时禁止伪装成成功交付；旧任务文本仍可在导出边界确定性归一。
- do_not_regress: 不得在识别完整公式前按换行拆分；不得只预检题干和选项而漏掉答案、解析或附属可见文本；不得在 Word 公式转换失败后输出原始 LaTeX；不得仅凭单一任务样本宣称平台级修复完成。
- verification: 共享解析器、真题公式链与生题 Word 定向回归 71 passed；生题导出/后台导出/生成门扩大回归 283 passed；`python3 scripts/run_quality_gates.py --full` 通过，pytest 与 coverage 复跑均为 1965 passed、16 deselected，分支覆盖率 70%，py_compile、版本一致性、公式 OMML、第三方声明、项目完整度、Ruff 和 Mypy 全部通过；未发起真实模型请求。

### OPT-20260903-02｜答案摘要公式结构化后再导出 Word

- status: verified
- scope: 真题解析、按题/知识点生题共用答案片段、历史任务 Word 修复、DOCX 渲染与公式审计
- changed: 为 `answer_summary` 增加耐久的文字/公式引用段；多行 `$$...$$` 整体提升为公式对象；生成、检查点迁移和 Word 本地修复共用同一幂等归一；渲染优先消费结构段，转换失败定位到题目和字段。
- trigger: 用户任务的 `answer_summary` 含跨行显示公式，旧单行 `$...$` 渲染正则误将公式内容留在普通文本，导致全部题目已成功后在 DOCX 阶段失败。
- invariants: 不改写答案语义、题目顺序、公式 LaTeX 与已成功题；不增加模型请求、重试或路由切换；旧任务保持可恢复；未通过 Word 审计和渲染的候选不交付。
- do_not_regress: 不得恢复到 DOCX 末端才用单行正则猜测摘要公式；不得将多行显示公式拆碎；不得丢弃摘要普通文字或只为通过审计而关闭严格公式门禁。
- verification: 2026-09-03 动态核对 OpenAI Codex `https://github.com/openai/codex.git` 默认分支 `main`、提交 `fc953e5234f2452e393310b2be2b29a482c4d907`，阅读 `core-plugins/src/artifact_operation.rs` 和结构化输出合同；核对 DeepSeek Harness `https://github.com/deepseek-ai/deepseek-harness.git` 默认分支 `master`、提交 `49a606bc5b5934603f22a26957a07dc799ab0291`，阅读工具 schema/结果校验与持久化合同；两个上游均无 DOCX/OMML 转换层。最终公式结构定向回归 40 passed，生成/检查点/DOCX/流水线扩大回归 93 passed；最后一版 `python3 scripts/run_quality_gates.py --full` 通过，pytest 与 coverage 复跑均为 1961 passed、16 deselected，覆盖率 70%，py_compile、版本一致性、公式链、第三方声明、项目完整度、Ruff 和 Mypy 全部通过；未发起真实模型请求，未部署、未发布。

### OPT-20260903-01｜全平台 Harness 恢复与交付语义补齐

- status: verified
- scope: 真题解析、按题/知识点生题、教材与练习的共用模型请求、多页文件、工具恢复、Word/ZIP 交付
- changed: 将普通文本与工具模型请求收敛为原服务商/模型/协议/思考深度的可取消传输重试，分离传输失败、完整但结构无效和输出上限；删除选择性复核的缩减证据再试；工具增加稳定会话、`started/result` 恢复和结果不确定防重放；文件表示最多保留 600 张页图并按 24 张顺序分批；Word 修复后重跑内容门并绑定哈希，最终候选验收、发布和 ZIP 均使用可恢复的原子替换。
- trigger: 全平台与官方 Harness 对照发现，旧链路会把网络失败误当内容修复、复核重试丢失证据、工具进程中断后缺少结果不确定语义，且 Word 候选曾在最终验收前占用正式文件名。
- invariants: 不换服务商、模型、协议或思考深度；不压缩题干、答案、用户要求、教材/真题证据或成功工具结果；不盲目重放可能已产生外部副作用的调用；不将未渲染候选件标为正式交付。
- do_not_regress: 不得把传输错误文字加入 JSON 内容修复上下文；不得通过减少证据、输出上限或思考深度换取复核成功；不得重放 `TOOL_OUTCOME_UNKNOWN`；不得静默省略第 25 张以后页图；不得在修复后沿用旧内容验收结果或先覆盖已有正式交付。
- verification: 2026-09-03 动态核对 OpenAI Codex `https://github.com/openai/codex.git` 默认分支 `main`、提交 `8e3b180d49951c3e53140710b2baad09791cc999`，阅读 `responses_retry.rs`、`mcp_openai_file.rs`、`retained_context.rs`；核对 DeepSeek Harness `https://github.com/deepseek-ai/deepseek-harness.git` 默认分支 `master`、提交 `49a606bc5b5934603f22a26957a07dc799ab0291`，阅读 `llm-retry`、`agent-loop`、`session-persistence`、`output-retention`。新增/受影响专项回归 367 passed；`python3 scripts/run_quality_gates.py --full` 通过，pytest 与 coverage 复跑均为 1956 passed、16 deselected，覆盖率 70%，py_compile、版本一致性、公式、第三方声明、项目完整度、Ruff 和 Mypy 均通过；未发起真实计费模型请求，未部署、未发布。

### OPT-20260902-01｜临时模型故障原路由恢复与文件多表示输入

- status: verified
- scope: 真题解析、按题/知识点出题、教材库的模型重试、路由熔断、PDF/Word/图片/文本输入与 Word 公式转译
- changed: 可重试故障增加有界指数退避，考查内容规划改为原路由最多 3 次尝试，全题失败时不再把关键词兜底伪装成成功；灵算“当前账号组无支持模型的账号”404 改为可恢复路由池故障，熔断维度收紧为服务商+模型+协议。输入端保留原文件、结构化文本和页面视觉的独立状态；PDF 保留页文本与页图，Word 保留 OMML/表格/内嵌图并对结构退化题加原页视觉补偿，直接教材 PDF/Word 也纳入同一表示合同；Windows 自动发现 Office OMML2MML 转换表。
- trigger: 实际任务的灵算 Gemini 3.7 在账号池短时无可用账号时返回 404，后续同模型又可用；真题 Word 的化学公式在 Windows 未发现已安装的 Office XSL，且旧文件链路存在某一表示失败后静默缺失局部内容的风险。
- invariants: 重试不改服务商、模型、协议、消息、思考深度或证据；不压缩真题/教材必要内容；局部表示失败只在同源等价表示足够时继续，否则明确停止；补偿页图仅供模型理解，不写入最终 Word 当作原题配图。
- do_not_regress: 不得把代理账号池暂时无可用账号判为模型永久不存在；不得用某一模型/协议故障熔断整个服务商；不得静默忽略 Word 公式、图表、PDF 扫描页或非法 UTF-8 字节；不得将页面补偿图泄漏到成品文档。
- verification: 2026-09-02 动态核对 OpenAI Codex `https://github.com/openai/codex.git` 默认分支 `main`、提交 `50fffd5ed367aa99491d9ec58575626fce4e9dd4`，阅读 `codex-rs/core/src/mcp_openai_file.rs`、`codex-rs/protocol/src/user_input.rs`、`codex-rs/core/src/responses_retry.rs`；核对 DeepSeek Harness `https://github.com/deepseek-ai/deepseek-harness.git` 默认分支 `master`、提交 `49a606bc5b5934603f22a26957a07dc799ab0291`，阅读 `packages/attachment/attachment-local/README.md`、`packages/llm/llm-deepseek/README.md`、`packages/llm/llm-retry/src/index.ts`。定向回归 94 passed 和新增教材/熔断回归 64 passed；`python3 scripts/run_quality_gates.py --full` 通过，pytest 与 coverage 复跑均为 1950 passed、16 deselected，覆盖率 70%，py_compile、版本一致性、公式、第三方声明、项目完整度、Ruff 和 Mypy 均通过；未发起真实计费模型请求，未部署、未发布。

### OPT-20260901-04｜v0.9.39 源码发布准备

- status: verified
- scope: 版本元数据、用户更新日志、发布包内容与隔离数据的 Chromium 端到端发布验证
- changed: 将 `APP_VERSION`、`VERSION` 和发布清单统一升级为 0.9.39，补充用户可见更新记录；端到端夹具改为等待弹窗最终文本而不依赖动画可见性瞬间，上传契约测试显式模拟已配置 Key，不读取本机私密配置；多图回归只禁止图片容量警告，保留无 Microsoft Word OMML 样式环境中合法的公式结构降级警告。
- trigger: 用户要求推送并更新版本；发布前隔离数据端到端检查暴露了弹窗动画瞬时可见性和本机 API Key 对测试结果的隐式影响。
- invariants: 源码包只来自 Git 索引，不包含 API Key、用户任务、教材、日志或输出；测试不访问真实计费模型；发布仍由 `main` 质量工作流通过后自动建立标签与稳定源码包。
- do_not_regress: 不得手工建立版本标签或绕过质量门禁；不得用本机 Key 让端到端套件偶然通过；弹窗契约应断言最终内容，不应在 GSAP 开合动画期间读取受渲染状态影响的 `innerText`。
- verification: `python3 scripts/run_quality_gates.py --full` 通过，pytest 1943 passed、16 deselected，分支覆盖率 70%，py_compile、版本一致性、公式、第三方声明、项目完整度、Ruff 和 Mypy 均通过；隔离用户数据目录的 `python3 -m pytest -q -m e2e` 16 passed、1943 deselected；未发起真实模型请求，未部署用户机。

### OPT-20260901-03｜模型重试保持质量与协议回退收紧

- status: verified
- scope: 真题解析、生题、教材证据选择、内容/Word 修复共用模型 JSON 重试，Responses/Messages 协议适配和模型调用报告
- changed: 通用 JSON 重试改为始终使用原服务商、原模型、原协议、原消息和原思考深度，删除关思考、备选模型及业务压缩参数和所有内部调用；删除教材证据截断式压缩；确定性和模糊 400 不再通用重试，可分类的网络/超时/限流/5xx 只原路由重试；生题传输和 JSON 修复不再切到 Chat 或降低思考深度；协议适配默认关闭，仅显式启用、纯文本、同模型且端点明确返回 404/405/501 时允许一次切换。
- trigger: 匿名化实际运行记录未见压缩、关思考、换模型或协议回退真实触发，且未见上下文/输出硬上限结束；旧通用路径仍会在未授权时改变模型、思考、证据完整性或协议语义，对真题解析质量风险高于完成率收益。
- invariants: 不压缩题干、答案约束、教材/真题证据、用户消息、成功工具结果或图片；不静默换模型/服务商；不把模糊 400 扩大为全局配置阻断；保留生题已有的灵算单题模糊 400 一次同路由补偿和批次拆分；内置 Responses 路由和灵算 Gemini 已登记直达协议不变。
- do_not_regress: 不得恢复 `disable_thinking`、`fallback_model`、`compact_messages`、`compact_fallback_disable_thinking` 或业务重试中的 Responses/Messages→Chat 切换；超时、429、5xx、模糊 400、图片、工具、JSON 无效和内容问题不得触发协议回退；未经真实任务质量对比和用户确认不得引入摘要式上下文压缩。
- verification: 2026-09-01 动态核对 OpenAI Codex `https://github.com/openai/codex.git` 默认分支 `main`、提交 `633ab199cfd724aa78013c006b27a2b3d049fc3b`，阅读 `codex-rs/core/src/responses_retry.rs`、`codex-rs/core/src/compact.rs`；核对 DeepSeek Harness `https://github.com/deepseek-ai/deepseek-harness.git` 默认分支 `master`、提交 `dd6322d604e00eec1ba5e0c8541159906a21094a`，阅读 `docs/subsystems/llm-streaming.md`、`docs/subsystems/compaction.md`、`packages/llm/llm-retry/src/index.ts`。本项目采用同路由可重试故障恢复和可选的旧失败工具结果无损裁剪，不采用上游会话摘要，是真题解析证据完整性的必要差异。定向回归 173 passed；`python3 scripts/run_quality_gates.py --full` 通过，pytest 1943 passed、16 deselected，分支覆盖率 70%，py_compile、版本、公式、许可证、项目完整度、Ruff 和 Mypy 均通过；未发起真实计费模型调用，未部署或发布。

### OPT-20260901-02｜多图任务移除 8 张硬阻断与截断

- status: verified
- scope: 三类业务共用模型上下文计划、按题/知识点生题材料解析与来源分析、多图诊断和模型接入标准
- changed: 将阶段 `quality_limits.max_images` 明确降为只读质量建议，只有能力登记的 `limits.max_images_per_request` 才能阻断；生题 PDF/DOCX/独立图片的分析接收边界统一为既有 24 张参考图预算，短材料一次发送全部已接收图片，长文本仅按段落长度拆分并用稳定图片编号保持映射；超过 24 张继续显式报告未使用内容。
- trigger: 9 张以上图片虽然已被解析为耐久参考图，公共规划器仍把 GLM 的 8 张阶段建议当硬上限，材料分析又在入口和分段末尾截到前 8 张，导致任务提前失败或第 9 张以后证据未进入来源分析。
- invariants: 不静默丢弃必要图片，不因建议值提高失败率；短材料保持跨图整体理解，长材料保持图文编号；无图、纯文本识图交接、模型能力门、Token 预算、模型/协议选择、调用次数和 24 张以上容量边界不变；没有用户授权不切换供应商或模型。
- do_not_regress: 不得重新把阶段质量建议解释为供应商硬限制；不得恢复 `[:8]`、前 8 页渲染或按相同图片字节反推编号；真实硬上限必须来自精确服务商/模型能力登记；超过材料接收边界必须保留可见诊断。
- verification: 2026-09-01 从官方远端再次确认 OpenAI Codex `https://github.com/openai/codex.git` 默认分支 `main`、提交 `633ab199cfd724aa78013c006b27a2b3d049fc3b`，阅读 `attachment_state.rs`、`image_preparation.rs`；确认 DeepSeek Harness `https://github.com/deepseek-ai/deepseek-harness.git` 默认分支 `master`、提交 `dd6322d604e00eec1ba5e0c8541159906a21094a`，阅读 `attachment-local/src/index.ts`、`llm-deepseek/README.zh.md`。本项目保留 24 张教学材料接收边界和长文本确定性分段，是对本地内存、多供应商兼容与教学证据映射的必要差异，未实现 DeepSeek 600 张历史卸载。新增/调整的 9 张、12 页、24/25 张、建议/硬上限回归与核心文件定向测试 182 passed，协议和上下文扩大回归 93 passed；`python3 scripts/run_quality_gates.py --full` 通过，pytest 1939 passed、16 deselected，分支覆盖率 70%，Ruff、Mypy、Python 编译、版本、公式、许可证和项目完整度全部通过；未发起真实计费模型调用，未部署或发布。

### OPT-20260901-01｜模型问题统一核对官方 Harness 最新实现

- status: verified
- scope: 项目级模型故障诊断、模型协议/上下文/工具/重试/恢复/多模态设计、模型接入标准与上游参考治理
- changed: 将原本主要覆盖自主生图和多模态工具闭环的 Harness 规则扩展到所有模型相关问题；在 `AGENTS.md` 和模型接入标准中固定 OpenAI Codex 与 DeepSeek Harness 的唯一官方仓库身份和当前核验快照，要求每次动态确认远端默认分支和最新提交、校验本地 `origin`、记录完整 SHA/时间/文件/合同及项目差异，并在上游变化时先更新模型接入标准，禁止把旧检出、Fork、镜像或搜索摘要当作当前官方实现。
- trigger: 现有 `AGENTS.md` 只明确要求多模态工具修改优先核对 Harness，未覆盖结构化输出、上下文分片、重试、错误分类、并发、取消和会话恢复等一般模型问题，也未规定如何确认官方仓库及防止本地参考过期。
- invariants: Harness 是优先调查和合同对照基线，不替代本项目教学质量、完成率、费用、隐私和跨平台约束；上游更新不得未经差异评估直接复制；纯规范修改不改变运行代码、模型调用、任务状态、用户数据或发布状态。
- do_not_regress: 不得把同名仓库、Fork、第三方文章、缓存页面或未更新的本地副本称为官方当前实现；不得永久假设默认分支名称；采用上游结论时不得省略完整提交身份、核对时间和本项目必要差异；官方无法核验时不得伪称已查阅最新版本。
- verification: 2026-09-01 通过两个官方 Git URL 重新浅克隆并校验远端：OpenAI Codex 默认分支 `main`、提交 `633ab199cfd724aa78013c006b27a2b3d049fc3b`，DeepSeek Harness 默认分支 `master`、提交 `dd6322d604e00eec1ba5e0c8541159906a21094a`；既有接入标准引用的 Codex `spec_plan.rs`、`image-generation/src/tool.rs`、`context_window.rs` 及 DeepSeek `tools/README.md`、`tool-calls.ts`、`core.md`、`compaction.md`、`compaction-basic/README.md` 在上述提交均存在。项目完整度审计检查 77 个必需文件、0 问题；`git diff --check -- AGENTS.md docs/MODEL_PROVIDER_INTEGRATION_STANDARD_DRAFT.md docs/operations/OPTIMIZATION_LOG.md` 通过；纯协作规范改动，未运行代码测试或模型调用。

### OPT-20260831-01｜生题选项最终随机编排

- status: verified
- scope: 按题出题与知识点出题共享的正式生成结果、单题重新生成
- changed: 单选题和多选题在新题通过生成门禁、进入检查点前由后端随机打乱选项一次，再按最终顺序重新标注 A、B、C、D；已成功题、已保存结果不在续作、展示或导出时重排。
- trigger: 模型常先生成正确陈述再补干扰项，现有程序原样保留模型顺序，实测出现正确答案集中在 A 的系统性偏差。
- invariants: 不修改题干、选项文本、正确项数量、蓝图、模型提示词、调用次数、任务状态或历史用户数据；非选择题和生成失败占位不受影响。
- do_not_regress: 不得恢复直接采用模型选项顺序；不得在页面刷新、历史加载或 Word 导出时再次打乱；打乱后必须由程序连续重建选项标签。
- verification: 选项顺序、重标注与非选择题隔离新增回归 3 passed；生题、批量恢复、重试预算、阶段修复与信任边界定向回归 258 passed；完整 pytest 1936 passed、16 deselected；受影响文件 Ruff、`py_compile` 与 `git diff --check` 通过。未发起真实模型调用，未部署用户机。

### OPT-20260830-27｜用户机 0.9.38 同步补充要求组合输入

- status: verified
- scope: Windows 用户机模拟出题补充要求、8766 局域网服务启动与本机常用项持久化
- changed: 在实际运行的 0.9.38 源码目录最小同步组合输入框、常用要求本机存储模块和接口；部署前备份原文件与计划任务；将失效计划任务改为实际 0.9.38 目录和打包 Python 环境，并为 TCP 8766 增加明确入站放行。
- trigger: 用户明确要求将本地已确认优化同步到 SSH 用户机；排查发现旧计划任务仍指向 0.9.36 废弃目录，而当前 8766 实际由另一套 0.9.38 源码运行，重启后打包 Python 未被既有按程序防火墙规则覆盖。
- invariants: 只同步补充要求功能需要的后端模块、接口和三项前端文件；不覆盖密钥、任务、教材、输出或其他用户数据；版本和发布清单保持 0.9.38；不发起模型调用。
- do_not_regress: 部署判断必须以运行中 `/api/version` 和进程源码目录为准，不得再用废弃目录的 `VERSION` 推断；计划任务必须指向实际源码目录；8766 必须从局域网可访问；临时 CRUD 验证项必须删除。
- verification: 远端打包 Python `py_compile` 与 `import app.server` 通过，5 个同步文件 SHA-256 与本地最小补丁一致；远端本机 `/api/version` 返回 0.9.38 且发布清单一致，计划任务状态 Running；局域网 `/api/version`、带监控认证的首页和常用要求 GET 通过，首页包含组合输入、新建入口和目标占位文案；临时常用要求经 POST 创建和删除成功，最终 `item_count=0`、`storage=local_user_data`；未发起模型调用。

### OPT-20260830-26｜生题补充要求组合输入与本机常用项

- status: verified
- scope: 模拟出题范围确认中的补充要求输入、常用要求下拉与本机持久化
- changed: 将补充要求收敛为可直接编辑且可展开多选的单行组合输入框；下拉内直接提供逐项编辑、删除和底部新建，选择后即时同步输入；长内容单行省略；常用项通过独立本机接口原子写入系统用户数据目录。
- trigger: 独立下拉、编辑框、保存按钮和管理模式偏离用户确认的简约 C 方案，箭头点击还被全局焦点规则绘制为突兀蓝框；旧点击事件曾被误当作新建文本。
- invariants: 不修改正式生题请求中的 `focus` 字段合同、题量/难度/题型、任务状态、模型调用、计费或既有任务数据；源码更新只替换代码，不覆盖系统用户数据目录中的常用要求；不部署用户机。
- do_not_regress: 不得恢复独立编辑框、“保存为常用”或管理模式；箭头点击不得出现独立重描边；下拉长文字必须保持单行省略；新建事件不得把浏览器事件对象写入数据；编辑和删除必须留在下拉项内。
- verification: `python3 -m pytest -q tests/test_practice_requirement_presets.py tests/test_frontend_contract_guards.py tests/test_practice_redesign.py tests/test_paths_distribution.py` 177 passed；Ruff、`py_compile`、`node --check web/app.js`、`git diff --check` 通过；1280×820、760×760、560×720 内置浏览器检查通过，直接输入与勾选同步有效，长文本保持单行省略，箭头点击无独立蓝框，最终控制台 0 warning/error；未发起模型调用或部署用户机。

### OPT-20260830-25｜长结果页上下文与复核风险展示修复

- status: verified
- scope: 桌面端模拟出题历史结果、长题卡操作区、真题结果题目侧栏与需复核风险卡
- changed: 历史练习结果完成布局后再次复位页面顶部，避免滚动锚定跳过标题和流程；题卡选择入口增加可见文字和逐题无障碍名称；作图说明隐藏原始图片模型路由；结果侧栏预览去除旧 MathML/LATEX 内部标记；需复核风险将内部检查键和英文诊断转为三条独立可读提示。
- trigger: 1280×720 实页检查发现从任务列表打开 2/3 题结果时停在 `scrollY=344`，标题与流程被跳过；长题卡选择入口只剩无文字方框，作图说明直接显示 `gpt-image-2`；可交付待复核结果把 `answer_coverage`、`answer is pending review` 和三项风险挤为单段，题目侧栏可能显示 `MATHML` 标记。
- invariants: 仅调整桌面端前端滚动复位、可见标签、预览文本清理、诊断文案映射、风险排版和静态合同测试；不修改题目数据、选择/下载/生成行为、任务状态、最终验收、质量门、检查点、模型调用、计费或用户数据；`delivery_ready=true` 的需复核任务继续允许下载正式交付包；不调整移动端。
- do_not_regress: 从任务管理打开长结果必须先显示标题和流程上下文；题卡选择控件不得再次退化为无文字方框；普通视图不得暴露原始模型路由、检查键、英文内部诊断或公式序列化标记；多条复核风险不得重新压成日志式长段落；前端不得据此改变验收或下载合同。
- verification: `python3 -m pytest -q tests/test_frontend_contract_guards.py tests/test_task_result_checkpoint_view.py tests/test_render_delivery_consistency.py tests/test_practice_redesign.py` 187 passed；`node --check web/app.js`、`node --check web/task-contract-ui.js`、主站 592 个及独立 Word 工具 40 个 HTML ID 唯一性检查、`git diff --check` 通过；1280×720 内置浏览器复验历史结果入口 `scrollY=0`、题卡显示“选择”且选择后已选计数和下载按钮即时启用，普通页面不再出现 `gpt-image-2`、`answer_coverage`、`answer is pending review` 或侧栏 `MATHML` 标记，三项风险独立展示；未执行移动端、下载、复制、重新生成、继续、保存、删除、任务状态写入或模型调用。

### OPT-20260830-24｜题目编辑弹窗与验收诊断排版修复

- status: verified
- scope: 桌面端模拟出题题目编辑器、真题结果最终检查结果卡
- changed: 原生题目编辑弹窗显式居中并改为固定页眉、独立滚动正文和常驻底部操作区，长题干下仍能直接看到取消与应用修改；最终检查失败项从整段内部错误拆为用户可读的问题列表，常见缺失 Word 与答案配图问题改为操作语义，本机路径和内部检查键默认收进技术详情。
- trigger: 1280×720 真实任务实页审查发现题目编辑器因全局零外边距贴在视口左上角，底部操作需滚动整个表单才能出现；点击“查看检查结果”后，验收卡直接显示 `output missing`、本机绝对路径和 `figure_delivery`，两条问题挤成一段日志式长句。
- invariants: 只调整桌面端前端结构、样式、诊断文案映射和静态合同测试；不修改编辑保存内容与接口、任务状态、最终验收结论、质量门、下载条件、检查按钮请求、检查点、模型调用、计费或用户数据；`delivery_ready=true` 的需复核任务继续允许下载正式交付包；不调整移动端。
- do_not_regress: 桌面弹窗不得再次依赖浏览器默认外边距定位；长表单滚动时标题和提交操作必须持续可见；普通验收结果不得默认暴露绝对路径或内部检查键；技术详情仍须可展开读取原始诊断；前端不得据视觉判断改写验收或下载合同。
- verification: `python3 -m pytest -q tests/test_frontend_contract_guards.py tests/test_task_result_checkpoint_view.py tests/test_render_delivery_consistency.py tests/test_practice_redesign.py` 186 passed；`node --check web/app.js`、`node --check web/task-contract-ui.js`、主站 592 个及 Word 工具 40 个 HTML ID 唯一性检查、`git diff --check` 通过；1280×720 内置浏览器实测题目编辑弹窗由左上角 `(0,0)` 改为居中 `(256.5,24)`、底部操作区位于可视范围内，最终检查卡默认只显示两条可读问题且原始路径/内部键收进折叠技术详情；未执行移动端、编辑保存、下载、任务状态写入或模型调用。

### OPT-20260830-23｜运行详情交互边界与发布端到端同步

- status: verified
- scope: 桌面端任务管理运行详情、平台弹窗、生题文件上传与发布端到端门禁
- changed: 任务卡的“运行详情”折叠点击不再冒泡为打开整个任务，内部复制 ID 按钮仍按原动作处理；Chromium 用例改为等待弹窗队列的最终文案，显式展开折叠运行指标，并在文件上传契约测试中选择不需要外部生图配置的传统绘图路线。
- trigger: 正式发布前首次显式运行全部 `e2e` 标记用例时，发现旧断言在弹窗动画队列中抢跑、运行指标已移入折叠区，并定位到点击“运行详情”会误触发整卡跳转的真实回归。
- invariants: 不改变任务状态、暂停/继续/取消请求、模型调用、计费、生图默认选择、弹窗产品动画或用户数据；端到端服务仅使用隔离数据目录和不会发出真实供应商请求的占位配置。
- do_not_regress: 折叠“运行详情”不得打开任务详情页；折叠内明确的 `data-action` 按钮必须继续可用；动画弹窗测试不得只等待外层去除 `hidden`；默认排除 `e2e` 的普通 pytest 不得被误报为浏览器验收。
- verification: 锁定 Python 3.11 环境下 `ANSWER_BOOK_E2E_URL=http://127.0.0.1:8876 python -m pytest -q -m e2e` 为 16 passed、1926 deselected；`node --check web/app.js` 通过；门禁使用隔离 `ANSWER_BOOK_DATA_DIR`，未发出真实模型或图片请求。

### OPT-20260830-22｜模型配置与格式规则长页降噪

- status: verified
- scope: 桌面端真题解析高级模型设置、按题/知识点出题模型配置、Word 格式标准长页、历史任务阶段展示
- changed: 模型设置中的原始模型路由改为配置标签和可读状态，移除与下拉框重复的服务商/模型组合；旧 `uploading` 阶段改为“准备输入材料”；Word 完整规则按五个分类折叠展示，首类默认展开，显示分类和规则数量，并在编辑、取消后保留展开状态。
- trigger: 逐页实测发现三套模型配置把 `provider / raw-model-id` 重复暴露在已选择的下拉框下，既冗余又偏技术；历史失败任务直接显示 `停止于 uploading`；真题答案标准把 21 项规则全部铺开，页面高且难以定位，单项编辑虽可用但被大量同层信息淹没。
- invariants: 仅调整前端可读标签、信息层级、原生折叠展示及静态合同测试；不修改模型路由、配置保存、模型能力、格式规则内容或保存范围、任务状态、检查点、质量门、下载权限、模型调用、计费、用户数据或移动端；`delivery_ready=true` 的需复核任务继续允许下载正式交付包。
- do_not_regress: 普通模型设置页不得再次显示原始模型路由或重复完整选择结果；未知历史阶段不得直接把英文内部值放入主视图；完整格式规则必须可按分类快速定位，折叠不得让当前编辑项消失或破坏单项保存/取消；折叠摘要必须保留可读标题和数量。
- verification: `python3 -m pytest -q tests/test_frontend_contract_guards.py tests/test_task_result_checkpoint_view.py tests/test_render_delivery_consistency.py tests/test_practice_redesign.py` 184 passed；`node --check web/app.js`、`node --check web/task-contract-ui.js`、主站 592 个及 Word 工具 40 个 HTML ID 唯一性检查、`git diff --check` 通过；1280px 内置浏览器复验真题/按题/知识点模型页无原始 `gpt-*`/`gemini-*` 路由文本，Word 规则页由 21 项全展开收敛为 5 类、默认 1 类展开且页面无横向溢出，跨分类展开、修改及取消有效，历史 `uploading` 显示为“准备输入材料”；未执行移动端、配置写入、规则保存、文件上传、下载、任务状态写入或模型调用。

### OPT-20260830-21｜继续操作语义与空状态去伪入口

- status: verified
- scope: 桌面端模拟出题部分完成结果、任务卡继续操作、API Key 配置、未完成任务恢复弹窗、格式审查任务空状态
- changed: “继续未完成项”和任务恢复操作不再使用播放三角形，统一改为向右继续语义；部分结果动作改用现有主次按钮组件并补齐 42px 高度、内边距和图文间距；API 平台已展开并显示就地校验时隐藏过时的全局“请选择平台”提示，只有真实测试/保存结果才显示全局反馈；任务恢复弹窗眉题改为“任务恢复”，避免与主标题重复；按业务类型筛选且该类型无任务时，空状态直接提供对应新建入口，格式审查入口已实测可进入工具页。
- trigger: 用户截图指出“继续未完成项”显示为紧凑的播放按钮，图标语义像播放视频且图文贴合；继续检查发现 API 就地错误下仍有过时全局提示、恢复弹窗连续两次表达未完成，以及格式审查零任务页只提供“清除筛选”而没有创建任务的有效下一步。
- invariants: 仅调整前端图标、现有按钮组件、提示可见性、空状态路由和静态合同测试；不修改任务状态、继续/重试/创建业务参数、API Key 测试与保存规则、质量门、检查点、模型调用、计费、用户数据或移动端；`delivery_ready=true` 的需复核任务继续允许下载正式交付包。
- do_not_regress: 继续/恢复操作不得使用播放媒体语义；结果动作不得绕过现有按钮组件形成紧凑胶囊；就地校验出现后不得同时保留相矛盾的全局初始提示；单一业务类型无任务时必须提供可生效的新建入口，而有搜索或状态筛选时仍应提供清除筛选；空状态动态图标更新不得因已渲染 SVG 而退化为问号图标。
- verification: `python3 -m pytest -q tests/test_frontend_contract_guards.py tests/test_task_result_checkpoint_view.py tests/test_render_delivery_consistency.py tests/test_practice_redesign.py` 181 passed；`node --check web/app.js`、`node --check web/task-contract-ui.js`、HTML ID 唯一性检查和 `git diff --check` 通过；1273×716 内置浏览器复验 2/3 部分完成结果按钮为 42px、高度与 8px 图文间距，API 空 Key 只保留就地错误，任务恢复眉题不再重复，格式审查空状态显示“新建格式审查”及加号并实际进入 `/word-format`；未执行移动端、真实继续/重试、API Key 写入、文件上传、下载或模型调用。

### OPT-20260830-20｜灵算 Gemini 3.7 单选固定到 medium 真实路由

- status: verified
- scope: 灵算 Google 模型选项、旧配置迁移、思考下限、原生图片工具能力登记与能力文档
- changed: 将单个可见的“Gemini 3.7 Flash”选项从灵算不接受的无后缀 `gemini-3.7-flash` 改为真实可用的 `gemini-3.7-flash-medium`；保持供应商整体默认模型为 3.6，将旧无后缀、low 和 high 保存值收敛到 medium，固定 `thinking_minimum=medium`，并在闭集能力表登记 medium 的 Chat Completions 原生工具路由。回看闭环继续遵循 Codex 工具结果携带真实图片输入以及 DeepSeek Harness 保留模型工具调用和最终工具内容的历史映射，未改写循环实现。
- trigger: 新版把 low/medium/high 合并为无后缀模型后，灵算明确返回 `404 model_not_found`，导致知识点出题的主模型自主生图路由被阻断；同一账号组的实时模型目录只列出三个带后缀的 3.7 路由。
- invariants: 不伪造或调用无后缀 3.7；不把官方 Google AI 直连能力继承给灵算；只有真实工具调用后准确看到图片像素的路由才能登记自主生图；不新增补偿调用、静默换模型或降低质量；不覆盖本地密钥、任务和其他用户数据；不部署用户机。
- do_not_regress: 公开选择器不得恢复 low/medium/high 三个 3.7 选项，也不得发送 `gemini-3.7-flash`；展示标签必须保持“Gemini 3.7 Flash”，底层路由必须为 medium；旧无后缀、low 和 high 值必须无损迁移到 medium；能力登记必须与协议和模型精确匹配。
- verification: 本机与用户机各自使用已保存的灵算 Google 密钥只读实测，`gemini-3.7-flash-medium` 均返回 HTTP 200，无后缀路由均返回 404；medium 完成 2 步、1 次原生工具调用的盲图回看，准确识别左侧紫色三角形和右侧绿色菱形，并只引用已看过的资产。锁定 Python 3.11 完整 pytest 为 1921 passed、16 deselected；58 个配置/能力/协议/工具循环定向回归通过；变更文件 Ruff、`mypy app/settings.py app/model_capability_registry.py`、`py_compile`、能力文档同步检查和 `git diff --check` 通过；将未修改的 `app/model_tool_loop.py` 加入单文件 Mypy 时仍有 4 个已存在类型错误，本轮未改写该循环。未修改或部署用户机。

### OPT-20260830-19｜批量操作、部分结果与监控动作层级修复

- status: verified
- scope: 桌面端任务多选管理、模拟出题部分完成结果、运行监控与磁盘空间管理
- changed: 将任务批量管理收拢为同一组有边界的操作并明确“选择本页任务”；部分完成结果改为结构化状态卡，将继续生成动作从说明文字中分离，并提供可展开的复核原因；批量下载和重新生成文案明确作用于已选题目；运行监控刷新、磁盘重新统计和清理动作改为可辨识的按钮，刷新与统计期间提供禁用、旋转图标和 `aria-busy` 反馈。
- trigger: 1273×716 实页检查发现任务多选入口像低对比度文字、部分完成状态与继续动作挤成一行且复核原因不可见；运行监控和磁盘管理的关键动作仍呈现为小号蓝/红文字，点击后缺少进行中反馈，容易被误认为装饰或未生效。
- invariants: 仅调整前端文案、信息层级、桌面按钮样式、刷新反馈和静态合同测试；不修改任务状态、生成数量合同、重试/继续行为、质量门、检查点、模型调用、计费、缓存清理范围、用户数据或移动端；`delivery_ready=true` 的需复核任务继续允许下载正式交付包。
- do_not_regress: 批量操作必须明确作用范围；部分完成状态不得把说明、复核原因和恢复动作压成连续文字；已选题目动作必须显式说明“已选”；运行监控与磁盘管理主操作不得退回无边界的小号文字，异步刷新必须有可见忙碌反馈并防止重复点击。
- verification: `python3 -m pytest -q tests/test_frontend_contract_guards.py tests/test_task_result_checkpoint_view.py tests/test_render_delivery_consistency.py tests/test_practice_redesign.py` 179 passed；`node --check web/app.js`、`node --check web/task-contract-ui.js` 和 `git diff --check` 通过；1273×716 内置浏览器复验任务多选、2/3 部分完成、0/2 失败占位、运行监控与磁盘操作按钮，部分结果页出现“下载已选 Word”“查看 3 项复核提示”和独立“继续未完成项”；未执行移动端、真实继续/重试、下载、缓存删除、任务状态写入或模型调用。

### OPT-20260830-18｜零题结果与长任务搜索可用性修复

- status: verified
- scope: 桌面端模拟出题零题结果页、失败题目卡片、长任务列表搜索与筛选空状态
- changed: 零题结果不再显示“部分题目已生成”或附带“题目已生成·待复核”；失败占位卡隐藏无效的编辑与仅含禁用项的更多菜单，仅保留反馈和重新生成/配置入口；任务管理新增名称、材料、模型与文件维度的本地搜索、清除入口和可恢复空状态，排序与搜索会回到第一页；后台刷新已有缓存任务时不再把搜索无结果误报为“正在读取”；搜索工具栏允许收缩换行，避免长条件挤压桌面排版。
- trigger: 1273×716 实页检查发现 0/2 结果仍用部分完成语义、失败占位文本可被当作题目编辑、“更多”菜单打开后只有禁用项；32 个任务只能逐页查找，搜索无结果曾误显示持续加载且没有恢复动作，长搜索条件需要稳定的桌面收缩规则。
- invariants: 仅调整前端文案、可见操作条件、客户端搜索/分页展示、桌面工具栏排版和静态合同测试；不修改任务状态、完成度数据、生成/重试/配置动作、质量门、检查点、模型调用、计费、用户数据或移动端；`delivery_ready=true` 的需复核任务继续允许下载正式交付包。
- do_not_regress: 0 题生成不得使用已生成或部分完成语义；失败占位内容不得提供看似可用的编辑、复制或下载入口；已有任务缓存的后台刷新不得覆盖真实搜索空状态；任务搜索必须有清除与恢复入口，长条件不得挤出横向溢出。
- verification: `python3 -m pytest -q tests/test_frontend_contract_guards.py tests/test_task_result_checkpoint_view.py tests/test_render_delivery_consistency.py tests/test_practice_redesign.py` 177 passed；`node --check web/app.js`、`node --check web/task-contract-ui.js`、HTML 592 个 ID 唯一性检查和 `git diff --check` 通过；1273×716 内置浏览器复验 32 项任务搜索、无结果恢复、0/2 结果标题与失败卡片操作，页面横向宽度 1273/1273 且 `scrollX=0`；未执行移动端、真实重试、配置写入、下载、任务状态写入或模型调用，配置阻断零结果仅完成代码路径检查。

### OPT-20260830-17｜长任务页导航与蓝图审查降噪

- status: verified
- scope: 桌面端模拟出题蓝图审查、失败任务卡、真题长结果导航、需复核结果与审查报告映射
- changed: 蓝图页使用固定动作标题并移除与训练目标相邻重复的长标题；将每项大量来源选择收进带已选数量的折叠入口；失败任务恢复入口统一为“处理未完成”；长结果侧栏改用全局连续项序并保留原题号；审查映射将内部题目标识移入折叠技术信息、公开提示统一可读化并将三项摘要排为同一行；需复核交付文案明确正式包仍可下载。
- trigger: 1440×1000 深入等待确认、失败、长结果和需复核页面发现，训练目标在标题与文本框连续重复、来源复选框过度拉长蓝图、失败入口名称与实际恢复操作不符、跨分组原题号重复导致侧栏定位歧义、内部题目标识抢占主内容，以及需复核文案与既有下载权限表达矛盾。
- invariants: 仅调整前端标题、文案、展示层级、折叠结构、桌面栅格和静态合同测试；不修改任务状态、失败恢复/重试行为、蓝图数据、质量门、检查点、模型调用、计费、用户数据或移动端；`delivery_ready=true` 的需复核任务继续允许下载正式交付包。
- do_not_regress: 长结果列表必须具备唯一连续导航标识并保留原题号；普通用户主视图不得优先暴露内部题目标识；可恢复失败任务的入口必须表达“处理”而非只读查看；长来源列表不得默认撑高每个蓝图项；需复核提示不得暗示已允许的正式包下载被阻断。
- verification: `python3 -m pytest -q tests/test_frontend_contract_guards.py tests/test_task_result_checkpoint_view.py tests/test_render_delivery_consistency.py tests/test_practice_redesign.py` 176 passed；`node --check web/app.js`、`node --check web/task-contract-ui.js`、HTML 589 个 ID 唯一性检查和 `git diff --check` 通过；1440×1000 内置浏览器复验固定蓝图标题、折叠来源、1—11 连续结果导航、折叠技术信息、三列审查摘要、失败任务操作名和需复核正式下载文案；未执行移动端、真实确认、重试、下载、任务写入或模型调用。

### OPT-20260830-16｜暂停筛选与审查映射交互补全

- status: verified
- scope: 桌面端任务管理状态筛选、运行任务卡、真题需复核风险摘要与审查报告映射
- changed: 新增已暂停任务统计和筛选入口；相邻重复阶段只展示一次，将模型请求、调用预算和等待上限移入运行详情；审查映射首项默认展开，读取按钮增加加载/成功/失败反馈并取消桌面空白最小高度；公开风险兼容对象和字符串化对象，提取可读消息并翻译 OMML 数量断言。
- trigger: 本机交互测试和经用户授权的局域网只读样本发现，54 个任务的可筛选状态总数只有 53 且暂停任务无入口，失败任务阶段路径重复，运行卡片主状态混入技术统计；审查映射实际已加载却呈现大块空白，风险区直接显示后端对象结构。
- invariants: 仅调整前端筛选、展示层级、文案格式化、折叠默认态、按钮反馈和静态合同测试；不修改任务状态、暂停/继续/取消/重试行为、质量门、检查点、模型调用、计费、用户数据或移动端；`delivery_ready=true` 的需复核任务继续允许下载正式交付包；局域网样本审查拒绝所有写请求。
- do_not_regress: 每种实际任务状态必须有可发现的筛选入口且统计可对账；加载按钮不得把成功或失败只写入隐藏技术区；审查映射加载后首屏必须出现可读内容；公开风险不得泄露字典、内部字段或 OMML 断言；运行主状态不得被调用预算和等待上限淹没。
- verification: `python3 -m pytest -q tests/test_frontend_contract_guards.py tests/test_task_result_checkpoint_view.py tests/test_render_delivery_consistency.py tests/test_practice_redesign.py` 175 passed；`node --check web/app.js`、`node --check web/task-contract-ui.js`、HTML 589 个 ID 唯一性检查和 `git diff --check` 通过；1440×1000 内置浏览器复验已暂停筛选、可读风险摘要和默认展开审查映射；局域网只读样本覆盖 54 个任务及运行、暂停、失败、等待确认、部分完成、配置阻断、需复核、已完成和 20 项长蓝图场景，未执行任何远程写操作、真实下载或模型调用。

### OPT-20260830-15｜终止态卡片与结果页状态层级收敛

- status: verified
- scope: 桌面端任务管理终止态卡片、真题需复核结果页、专项训练部分完成与已完成结果页
- changed: 任务卡当前阶段与状态标签完全相同时不再重复展示；真题结果页将交付结论改为通栏提示并压缩指标卡，对公开风险文本去重并使用下游风险标题；专项训练部分完成态改为“部分题目已生成”，隐藏重复的顶部状态摘要，并将超长训练目标限制为最多三行。
- trigger: 1440×1000 实页检查发现终止态卡片重复显示同名状态，真题需复核页的结论提示像居中的小岛且风险文案重复，部分完成专项训练同时出现“存在未完成题目”和“整套专项补强已生成”，长训练目标挤压主要操作。
- invariants: 仅调整前端文案选择、展示条件、桌面样式和静态合同测试；不修改任务状态、完成度合同、质量门、检查点、继续或重试动作、模型调用、计费、用户数据或移动端；`delivery_ready=true` 的需复核任务继续允许下载正式交付包。
- do_not_regress: 相同状态不得在任务卡或结果页相邻层级重复表达；部分完成结果不得使用整套完成语义；需复核风险不得因源数据重复而重复展示；长目标不得挤压桌面首屏主要操作；需复核正式交付下载权限不得改变。
- verification: `python3 -m pytest -q tests/test_frontend_contract_guards.py tests/test_task_result_checkpoint_view.py tests/test_render_delivery_consistency.py tests/test_practice_redesign.py` 174 passed；`node --check web/app.js`、`node --check web/task-contract-ui.js`、HTML 588 个 ID 唯一性检查和 `git diff --check` 通过；1440×1000 内置浏览器复验任务列表、真题需复核结果、专项训练部分完成与已完成结果页，部分完成态只保留一处可操作状态且标题为“部分题目已生成”，需复核正式交付下载仍可用；未执行移动端、真实下载、任务状态写入或模型调用。

### OPT-20260830-14｜任务列表等待卡与管理工具降噪

- status: verified
- scope: 桌面端任务管理列表、等待确认任务卡、排序与多选管理工具栏
- changed: 等待确认卡只保留一处状态标签和一条下一步说明，隐藏重复的阶段文字与右侧提醒徽标；将原本独占一行的多选管理并入筛选摘要和排序工具栏，并验证普通态与多选态的桌面排版。
- trigger: 1440×1000 实页检查发现等待确认任务在同一卡片重复出现“等待我确认 / 等待确认 / 等待你的确认 / 等待你确认后继续”，同时多选管理单独占据一层工具条，形成无意义留白和层级噪声。
- invariants: 仅调整前端结构标记、桌面样式和静态合同测试；不修改任务状态、确认动作、审查入口、批量选择与删除逻辑、检查点、质量判断、下载权限、模型调用、计费、用户数据或移动端；`delivery_ready=true` 的需复核任务继续允许下载正式交付包。
- do_not_regress: 同一任务状态不得在一张卡片内用多个徽标和阶段文本反复表达；低频批量入口不得无理由独占整行；排序与多选展开态必须在常规桌面宽度保持同一工具层级且不遮挡操作。
- verification: `python3 -m pytest -q tests/test_frontend_contract_guards.py tests/test_task_result_checkpoint_view.py tests/test_render_delivery_consistency.py tests/test_practice_redesign.py` 173 passed；`node --check web/app.js`、`node --check web/task-contract-ui.js`、HTML 588 个 ID 重复检查和 `git diff --check` 通过；1440×1000 内置浏览器复验普通列表及多选展开态，等待确认卡只保留单一状态标签，排序和多选控制保持同一行；未执行移动端、真实确认、删除、下载或模型调用。

### OPT-20260830-13｜桌面操作型标题与监控层级统一

- status: verified
- scope: 桌面端真题解析任务详情页标题区、运行监控首屏信息层级
- changed: 将带返回操作的任务标题区改为“返回按钮—标题内容”的左对齐双栏结构，与结果页、配置页的操作型页头保持一致；运行监控卡片内的重复标题改为“服务概况”，将访问地址收进默认折叠的服务技术信息。
- trigger: 用户截图与 1440×1000 实页复现显示，原任务页把返回箭头计入居中 flex 组，导致标题文字被向右挤偏；继续逐页检查发现运行监控在相邻层级重复显示“运行监控”，并在首屏直接暴露技术访问地址。
- invariants: 仅修改桌面端 CSS 排版和对应静态合同测试；不修改返回事件、页面导航、任务状态、检查点、下载权限、模型调用、计费、用户数据或移动端；`delivery_ready=true` 的需复核任务继续允许下载正式交付包。
- do_not_regress: 带返回操作的桌面任务页不得继续使用“箭头 + 居中标题”的混合语法；返回控件必须与左对齐标题形成清晰的一组；页面标题与首张内容卡不得重复使用同一标题，技术地址不应抢占普通用户首屏。
- verification: 锁定 Python 3.11 完整本地门禁通过：pytest 与覆盖率轮次均为 1914 passed、16 deselected，整库分支覆盖率 70%，版本一致性、Python 编译、公式转换、许可证、项目完整度、Ruff 和 Mypy 全部通过。首轮覆盖率测试发现模型并发上限用例依赖 25ms 调度窗，改为有界同步后覆盖率下连续 6 次定向通过，最终整轮通过；生产并发逻辑未改动。未发起真实付费模型或生图请求；Git 索引源码包、隔离启动、远程门禁、公开附件和稳定更新源待后续核验。

### OPT-20260830-12｜准备 0.9.37 生成恢复与桌面体验版本

- status: verified
- scope: 版本元数据、用户发布说明、模糊 400 恢复、灵算 Gemini 3.7 配置、生成图片语言合同、桌面任务/审查/交付页和源码 ZIP 发布候选
- changed: 将 `APP_VERSION`、`VERSION`、发布清单和 `CHANGELOG.md` 同步升级为 0.9.37；发布说明覆盖错误四态与单题恢复、Gemini 3.7 单选迁移、中文生成图标注合同，以及桌面任务状态、蓝图审查、正式交付和长页布局修复；将模型并发上限测试的 25ms 调度窗改为有界双请求重叠同步，避免覆盖率仪器下的假阴性。
- trigger: 用户确认本地实现后要求推送新版本，并明确不部署用户机；本地 `main` 尚为 v0.9.36，需把已验证的同一批未发布改动一并冻结为新补丁版。
- invariants: 只由受保护的 `main` 自动工作流创建标签和公开附件；不连接或部署用户机；不因模糊 400 全局阻断或静默换模型；无后缀 Gemini 3.7 未实测工具能力不得冒充已验证；源码包不得包含 API Key、任务、教材、日志、输出或本地配置。
- do_not_regress: 不得人工创建或推送标签；不得遗漏未跟踪的回归测试；必须在推送前跑完锁定 Python 3.11 完整门禁、从 Git 索引打包并反向验证、隔离数据目录启动；必须核验远程提交、自动标签、公开 ZIP/SHA256 和稳定更新源后才能宣布完成。
- verification: 锁定 Python 3.11 完整本地门禁通过：pytest 与覆盖率轮次均为 1914 passed、16 deselected，整库分支覆盖率 70%，版本一致性、Python 编译、公式转换、许可证、项目完整度、Ruff 和 Mypy 全部通过。首轮覆盖率测试发现模型并发上限用例依赖 25ms 调度窗，改为有界同步后覆盖率下连续 6 次定向通过，最终整轮通过；生产并发逻辑未改动。40 个待发布文件的高风险凭据、私钥、本机路径和用户机地址扫描 0 命中。Git 索引源码包包含 387 个白名单文件，反向验证 0 问题；解压后使用独立数据目录启动，`/api/version` 返回 0.9.37、首页 HTTP 200，随后正常停止。未发起真实付费模型或生图请求；远程门禁、公开附件和稳定更新源待推送后核验。

### OPT-20260830-11｜异常任务状态与任务管理首屏降噪

- status: verified
- scope: 桌面端任务管理、真题解析失败/暂停/取消/等待确认详情、模拟出题后台加载标识
- changed: 失败诊断横幅只在“未完成”筛选中出现，并压缩状态统计、筛选与多选工具的纵向占用；任务详情按终止或等待状态切换页头、说明、总体进度标签和阶段标题，失败与暂停使用静态状态配色；模拟出题加载页将完整任务 ID 折叠到“查看任务标识”。
- trigger: 1440×1000 实页审查发现全部任务首屏被与当前筛选无关的失败诊断横幅和多层工具占用，失败详情仍提示任务会持续执行、使用运行中蓝紫进度和“正在做什么”，加载页直接暴露完整内部任务 ID。
- invariants: 不修改任务状态、进度计算、失败原因、检查点、继续/取消/重试动作、生成请求、质量判断、下载权限、模型调用、计费或用户数据；`delivery_ready=true` 的需复核任务继续允许下载正式交付包；不调整移动端。
- do_not_regress: 失败、暂停、取消和等待确认页面不得使用“仍在持续执行”的运行中语义；终止态进度不得伪装成活动动画；技术任务标识默认不应抢占普通用户视线；失败诊断提示不得在无关筛选中长期占据首屏。
- verification: `python3 -m pytest -q tests/test_frontend_contract_guards.py tests/test_task_result_checkpoint_view.py tests/test_render_delivery_consistency.py tests/test_practice_redesign.py` 172 passed；`node --check web/app.js`、`node --check web/task-contract-ui.js` 通过；1440×1000 浏览器复验全部任务、未完成筛选和失败任务详情，确认全部任务首屏不再显示失败横幅、任务列表提前进入首屏，失败详情显示“任务未完成 / 流程停止位置”及静态停止态配色；当前无真实 running/queued/paused 任务，未执行移动端或真实模型调用。

### OPT-20260830-10｜灵算 Gemini 3.7 Flash 统一为单一路由

- status: verified
- scope: 灵算 Google 模型配置、旧本地配置迁移、模型能力登记、模型选择与主模型工具能力守卫
- changed: 将 `gemini-3.7-flash-low/medium/high` 三个公开选项统一为一个 `gemini-3.7-flash`，采用与 3.6/3.5 相同的 Chat Completions、`thinking_minimum=minimal` 及采样参数省略规则；加载旧 `providers.local.json` 时删除三个旧别名，并将旧默认模型和视觉模型迁移到无后缀模型；能力表不继承旧别名的工具调用验证结论。
- trigger: 用户要求 Gemini 3.7 Flash 与 3.6 Flash 使用相同的单模型选择逻辑，不再把推理深度显示为三个模型。
- invariants: 不修改灵算 API Key、3.6/3.5 模型路由及现有用户数据；不得把旧别名的真实调用证据转移给尚未实测的无后缀路由；无后缀路由未完成真实工具探测前，不得开启主模型自主生图工具循环；旧历史记录保持可读。
- do_not_regress: 旧本地覆盖配置不得重新显示 low/medium/high 三项；旧默认/视觉模型不得迁移成不存在的值；界面与 API 只能公开一个 Gemini 3.7 Flash 选项；不得伪造无后缀路由的结构化输出、工具调用或任务质量验证状态。
- verification: `python3 -m pytest -q tests/test_lingsuan_provider_config.py tests/test_model_capability_registry.py tests/test_google_ai_provider.py tests/test_practice_image_route_persistence.py tests/test_model_protocol_profiles.py` 40 passed；`python3 -m pytest -q` 1913 passed、16 deselected；两个 JSON 配置通过 `python3 -m json.tool`，能力文档通过生成器同步，`git diff --check` 通过；未发起真实灵算模型调用，无后缀路由仍标记待验证。

### OPT-20260830-09｜生成阶段图片文字语言统一

- status: verified
- scope: 真题/题目解析单题与批量答案生成、审查与 DOCX 回修、作图代码生成/修复、主模型图片工具修复、按题与知识点生题及题图修复、共用答案片段辅助入口
- changed: 新增一个共享且幂等的主模型输入注入器，仅在生成或修改用户内容的调用中要求图片标题、说明和自然语言标注与题目语言一致，中文题优先简体中文，同时保留数学符号、字母标记、公式、单位和专业缩写；JSON、纯文本与多模态消息均在当前请求中最多注入一次，修复/再生成仍保留该要求；相关提示词合同升级为 v2。
- trigger: 中文用户的生成图偶发无必要的英文标题；用户要求在主模型生成答案/题目时直接写清要求，失败后主模型修改或再生成时同样保留，且覆盖生题、解析和共用辅助工具。
- invariants: 不增加 OCR、图片验收、生成后审查或额外模型调用；不要求每题必须生图；不修改材料识别、蓝图规划、语义审查、连接测试、模型路由、推理强度、图片像素或黑白打印规则。
- do_not_regress: 不得把该要求扩展为全局 system 提示词或后置硬审查；同一模型请求已有等价要求时不得重复追加；修复或再生成不得丢失该要求；不得翻译或改写必要的专业符号、公式、单位和缩写。
- verification: 生成、回修、幂等去重、多模态保留、练习生成/题图修复与非生成阶段隔离定向回归 248 passed；受影响文件 Ruff、`py_compile`、新增共享模块与提示词注册表 Mypy、公式转换、第三方声明、77 文件项目完整性与 `git diff --check` 通过；完整门禁的 Python 编译和版本一致性通过，全量 pytest 为 1912 passed、1 failed、16 deselected，唯一失败是本轮前已存在的灵算 Google `gemini-3.7-flash` 未完成公开配置/能力表同步，与本变更路径无关；未发起真实计费模型或生图请求。

### OPT-20260830-08｜出题审查页上下文与长文本排版修复

- status: verified
- scope: 桌面端任务管理、按题出题蓝图审查、知识点出题入口与范围确认
- changed: 等待确认任务卡移除重复的分散进度三段；打开已保存的范围/蓝图审查任务时，首屏可见面板不再触发自动滚动；训练目标、目标能力、变化方式、难度实现和必考知识点改为可换行文本框，蓝图编辑桌面网格改为两列并让长字段占满整行；知识点入口只显示可读模型简称。
- trigger: 1440×1000 实页审查发现任务卡提示横跨三处且重复，任务管理进入审查页后停在约 349px 的页面中段，蓝图长文本被单行输入框截断且三列网格产生半页空洞，知识点入口暴露并截断原始模型 ID。
- invariants: 不修改任务状态、确认条件、生成请求、蓝图内容、模型选择、下载权限、质量判断、调用次数、计费或用户数据；`delivery_ready=true` 的需复核任务继续允许下载正式交付包；不调整移动端。
- do_not_regress: 从任务管理打开中间审查页不得跳过标题和步骤上下文；长段落不得再使用只可横向查看的单行控件；蓝图长字段不得无理由只占半页；普通入口不得暴露原始模型 ID；等待确认卡不得重复四次表达相同状态。
- verification: `python3 -m pytest -q tests/test_frontend_contract_guards.py tests/test_practice_redesign.py tests/test_task_result_checkpoint_view.py tests/test_render_delivery_consistency.py` 171 passed；`node --check web/app.js`、`node --check web/task-contract-ui.js` 与 `git diff --check` 通过；1440×1000 浏览器复验任务管理、按题蓝图、知识点入口和知识点范围页，从任务管理打开两类审查页 scrollY 均为 0，长字段完整换行且模型摘要为“Gemini 3.6 Flash”；未执行移动端、真实确认或模型调用。

### OPT-20260830-07｜桌面操作页空洞与首屏排版修复

- status: verified
- scope: API Key 配置、运行监控、Word 格式审查、教材管理与真题材料选择的桌面端布局
- changed: API 平台展开卡改为横跨双列；运行监控只在 1100px 以下退化为单列；Word 工具压缩纯纵向留白并让 44px 主操作完整进入 1000px 高首屏；单本教材在教材管理和材料选择页使用受控宽度居中展示。
- trigger: 1440×1000 实页审查发现 API 展开态右侧留下半屏空洞，监控页因 1500px 断点过早单列产生纵向空白，Word 工具主按钮位于首屏之外，单本教材被孤立在三列网格左侧。
- invariants: 只修改桌面端样式，不修改任务状态、质量判断、下载权限、按钮事件、文件选择、教材索引、模型调用、计费或用户数据；`delivery_ready=true` 的需复核任务继续允许下载正式交付包；不调整移动端。
- do_not_regress: 常规桌面宽度不得把可并列的监控空状态过早堆叠；展开式编辑区不得留下同等面积的空白列；首屏已完成配置的工具不得把唯一主操作挤出视口；单个网格项不得无理由贴在超宽容器一角。
- verification: `python3 -m pytest -q tests/test_frontend_contract_guards.py tests/test_practice_redesign.py tests/test_task_result_checkpoint_view.py tests/test_render_delivery_consistency.py` 170 passed；Word/监控相关扩大回归 124 passed；`node --check web/app.js`、`node --check web/task-contract-ui.js` 与 `git diff --check` 通过；1440×1000 浏览器逐页复验 API 展开态、运行监控、Word 工具、教材管理和材料选择，Word 主按钮实测 top 898px、bottom 942px、高 44px；未执行移动端和真实模型调用。

### OPT-20260830-06｜结果页按钮与整页排版修复

- status: verified
- scope: 桌面端结果页检查条件与下载交付包操作区
- changed: 为两个裸按钮补回现有设计系统的主次按钮组件，增加桌面端 44px 高度、内边距、12px 间距、字重和主操作最小宽度；将占用 224px 的居中结果标题压缩为左对齐信息头；将与长正文等高的题目侧栏改为限定高度的吸顶列表，并把题目项从夸张胶囊改为现有卡片圆角。
- trigger: 用户截图指出两个按钮显示为紧贴的文字胶囊；浏览器测量确认二者高度仅 24px、内边距为 0，未应用标准按钮组件。继续检查整页发现结果标题占 224px、正文到首屏 759px 才开始，题目侧栏被正文拉到 4627px 高，形成大面积无效留白。
- invariants: 不修改按钮可见性、启用条件、点击处理、任务状态、质量判断或下载权限；`delivery_ready=true` 的需复核任务继续允许下载交付包；本轮不调整移动端。
- do_not_regress: 结果页主要操作不得再使用无组件类的裸按钮；桌面首屏不得被装饰性标题卡过度占用；长正文不得把短侧栏拉成整页空白色块；视觉审查必须检查实际尺寸、内边距、间距、留白、对齐、禁用态和主次层级，不能只检查文案与流程。
- verification: 前端状态/结果定向回归 169 passed，`node --check web/app.js`、`node --check web/task-contract-ui.js`、HTML 582 个 ID 零重复及 `git diff --check` 通过；1440×1000 浏览器实测按钮由 24px/零内边距修复为 44px/10×18px，按钮间距 12px，结果标题由约 224px 压缩到 119px，内容面板起点由约 759px 提前到 646px，题目列表限定为 882px 并在滚动时稳定吸顶；需复核任务下载按钮仍为启用状态；未执行移动端。

### OPT-20260830-05｜撤回前端交付权限误判

- status: verified
- scope: 需复核任务的正式交付包按钮与任务质量标签
- changed: 撤回将正式交付包错误绑定到“正式验收通过”的前端判断，恢复使用既有 `delivery_ready` 下载合同；恢复后端提供的“可交付待复核”标签，不再擅自改成“预览待复核”；第三轮审计报告同步删除错误结论。
- trigger: 用户明确说明“需复核”结果允许下载正式交付包，前端审计不得改变产品逻辑；此前把视觉语义判断扩展成下载权限调整，超出了审计授权。
- invariants: 前端只展示既有任务、质量和下载合同，不自行收紧或放宽权限；`completed_with_issues` 在 `delivery_ready=true` 时仍可下载带风险与验收报告的交付包；其他第三轮排版和阅读优化保持不变。
- do_not_regress: 不得依据文案、视觉理解或一般产品惯例改写业务状态、质量门禁、下载权限、任务动作或后端合同；发现疑似逻辑矛盾时只记录并向用户确认。
- verification: 前端状态/结果定向回归 169 passed，`node --check web/app.js` 与 `git diff --check` 通过；浏览器复验“可交付待复核”标签恢复，`delivery_ready=true` 的需复核任务正式交付包按钮为启用状态，提示继续说明可导出 Word、PDF 与复核记录；未执行移动端。

### OPT-20260830-04｜桌面任务状态、正式交付与长题阅读纠偏

- status: verified
- scope: 桌面端任务管理、失败任务页眉、需复核结果下载区与超长单题解析阅读
- changed: 失败、取消、暂停、等待确认和复核任务的页眉改为真实状态；等待确认任务不再显示百分比，部分生成任务改显实际生成题数；结果指标只统计主要 Word/PDF，正式交付包增加就地禁用原因并仅在正式验收通过时启用；复核稿统一称为“预览待复核”；超长单题增加可持续停留的段落导航和减弱动画偏好支持。
- trigger: 第三轮桌面实页审计发现失败任务仍标“解析进行中”，等待确认及部分生成可能显示 100%，过程文件混入主要文件数，正式包禁用缺少原因，长题无法快速定位；进一步实页复核又发现“可预览需复核”任务实际启用了正式交付包且提示“已通过下载条件”。
- invariants: 不修改后端任务状态、生成完成度、质量检查、最终验收报告、单文件预览下载、模型调用、计费或用户数据；只有现有正式验收合同判定通过时前端才启用正式交付包；本轮不做移动端适配与验证。
- do_not_regress: 失败任务不得显示进行中；等待用户操作和未完成生成不得用 100% 暗示完成；主要文件数不得包含复核/过程文件；`completed_with_issues` 不得称为正式可交付或启用正式包；长题导航不得被主导航遮挡或在跳转后消失。
- verification: 前端状态/下载/结果定向回归 169 passed，前端离线资源/任务合同扩大回归 141 passed；`node --check web/app.js`、`node --check web/task-contract-ui.js`、HTML 582 个 ID 零重复及 `git diff --check` 通过；源码服务 8766 在 1440×1000 桌面视口完成浏览器复验，确认等待确认、部分生成、失败页眉、主要文件计数、正式交付门禁及题内导航状态正确；未执行移动端和真实计费生成。

### OPT-20260830-03｜模型错误四态分级与模糊 400 单题恢复

- status: verified
- scope: 模型 HTTP 错误分类、专项练习批量/单题生成、耐久重试状态、历史任务投影与私有调用诊断
- changed: 将调用失败明确分为 `item_failed`、`service_degraded`、`route_blocked`、`configuration_blocked`；无具体错误字段的 400 不再全局阻断，批量失败直接拆为单题，灵算单题仅在原供应商、模型、协议、消息和参数上补偿一次，二次失败只保留当前题并继续后续题；权限和额度耗尽只阻断当前路由；私有诊断补充服务商 `code/type/param/message` 与 request ID，公开错误继续使用脱敏文案。
- trigger: 灵算 Gemini 批量生成中两个仅返回 `Invalid request` 的偶发 HTTP 400 被旧逻辑误判为全局参数配置错误，导致随后七题零调用并统一显示“模型配置不可用”，尽管同一路由在错误前后均有成功请求。
- invariants: 不修改已成功题；不因模糊 400 切换模型、供应商、协议、上下文或生成参数；不降低推理与多模态能力；后备路由仍须用户授权且能力等价；完整请求/响应只进入有容量和保留期限制的私有诊断数据，不进入用户错误或通用运行日志。
- do_not_regress: 通用 `Invalid request` 400 不得设置 `configuration_blocked` 或打开兄弟题熔断；批量模糊 400 不得原样重试整批；非灵算路由不得获得该补偿重试；灵算补偿不得超过一次或改变实际路由/请求；403、额度耗尽不得重新混入配置阻断；确定性 Key、模型/Endpoint 和明确不支持参数仍须快速阻断。
- verification: `python3 -m pytest -q tests/test_provider_errors.py tests/test_practice_generation_retry_budget.py` 48 passed；`python3 -m pytest -q` 1907 passed、16 deselected；`python3 -m py_compile app/provider_errors.py app/exercise_generation.py app/llm_client.py app/runtime_monitor.py` 与 `git diff --check` 通过；Windows 用户机完整备份后部署一致的 0.9.36 运行包和本修复，远端真实 `import app.server` 及模糊 400 状态检查通过，7 个修复文件 SHA-256 与本地一致，计划任务已指向新项目源码启动器，局域网 `/api/version` 返回 HTTP 200 且版本/清单均为 0.9.36；未发起真实计费模型调用。

### OPT-20260830-02｜桌面端创建与结果下载流程降噪

- status: verified
- scope: 首页版本提示、桌面导航、按题/知识点出题入口、结果下载区与最终验收风险说明
- changed: 中等宽度桌面导航保留完整品牌名；首页将内部发布清单状态替换为本机数据保存提示；模拟出题底部操作条从覆盖页面的固定层改为内容末尾操作区，模型摘要仅显示可读模型名；结果下载区移到超长题目正文之前，主要 Word/PDF 与复核/过程文件分组，复核稿 Word 可作为预览回退；最终验收风险复用用户可读诊断映射，不再显示内部字段。
- trigger: 第二轮逐页浏览器审计发现首步输入区被固定操作条遮挡，品牌名在桌面窄窗口被挤断，模型 ID 换行破坏卡片层级，下载入口位于数千像素题目正文之后，过程文件与主要交付文档混排，复核风险仍泄露 `answer_coverage` 和 `evidence_ids`。
- invariants: 不修改任务状态、生成流程、质量门禁、最终验收结论、正式下载权限、模型选择、调用次数、计费和用户数据；复核稿只能标为预览文件；本轮按用户要求不调整移动端。
- do_not_regress: 桌面输入表单不得被全局固定操作条遮挡；默认页面不得再次显示发布清单、原始模型 ID 或内部质量字段；主要 Word/PDF 必须先于过程文件出现，过程文件折叠后仍须可下载；不得把复核稿称为正式交付文件。
- verification: `python3 -m pytest -q tests/test_frontend_contract_guards.py tests/test_practice_redesign.py tests/test_task_result_checkpoint_view.py tests/test_render_delivery_consistency.py` 169 passed；`node --check web/app.js`、`node --check web/task-contract-ui.js` 和 `git diff --check` 通过；源码服务 8766 启动成功，浏览器复验按题出题、知识点出题、API Key 配置、Word 格式审查、任务结果与过程文件折叠，确认遮挡、换行、下载层级和内部文案问题已消除；未执行移动端和真实计费生成。

### OPT-20260830-01｜桌面端任务与交付信息降噪

- status: verified
- scope: 桌面端任务管理、任务诊断、结果交付结论、教材管理、环境自检、运行监控与工作流顶部导航
- changed: 将任务筛选和状态文案改为“等待我确认、结果需复核、未完成”等用户结果语义，保留状态值和完成度计算不变；结果页新增“可正式交付、可预览但需复核、当前不可交付”首屏结论，并让复核任务至少显示一条复核提示；将内部门禁代码和常见英文诊断转换为用户可读说明，原文件路径与日志仍保留在技术详情；教材格式、共享教材库、Pydantic 观察、主机名和进程号默认折叠；任务/结果页取消第二层步骤栏吸顶，避免长页滚动时双层遮挡。
- trigger: 全页面视觉与体验审计发现任务状态同时使用完成、失败、待处理和 100% 容易误判，结果页缺少单一交付结论，技术实现词和英文门禁直接面对普通用户，教材与监控首屏信息密度过高，任务/结果长页存在双层吸顶占用阅读空间。
- invariants: 不修改后端任务状态、进度里程碑、质量门禁、最终验收、下载权限、Pydantic 影子观察行为、模型调用、计费和用户数据；`completed_with_issues` 仍不得被描述为正式验收通过；本轮按用户要求不调整移动端。
- do_not_regress: 不得在默认诊断视图重新暴露门禁代码、英文内部错误、文件路径或日志事件；不得只凭 100% 暗示任务成功；结果页必须先给出唯一交付结论，且正式交付只能由既有最终验收合同决定；技术详情折叠后不得删除其原始排障数据或功能入口。
- verification: `python3 -m pytest -q tests/test_frontend_contract_guards.py tests/test_practice_redesign.py tests/test_task_result_checkpoint_view.py tests/test_render_delivery_consistency.py` 169 passed；`node --check web/app.js`、`node --check web/task-contract-ui.js`、HTML 578 个 ID 零重复及 `git diff --check` 通过；源码服务在 8766 启动成功，浏览器逐页复验任务管理、结果页、失败诊断、环境自检、教材管理和运行监控，确认状态投影、交付结论、技术信息折叠和桌面布局正常；未执行移动端验证。

### OPT-20260829-24｜准备 0.9.36 运行时可靠性版本

- status: verified
- scope: 版本元数据、用户发布说明、模型/工具观察、不可变资产、上下文计量、更新网络恢复和源码 ZIP 发布候选
- changed: 将 `APP_VERSION`、`VERSION`、发布清单和 `CHANGELOG.md` 同步升级为 0.9.36；发布说明覆盖耐久调用/重试事件、提示词合同、任务/不变量只读投影、内容寻址资产、Token 计量与安全压缩，以及 Windows 10054 更新重试和附件续传。
- trigger: 用户要求把已验证的更新器修复推送为新版本；同一发布工作树还包含 0.9.35 后已完成并具备完整门禁证据的运行时可靠性改动，需要统一冻结、复验和发布，避免漏发未跟踪依赖模块。
- invariants: 只由受保护的 `main` 自动工作流创建标签和公开附件；观察层不得新增付费调用、改写任务状态或提示词；压缩不得触碰题干、答案约束、确认依据、成功工具结果或图片像素；源码包不得包含 API Key、任务、教材、日志、输出、本地配置或质量报告。
- do_not_regress: 不得人工创建或推送标签；不得遗漏新模块或把未跟踪运行数据混入包；不得把本地门禁、Git push 或 CI 入队表述为正式公开发布；必须核验公开 ZIP、SHA256 和稳定更新源后才能宣布完成。
- verification: 锁定 Python 3.11 最终完整门禁通过：pytest 与覆盖率轮次均为 1899 passed、16 deselected，整库分支覆盖率 70%，版本一致性、Python 编译、公式转换、Ruff、Mypy、许可证和 77 文件项目完整性检查全部通过；46 个待发布文件敏感凭据扫描 0 命中，`git diff --check` 通过。Git 索引源码包包含 387 个白名单文件且反向验证 0 问题；解压后使用独立数据目录启动，`/api/version` 返回 0.9.36，首页及不变量、资产、Token、提示词、执行投影五个只读端点均 HTTP 200。未发起真实付费模型或生图请求；Git 提交、远端门禁、公开附件和稳定更新源待推送后核验。

### OPT-20260829-23｜不变量、不可变资产与安全上下文压缩内核

- status: verified
- scope: 三类业务共用的模型上下文计划、主模型图片工具循环、真题最终交付、练习历史保存、图片/诊断资产完整性及只读质量诊断 API
- changed: 新增只读 InvariantService，统一聚合调用意图/结果、重试配对、提示词注册覆盖、任务投影及资产摘要事实，并严格区分状态矛盾、证据缺口和覆盖缺口；新增不可变 Artifact 元数据与摘要审计，图片文件和 manifest 原子落盘并 fsync，写后/读时/最终采用时重新核验 SHA256，真题最终验收和练习耐久保存只标记实际进入最终结果的资产，诊断附件保持原容量和成功 30 天/失败 60 天策略；新增完整 Token Meter，计量文字、图片、工具 schema、历史工具结果和结构开销，并按服务商/模型使用 provider-reported usage 中位比例校准。对照当前 Codex context window 与 DeepSeek Harness compaction 合同，预算压力下只确定性裁剪较旧失败工具结果正文，保留最近两条失败、调用/结果身份和全部核心内容，不执行模型摘要。新增三个只读质量端点。
- trigger: 原字符估算未计工具 schema、历史结果和图片成本，资产 manifest 缺少 fsync、读取摘要复核及最终采用状态，确定性规则又散落在调用、任务和交付模块；直接用新规则阻断或压缩题干/证据可能降低答案质量和完成率，因此统一测量与事实层，同时只启用可证明非核心且可保持工具配对的失败结果裁剪。
- invariants: InvariantService 始终 `enforced=false`、不改业务状态；现有上下文质量预算、模型/协议/重试/取消/质量门不变；题干、答案约束、确认教材/真题证据、用户消息、成功工具结果、图片像素和采用资产不得压缩；压缩不得增加模型调用、Token 或网络请求；诊断数据继续执行容量上限与成功 30 天/失败 60 天保留；摘要不一致资产不得进入读取或最终采用，但元数据观察失败不得让已合格用户结果丢失。
- do_not_regress: 不得把老账本缺字段报成真实业务失败或宣称影子不变量已避免失败；不得信任 manifest 外部路径、只按文件存在判断资产可用、在最终质量边界前标记 final adopted，或用不可变文件名掩盖摘要不一致；不得把 provider 官方最大上下文当作平台质量组装目标；扩大压缩到成功结果、题目/答案/证据或模型摘要前，必须用固定真实任务语料验证质量与完成率并取得用户确认。
- verification: 新增不变量、资产、Token Meter、HTTP 合同和真实工具循环压缩边界，加上答案/练习/图件/任务恢复相关定向 118 passed；模型调用、答案生成、练习生成、图件、最终交付、任务恢复及新统一层扩大回归 417 passed。本机只读审计：2296 条模型执行事件中 897 个意图与 897 个结果完全配对，只有 679 条旧意图缺提示观察和 212 条测试/未登记观察覆盖缺口，无状态矛盾；10 个 manifest 的 34 个图片资产与 191 个诊断附件摘要 0 违规；47 条模型路线中 18 条已有至少 3 个供应商 usage 校准样本。最终工作树完整 `python3 scripts/run_quality_gates.py --full` 通过，pytest 与覆盖率轮次均为 1899 passed、16 deselected，整库分支覆盖率 70%，Python 编译、版本一致性、公式、许可证、项目完整度、Ruff 和 Mypy 全部通过；首次完整门禁只发现既有更新测试的空 HTTP header 类型不符合 Mypy，改用等价 `Message()` 后该测试 20 passed 且不改变运行行为。`git diff --check` 通过。未发起真实计费模型或生图请求，未据此宣称固定真实任务语料质量无损。

### OPT-20260829-22｜更新检查重试与附件安全续传

- status: verified
- scope: 普通用户检查更新、稳定清单读取、源码 ZIP 下载与更新失败提示
- changed: 稳定清单读取对连接重置、超时、限流和 GitHub 5xx 执行有限指数退避，Raw GitHub 多次失败后切换同仓库 GitHub Contents API；源码附件下载在瞬时断线后用 Range 续传，服务器忽略 Range 时从头覆盖重下，重试耗尽删除 `.part`。所有恢复路径继续执行声明大小和 SHA256 强校验。
- trigger: Windows 用户检查 0.9.35 时遇到 `[WinError 10054]`，公开清单与附件正常，但旧客户端单次 `urlopen` 立即把区域网络连接重置升级为更新失败；附件下载也存在同类单请求失败面。
- invariants: 备用清单必须属于配置中的同一 GitHub 仓库、分支和文件；404、权限及非瞬时 HTTP 错误不得盲目重试；任何不完整、超限、大小不符或 SHA256 不符的附件不得进入替换计划；不得触碰 API Key、任务、教材、日志和输出。
- do_not_regress: 不得重新退回单次 Raw 请求；不得用第三方镜像或重试绕过 HTTPS、仓库/标签/文件名白名单、大小和 SHA256；服务器不接受 Range 时不得把完整响应追加到旧片段；重试耗尽必须清理未验证临时文件并给出可理解提示。
- verification: Windows 10054 有限重试、404 不重试、Raw→同仓库 Contents API、Range 续传、Range 被忽略后重下及重试耗尽清理定向 20 passed；更新、前端合同、源码启动器及发布工作流扩大回归 139 passed；受影响文件 Ruff、Mypy 通过。程序自身真实读取公开 Raw 与 GitHub API 清单均返回 0.9.35 且 SHA256 一致；新下载器从公开 Release 下载 14,874,246 字节正式 ZIP，SHA256 `34b0825f81c4b5449b757b018b8d125fc29cf4c23403abbe25d4524df6aaac90` 校验通过；独立数据目录启动服务后 `/api/version` 与 `/api/update/status?refresh=1` 均 HTTP 200。整库日常门禁通过：1888 passed、16 deselected，Python 编译、版本一致性、公式、许可证和 77 文件项目完整度全部通过；未执行正式发布候选完整 Ruff/Mypy/覆盖率门禁，未提交、推送或发布新版本。

### OPT-20260829-21｜提示词合同注册与影子调用关联

- status: verified
- scope: 真题解析/题目解析、按题出题/知识点出题、内容/Word/选择性审查修复、传统与主模型图片链路、模型执行事件及质量诊断 API
- changed: 新增只读 Prompt Section Registry，为系统连接探测、题意理解、知识规划、依据选择、图规规划、答案单题/批量生成、依据绑定审计、内容/Word/选择性审查修复、作图代码/视觉复核、练习材料分析/蓝图/生成/语义复核/单题重做及图片工具等登记 24 个稳定 `prompt_id`、显式版本、适用任务 Profile、固定 section 顺序、输出合同和消费者；无教材 Profile 显式声明关闭 `textbook_evidence` section。所有业务模型入口在原调用外层附加上下文身份，最终网络意图事件只记录合同、传输结构、消息角色、体积和 SHA-256，不保存提示词、题目、教材、图片或响应正文；协议适配、JSON 修复和工具多轮继续继承同一合同，图片工具调用临时切换为独立图片合同后恢复父合同。新增只读聚合端点和静态覆盖守卫；观察器异常只标记报告不可用，不阻断请求。
- trigger: 现有提示词分散在答案、练习、图件和审查文件中，但其中无教材、视觉证据、内容修复、Word 修复和练习各阶段的差异承担不同质量责任；直接合并可能让无教材任务重新被要求教材依据，或让修复阶段丢失最新候选、图片工具和输出协议。先注册身份与差异，才能用后续真实调用证据判断哪些是必要分叉、哪些是漂移。
- invariants: 不改变任何提示词正文、消息顺序、证据内容、工具 schema、输出 schema、模型/协议/Thinking、上下文预算、Token、重试、并发、质量门或业务状态；注册观察不得增加模型调用、Token、网络请求或自动修复；无教材任务继续关闭教材内容及教材型校验，真题任务继续保留教材证据；观察或报告失败必须 fail-open，模型执行意图账本自身的既有 fail-closed 耐久边界不变。
- do_not_regress: 不得把 `prompt_id` 当作已接管文本拼装；不得因合同名称相近合并答案生成、内容修复、Word 修复、练习生成或视觉审查；不得记录提示词/响应正文或明文任务标识；新增业务模型入口必须声明注册合同；在现有手工拼装迁入确定性 section assembler、真实新调用覆盖主要合同且固定真实任务语料完成质量审查前，不得强制 section 顺序或用注册表改写请求。
- verification: 新增注册表、隐私/不变性、嵌套工具合同、观察器 fail-open、旧账本区分、只读 HTTP 端点及业务入口覆盖守卫 9 passed；模型执行账本、Chat/Responses/Anthropic 协议、答案生成与审查修复、依据/题意/知识/图规规划、专项练习分析/规划/生成/重做、图片工具与图件质量扩大回归 311 passed。现有本机执行账本含 679 条功能上线前的旧调用意图、0 条新提示词观察，报告如实保留为无覆盖证据且不读取提示词正文。最终工作树完整 `python3 scripts/run_quality_gates.py --full` 通过，pytest 与覆盖率轮次均为 1882 passed、16 deselected，整库分支覆盖率 70%，Python 编译、版本一致性、公式、许可证、项目完整度、Ruff 和 Mypy 全部通过；新模块单独 Mypy、受影响文件 Ruff 与 `git diff --check` 通过。未发起真实计费模型或生图请求，未据此宣称真实任务提示词质量无损或注册表已经接管拼装。

### OPT-20260829-20｜任务状态与进度只读影子投影

- status: verified
- scope: 真题解析/题目解析任务、按题出题/知识点出题后台任务、任务事件、模型执行事件及质量诊断 API
- changed: 新增只读执行投影，将生命周期结果、内容单元完成度和前端展示进度拆为三个独立维度；统一复用既有考试阶段进度与练习活动进度算法，不改变公开百分比。报告区分真实状态矛盾、终止态 100% 展示歧义、内容完成后质量/文档失败的合法阶段边界、成功结果计数陈旧及事件证据缺口；练习成功任务可用耐久结果中的题目数量旁证陈旧计数，但不回写快照。新增只读质量端点，仅返回聚合计数和任务标识哈希，不包含题目、提示词、响应或错误正文。对本机既有 51 条耐久记录的影子投影发现 7 条展示歧义、5 条合法阶段边界、2 条成功结果计数陈旧，未发现内部状态矛盾；历史任务均不具备新模型执行账本覆盖，因此明确拒绝升级为权威投影。
- trigger: 练习任务 API 会把所有终止状态显示为 100%，而考试任务可能在题目单元全部生成后于内容质量、Word 或最终验收阶段失败；直接把百分比当成产出成功会掩盖交付失败，直接重写状态机又可能放过质量门。另有少量成功练习的耐久结果完整但计数器未同步，需要在不降低质量门的前提下分辨计数陈旧与真实缺题。
- invariants: 不改变任务状态、当前阶段、计数器、检查点、恢复、取消先胜、重试、模型路由、提示词、质量门或 UI 展示；`completed_with_issues` 仍是成功终止态，`failed`/`cancelled` 即使内容单元达到总数也不得投影为成功；影子报告不得增加模型调用、Token、网络请求或持久化用户内容；任务标识不得以明文进入聚合报告。
- do_not_regress: 不得用单一百分比同时表达生命周期、内容完成和活动进度；不得把“全部题目已生成”解释为 Word/渲染/验收已交付；不得用耐久结果旁证覆盖失败或取消状态；在考试事件具备连续序列、练习具备生命周期事件流、模型事件覆盖完整任务转换且固定真实任务语料完成质量审查前，不得让影子投影接管业务状态或恢复决策。
- verification: 新增影子投影单元与 HTTP 契约 11 passed；任务存储/恢复、流水线检查点/交付/运行状态/遥测、练习任务/队列/网络控制/续作幂等及公开任务合同扩大回归 142 passed；本机 51 条耐久任务聚合投影为 7 条展示歧义、5 条合法阶段边界、2 条成功结果计数陈旧、0 条真实状态矛盾，且不读取或输出题目/提示词/响应正文。最终工作树完整 `python3 scripts/run_quality_gates.py --full` 通过，pytest 与覆盖率轮次均为 1873 passed、16 deselected，整库分支覆盖率 69%，Python 编译、版本一致性、公式、许可证、项目完整度、Ruff 和 Mypy 全部通过；新模块单独 Mypy、受影响文件 Ruff 与 `git diff --check` 通过。未发起真实计费模型或生图请求，未据此宣称固定真实任务语料质量已验证。

### OPT-20260829-19｜统一重试分类与预算影子事件

- status: verified
- scope: 通用 JSON 生成重试、Responses/Anthropic 到 Chat 的既有协议适配、图片响应格式兼容重试、专项练习传输/JSON 修复重试及模型执行事件账本
- changed: 对照当前 Codex 的传输错误分类、指数退避/抖动与 retry trace，以及 DeepSeek Harness 在等待前写 `llm/retry`、下一请求前写 `llm/retry-started` 的耐久合同，在既有 `model_execution_events.jsonl` 增加 `retry.scheduled`/`retry.started` 影子事件。统一记录重试类别、标准化供应商错误、次数/上限、实际等待与 `Retry-After`、模型/协议/策略变化及既有预算是否扣减；原始供应商错误正文不进入重试事件。调用失败通过异常旁路关联源 `invocation_id`，成功后解析失败通过上下文隔离引用关联，不修改供应商原始响应对象。通用 JSON 计划会把分类器判定不可重试但仍由旧策略继续的场景标为 `policy_retry`；GLM 429 的既有练习预算豁免只被观察，不改变。
- trigger: 原系统在 JSON 报告、练习 attempt log、协议降级元数据和供应商错误分类器中分别保存部分重试事实，无法统一回答“为什么重试、等待多久、是否切换协议/模型、消耗哪一层预算”；直接收紧旧重试可能降低偶发恢复率，因此先建立可比较的影子证据。
- invariants: 不改变任何重试次数、计划顺序、退避/抖动、`Retry-After` 上限、模型与协议选择、GLM 429 豁免、质量门、任务检查点或恢复行为；重试事件不得保存提示词、响应正文、教材/题目或原始错误正文；不得向模型原始响应注入执行元数据；新事件只有观察权威，不能据此宣称重试政策已统一接管。
- do_not_regress: `retry.scheduled` 必须先于等待，`retry.started` 必须在等待结束后紧邻下一尝试写入；已有循环若在两者之间执行任务活动性或业务重试预算检查，阻断时不得伪造 started；协议适配必须同时保留原模型并显式记录 from/to protocol；不可重试分类与旧计划冲突必须可见而不得静默改写；后续改变重试策略前必须用固定真实任务语料验证完成率和产出质量。
- verification: 统一事件、HTTPS 边界、协议适配、专项练习预算/恢复、答案路由及模型工具循环扩大回归 313 passed；最终工作树完整 `python3 scripts/run_quality_gates.py --full` 通过，pytest 与覆盖率轮次均为 1862 passed、16 deselected，整库分支覆盖率 69%，Python 编译、版本一致性、公式、许可证、项目完整度、Ruff 和 Mypy 全部通过；受影响文件 Ruff 与 `git diff --check` 通过。未发起真实计费模型或生图请求，未据此宣称真实任务质量无损。

### OPT-20260829-18｜模型调用影子执行账本

- status: verified
- scope: 真题解析/题目解析、按题出题、知识点出题及图片生成共用的模型网络边界、运行监控与费用记账
- changed: 为每次模型网络调用生成统一 `invocation_id`，缺少显式业务批次号的任务在影子事件中获得独立观察 `run_id`，不借此启用既有运行预算；新增独立追加式 `model_execution_events.jsonl`，在网络前同步写入并 `fsync` 调用意图，在返回或异常后写入同身份结果。事件只保存任务/阶段标识、影子路由决策快照、协议与端点路径、能力要求、请求摘要哈希/体积和用量结果，不保存提示词或响应正文；原 `model_calls.jsonl` 继续承担现有费用统计。意图写入短暂重试仍失败时回滚调用预算并禁止网络请求；已返回结果的事件写入失败时结果标记丢弃并抛出不可由客户端自动重试的专用错误；取消后的返回结果记为 `cancelled_discarded`。
- trigger: 原模型费用账本只在调用结束后写入，进程中断时无法区分“尚未请求”和“请求已发出但结果未落盘”，三类业务又缺少统一调用身份及可比较的实际路由快照；直接让新账本接管恢复或业务状态会扩大失败面，因此本阶段只建立耐久影子事实。
- invariants: 不改变提示词、模型选择、协议降级、重试次数、并发、质量门、检查点或业务状态机；影子路由快照不具备恢复权威且明确标记未执行选路政策；模型全文仍只进入既有容量限制及成功 30 天/失败 60 天的诊断存储；账本只保存摘要、哈希和引用元数据；取消后的付费结果不得进入检查点或复用。
- do_not_regress: 任何付费请求不得先于耐久意图事件发出；不得把提示词、教材、题目、响应正文、凭据或带查询参数的端点写入执行账本；不得把当前影子快照宣称为已实现跨进程 replay、路由固定或事件投影权威；结果事件丢失时不得静默采用结果或将专用账本错误包装成可自动重试的普通模型错误。
- verification: 最终聚焦执行账本、运行监控及真实本地 HTTPS 传输夹具 35 passed；模型工具循环、协议适配、模型固定、练习重试/取消及能力路由扩大回归 129 passed；最终工作树完整 `python3 scripts/run_quality_gates.py --full` 通过，pytest 与覆盖率轮次均为 1860 passed、16 deselected，整库分支覆盖率 69%，Python 编译、版本一致性、公式、许可证、项目完整度、Ruff 和 Mypy 全部通过；`git diff --check` 通过。未发起真实计费模型或生图请求。

### OPT-20260829-17｜补齐 Harness 工具执行可靠性合同

- status: verified
- scope: 真题解析/题目解析、按题出题、知识点出题共享的主模型自主生图工具循环，答案与题图审查修复，任务级图片诊断
- changed: 重新逐项复核当前 Codex 图片工具和 DeepSeek Harness 工具注册、执行流水线、重复调用守卫、超时及耐久事件合同；图片工具在付费调用前执行严格运行时参数校验，未知字段和错误类型作为结构化结果回给主模型。新增任务级追加式 `tool_events.jsonl`，耐久记录主模型请求/完成摘要及工具 call/result/提醒；同一答案/修复事务内按 `call_id` + 规范化参数幂等复用，身份冲突显式拒绝；工具异常和调用预算耗尽不再直接终止整题；连续第三次完全相同调用追加 advisory；图片生成/编辑显式继承独立 240 秒请求期限并保留可用的请求 ID/修订提示元数据。接入标准同步记录已采用、串行等价、部分采用和仍未采用的上游合同。
- trigger: 现有闭环已能让主模型自主调用、回看并绑定图片，但仍只依赖模型遵守 JSON schema，工具身份缓存仅在单次 `run_json` 内且未校验参数，失败缺少结构化错误和耐久 call/result，重复付费调用只有总量硬上限；这些差距会让非法参数到达供应商、修复轮重复计费、失败后无法定位调用是否真正发出。
- invariants: 是否需要图片仍只由负责当前内容的主模型决定；无图任务不增加模型轮次或图片调用；真实像素必须回到同一主模型并被明确采用；重复提醒只能建议不能替模型阻断合法重绘；任务工具账本只保存在任务数据目录，不进入源码或通用运行日志；图片失败不得静默降级到传统绘图。
- do_not_regress: 不得再次只靠模型可见 schema 而跳过执行前校验；不得用同一 `call_id` 执行不同参数；不得将未知工具、参数错误、供应商失败或预算耗尽直接变成整题异常而不给主模型修复机会；不得把当前摘要账本宣称为 DeepSeek 的完整跨进程 session replay，当前同步 HTTP 客户端也尚未实现 cooperative cancellation。
- verification: 图片工具循环及三类入口聚焦回归 46 passed；真题答案、审查修复、按题/知识点生题、重复生成和图件交付扩大回归 319 passed；完整 `python3 scripts/run_quality_gates.py --full` 通过，pytest 与覆盖率轮次均为 1854 passed、16 deselected，整库分支覆盖率 69%，Python 编译、版本一致性、公式、许可证、项目完整度、Ruff 和 Mypy 全部通过；受影响文件额外 Ruff 与 `git diff --check` 通过。未发起真实计费模型或生图请求。

### OPT-20260829-16｜准备 0.9.35 自主生图与题目解析版本

- status: verified
- scope: 正式源码版本元数据、用户发布说明、图片专用服务商协议门禁、源码 ZIP 发布候选
- changed: 将 `APP_VERSION`、`VERSION`、发布清单和 `CHANGELOG.md` 同步升级为 0.9.35，发布说明覆盖无教材题目解析、Codex/DeepSeek Harness 对齐的主模型自主生图、参考原图编辑、Gemini/多协议工具闭环、灵算入口修复和方舟 Seedream 图片入口；协议回归显式跳过不提供文字生成的图片专用服务商，避免把 `ark_image` 错当作文本 Chat 服务商。
- trigger: 用户要求将本阶段累计完成的功能推送并发布新版本；完整门禁前检查发现通用文本协议测试遍历了新增图片专用服务商，虽然实际运行路由已隔离，测试合同仍会产生假失败。
- invariants: 只由受保护的 `main` 自动发布流程创建标签和公开附件；源码包不得包含 API Key、任务、教材、日志、输出、本地配置或质量报告；图片专用服务商不得进入文本模型选择和协议推断；发布说明不得把受控探测扩张为全平台付费质量结论。
- do_not_regress: 不得人工创建、移动或复用正式标签；不得为了通过门禁把 `ark_image` 加入文本协议集合；必须在锁定 Python 3.11 环境完成最终代码与版本元数据的完整门禁、Git 索引打包、反向验证和独立数据目录启动后才能推送。
- verification: 锁定 Python 3.11 最终 0.9.35 工作树完整门禁通过：1849 passed、16 deselected、69% 分支覆盖率，版本一致性、Python 编译、公式转换、Ruff、Mypy、许可证和 77 文件项目完整性检查通过；发布说明/工作流/协议定向 49 passed，改动及未跟踪源码共 64 文件敏感凭据扫描 0 命中；Git 索引源码包包含 382 个文件且反向验证 0 问题，使用独立数据目录解压启动后 `/api/version` 返回 0.9.35、首页 HTTP 200；远程质量工作流、公开附件和稳定更新源待推送后核验。

### OPT-20260829-15｜无教材题目解析复用真题核心

- status: verified
- scope: 首页辅助工具、解析任务创建/持久化/恢复、真题解析流水线、答案生成与批量提示、内容/Word 模型回修、覆盖审查、任务管理及 Word 标题
- changed: 新增“题目解析”入口和任务级 `question_only` 配置；入口先进入共享模型选择页，可选择结构化解析、正确性复核、读图和生图模型，只隐藏不会调用的教材知识识别/证据确认模型，再进入选题页。继续使用同一真题解析核心、答案结构、质量审查和交付链，仅跳过教材 Key 校验、教材复制/索引、知识规划、检索与依据绑定。初次答案生成、批量生成、内容模型回修和 Word 模型回修均继承无教材合同，不传教材内容或确认依据；覆盖审查在该配置下不再要求 `evidence_ids`，其他答案、计算、图件、格式和最终验收规则保持不变。旧任务缺少配置时默认有教材模式，无教材任务禁用要求教材包的混合云执行。
- trigger: 用户需要在辅助工具中增加与真题解析持续联动的题目解析能力，但不处理教材；风险不只在入口，还包括空依据被覆盖审查拒绝、回修阶段重新引入教材合同、断点/恢复丢失任务身份和交付文案误导。
- invariants: 题目解析不得复制出第二套真题流水线；真题解析后续共享改动应自动生效。无教材模式不得调用教材知识规划/依据选择模型、不得生成教材索引/检索/依据文件或伪造教材引用；同时不得跳过答案覆盖、内容正确性、计算一致性、图件、Word 和最终验收。历史真题任务行为不变。
- do_not_regress: 不得用前端隐藏代替后端配置；不得在首次生成、批量生成、内容回修或 Word 回修中向无教材任务传入 `textbook_content`/`confirmed_evidence`；不得把空 `evidence_ids` 当成无教材任务缺陷；不得让全局混合执行设置把无教材任务送入教材云协议；不得在题目解析输出和下载提示中宣称存在教材依据排查表。
- verification: 新功能及共享答案/审查/任务合同定向回归 159 passed，补充回修合同后相关定向回归 132 passed，题目解析模型选择流程与前端合同 97 passed；最小无教材流水线确认教材索引、知识规划、检索和依据选择四阶段均为 skipped，且未生成对应三类教材产物。最终全量 1847 passed、16 deselected、2 failed；两项失败均为既有 `ark_image` 图片专用服务商未进入文本协议枚举，与本功能无关且和上一轮全量结果一致。受影响文件 Ruff、Python 编译、`node --check web/app.js` 和 `git diff --check` 通过。

### OPT-20260829-14｜补齐 Codex 原图编辑与生成分流

- status: verified
- scope: 主模型图片工具、OpenAI 兼容图片 API、真题答案与审查修复、按题/知识点生题正式批次、单题草案/重生成及题图视觉修复
- changed: 按当前 Codex `image-generation` 工具合同增加 `referenced_image_paths` 与 `num_last_images_to_include`；两者均未选择时调用图片生成接口，选择任务内原图或本事务最近生成图时调用图片编辑接口，两类选择器互斥且单次最多 5 张。数据 URL 被按原始字节落为任务内文件，本地原图按真实字节直接组成 `image[]` multipart，不缩放、不重编码；生成或编辑结果继续作为真实图片输入返回同一主模型检查。所有答案、生题和修复入口均向工具登记各自隔离范围内的参考图，编辑失败不回退为从零生成。
- trigger: 已实现工具调用和生成图回灌，但工具参数只有 `prompt` 且执行器无条件调用 `/images/generations`；来源原图虽然交给主模型理解，却没有作为像素输入传给图片模型，漏掉 Codex Harness 的 Generate/Edit 前半段。
- invariants: 是否需要图片及是否引用来源图仍由负责当前内容的主模型决定；程序只提供任务内路径、执行主模型明确选择的 Generate/Edit、限制数量和验证资产；无图任务继续零图片调用；新旧作图链路互斥；图片默认黑白/灰度规则不变；不得允许工具读取任务外任意路径。
- do_not_regress: 不得再次把 `referenced_image_paths` 降为提示词文字或只把原图交给主模型而不交给图片模型；有参考图不得调用从零生成接口，图片编辑失败不得静默切换 Generate；最近图片选择只能引用本次工具事务真实生成的图片，不得因磁盘历史 manifest 自动获得资格；答案、按题生题、知识点生题、草案、单题重生成和两类修复入口不得漏传其可用参考图。
- verification: 当前 Codex 官方源码与 OpenAI GPT Image 2 官方图片生成/编辑合同复核完成；图片工具、multipart 编辑接口、原字节保持、选择器互斥/上限、最近图片编辑、生成图回灌及生题参考图入口定向 25 passed；答案/练习/图件/审查相关回归 714 passed；最终全量 1843 passed、16 deselected、5 failed，失败仍为与本改动无关的本机缺少 `math_ml2omml` 3 项和运营者动态 `ark_image` 未进入文本协议枚举 2 项，其余 1843 项全部通过。受影响文件 Ruff、Python 编译及 `git diff --check` 通过；确认无活动任务后重启源码服务，8766 监听和任务接口正常；未发起真实计费图片编辑请求。

### OPT-20260829-13｜主模型配图意图不可被后置规则覆盖

- status: verified
- scope: 真题答案生成/检查点复用/审查修复，按题生题、知识点生题、蓝图单题草案、单题重生成与题图修复，所有主模型生图工具提示合同
- changed: 调整答案图要求优先级，使显式 `answer_figure_required`、主模型题意理解和图规意图在来源图/可选作图等旧数据启发式之前生效；增加缺失已确认答案图的语义门禁，防止无图断点被复用或验收。生题主模型模式彻底移除传统 `figures` 合同，已确认题图意图必须由同一主模型调用、回看并绑定真实图片，重试和修复不得回退旧图规。统一添加黑白/灰度白底默认，禁止依赖颜色区分内容，仅当用户、题目或来源证据明确把颜色作为语义时允许用色。
- trigger: 真实《图题》中主模型已经形成答案配图意图，但后置“来源图只供阅读/可按画图格式不强制生图”程序规则将其覆盖，导致图片工具 0 次调用且原题图被误当作答案图。
- invariants: 是否需图只由负责当前内容的主模型决定，程序只执行已形成的意图、能力门和资产绑定；无图任务继续单轮零生图；新旧链路互斥且历史任务默认传统链路；黑白是表现默认而非是否生图的判断器，明确颜色题意不得被去色。
- do_not_regress: 不得再用来源图、题型、关键词、可选作图措辞、第三方分类器或旧 `figures` 合同否定主模型已确认的配图意图；按题/知识点生题的草案、批次、缺项恢复、内容重试、单题重生成和图片修复均不得丢失工具回路或改用程序图；默认图不得用不同颜色代替线型、网纹、符号、形状、编号和文字标注。
- verification: 真实《图题》断点 `qa_s03_06_01` 无计费重放确认配图要求为 true、生成提示保留图规意图，旧 0 图候选被标记 `missing_required_answer_figure`；主模型意图、图片工具、按题/知识点生题、重试、断点和验收定向回归 135 passed；全库排除 5 项与本改动无关的本机测试环境/运营者动态图片服务商枚举失败后 1833 passed、21 deselected；原始全库结果为 1833 passed、16 deselected、5 failed（测试 venv 缺少 `math_ml2omml` 3 项，本地新增动态 `ark_image` 不在文本协议枚举 2 项）；Python 编译、Ruff 及 `git diff --check` 通过。

### OPT-20260829-12｜火山方舟 Seedream 图片专用入口

- status: verified
- scope: API Key 配置页、真题解析/按题出题/知识点出题的生图模型选择、方舟图片生成请求、模型能力登记
- changed: 新增共用 `ARK_API_KEY` 的用户可见方舟图片专用入口，只展示 `Seedream-5.0-pro` 与 `Doubao-Seedream-5.0-lite`，并分别绑定方舟官方 Model ID；旧 `ark` 文字入口仅供历史任务兼容且继续隐藏。方舟生图固定调用原生 `/images/generations`，请求单图 PNG、关闭水印和组图；旧本地配置不能恢复其他模型到该公开入口。
- trigger: 用户要求在 API 配置中展示火山方舟，但只把两个 Seedream 5.0 作为生图模型使用，不展示方舟的其他模型。
- invariants: 不破坏已存方舟文字任务和已保存 Key；公开入口不能进入文字/视觉模型路由；只有生图链路可选择两个 Seedream 模型；连接测试生成的临时图片不持久化到用户任务。
- do_not_regress: 不得将旧 `ark` 整体改为公开入口导致文字模型重新出现；不得允许自定义或本地遗留模型混入 `ark_image`；不得把展示名当作方舟 API Model ID 发送。
- verification: 服务商、API Key、前端合同、能力登记及任务配置定向回归 189 passed；`node --check web/app.js`、Python 编译、模型能力文档同步、77 文件项目完整度和 `git diff --check` 通过。方舟请求载荷通过受控响应验证；本机未配置方舟 Key，未发起真实计费生图。

### OPT-20260829-11｜按 Codex 与 DeepSeek Harness 收紧全模型图片工具回路

- status: verified
- scope: OpenAI Responses、Gemini/DeepSeek/Qwen Chat Completions、真题答案生成与模型重试、答案审查修复、按题/知识点出题重试、前端任务预检、模型能力登记及后续多模态研发规范
- changed: 以 Codex 的能力门和真实 `InputImage` 工具结果回灌、DeepSeek Harness 的工具注册/模型可见内容/追加上下文/耐久事件循环为强制基线；按服务商通道、模型和实际协议建立原生工具闭环白名单，工具循环按所选模型协议创建客户端而不继承服务商默认协议。主模型模式下答案候选切换只保留等价工具模型，生题传输重试不再丢失工具循环，审查修复缺少等价路线时保留原稿并明示失败；网页在创建三类任务前校验所选主模型能力。补回误缩进的真实图片工具执行方法，并把 Harness 复核要求写入项目规则和接入标准。
- trigger: Gemini 暴露的问题实际影响所有把 Responses 协议、视觉输入或服务商级能力误当成原生工具能力的模型；百炼同一服务商同时存在 Responses 与 Chat 模型，原实现会预检通过后用错协议；模型重试、传输降级和审查修复还能静默变成纯文本链路。对照 Harness 时又发现真实 `ImageGenerationTool.execute` 被辅助函数缩进吞入，最小函数调用探测无法覆盖实际生图执行。
- invariants: 程序只决定工具能否安全暴露，是否需要图片仍只由负责当前内容的主模型决定；真实像素必须回到同一主模型并被明确采用；无图任务保持单轮零图片调用；没有等价模型时失败关闭而不切换传统绘图或纯文本；新旧用户选择链路继续隔离，历史任务语义不变。
- do_not_regress: 后续任何多模态工具修改必须先核对 Codex 与 DeepSeek Harness 当前官方源码并记录映射，禁止凭题型、关键词、第三方分类器或代理推测另造图片决策层；不得按服务商、模型名称、Responses 协议或视觉输入能力推断工具能力；重试、修复和恢复不得丢失工具结果、真实像素、调用身份或已验证资产绑定。
- verification: 当前已配置通道的最小原生工具真实探测确认 DeepSeek 视觉模型、6 个百炼模型、3 个灵算 GPT 模型和 5 个灵算 Gemini 模型可返回工具调用；百炼 `qwen-vl-max/plus` 未返回工具调用、灵算 `gpt-5.6-luna` 因限流未获成功证据，均保持关闭。定向跨协议、三类任务、重试、修复及前端合同 188 passed；全量 pytest 1830 passed、16 deselected；完整质量门禁的 Ruff、Mypy、分支覆盖率、公式、许可证、版本和项目完整度全部通过；模型能力文档同步与 `git diff --check` 通过。源码服务在 8766 启动成功，版本接口和脱敏供应商接口实际返回上述关闭白名单。

### OPT-20260829-10｜主模型图片绑定贯穿审查修复重试

- status: verified
- scope: 真题解析答案审查修复、主模型图片工具事务、答案草稿检查点、主模型模式图件装配及 Word 长反应式布局
- changed: 审查修复在主模型模式下继续使用同一 `generate_image` 工具合同；纯文本修复保留已有真实回传记录的已采用图片，修复草稿同步写回答案草稿检查点；同一题同一修复事务进入确定性重试时重新回传本事务已生成图片，使主模型可继续采用或替换，同时不信任历史任务或仅存在路径的候选资产；多模态修复提示统一移除传统图形规格合同；Word 对超出可打印宽度的聚合反应式按反应箭头拆为两行，保留全部公式语义。
- trigger: 真实复合图题中，主模型已生成并采用的图片在后续整题替换式修复中丢失；缺图修复仍只产生传统规格而被主模型链路隔离；同题第二次结构修复又因没有重新回传第一次已检查图片，把合法资产 ID 误判为未检查。
- invariants: 是否生图仍只由负责当前答案的主模型决定；只有真实生成、校验、回传并由主模型采用的资产可装配；不同任务、不同题和历史候选不得因文件存在而自动获信；传统模式行为、无图题单轮零图片调用及两条链路互斥不变。
- do_not_regress: 审查修复不得删除已证明的主模型图片绑定、切换到传统图规或未经回传直接恢复 manifest 资产；同一修复事务的下一轮不得忘记本轮已回传图片；修复后的耐久草稿不得继续保留旧缺陷版本。
- verification: 定向主模型工具、修复回归、图片隔离与 Word 公式布局 52 passed；完整 `pytest` 1816 passed、16 deselected；`git diff --check`、项目完整度 77 文件和模型能力文档同步检查通过。真实《图题》任务生成 11 个答案片段与 11 张已采用答案图，全部图片经 `gpt-5.6-sol` 视觉复核通过，0 个图件质量问题；正式 Word 导出 18 页、独立 LibreOffice 渲染 17 页，逐页检查无缺页、越界、重复图注或公式乱码。交付等级为 `review_candidate`，仅因所给教材无法直接支持两道聚合物依据，未伪造引用。

### OPT-20260829-09｜新旧作图链路用户切换与执行隔离

- status: verified
- scope: 真题解析、按题出题、知识点出题的作图模式选择、任务持久化、主模型图片工具合同、传统程序绘图与答案图形装配
- changed: 增加任务级 `image_orchestration` 双模式开关并在三条业务入口保存用户选择；历史任务默认保持传统链路。主模型模式只向答案/出题主模型注入真实图片工具，移除旧规格合同，只装配主模型检查并采用的资产；传统模式完全不创建主模型图片工具，保留原程序/代码绘图和图片兜底。流水线入口一次性拆分依赖并断言互斥，初始化失败直接报错而不跨链路降级。
- trigger: 使用真实作图题批次验证时，11 道题的答案工具回路均未调用 `gpt-image-2`，而被旧提示分流到程序绘图规格；后续生成 8 张程序图且视觉审查全部未通过，任务最终被图件质量门阻断。
- invariants: 作图模式由用户明确决定并随任务持久化，复跑不得读取页面当前选择；是否需要图片仍只能由负责当前内容的主模型结合完整任务决定；无图题必须保持单轮、零图片调用；未回传检查或未明确采用的图片不得进入结果；任一模式失败不得静默切换到另一模式。
- do_not_regress: 不得把两条链路同时注入同一任务；图片工具模式下不得再用题型、绘图模式或旧规格生成器抢占主模型工具决策；传统模式不得向主模型暴露 `generate_image` 或 `generated_images` 合同；不得同时装配主模型已采用图片和未检查程序图；复合题不得丢失图片所属作答单元。
- verification: 新增隔离、迁移、提示合同、图形装配与前端契约定向回归 113 passed；受影响的答案、图件、生题、任务存储定向回归 122 passed；完整 `pytest` 1802 passed、16 deselected；`py_compile`、项目完整度 77 文件、模型能力文档同步及 `git diff --check` 通过。浏览器实测真题解析、按题出题、知识点出题三处开关均能在“主模型自主生图/传统程序绘图”间切换并即时更新标签，验收后恢复为主模型模式；服务端未知混合模式返回 HTTP 400，健康检查通过。真实 `gpt-5.6-sol` / `gpt-image-2` 对照：明确作图题 2 步、1 次图片工具调用、采用 1 张 1024×1024 图片，旧规格与旧绘图代码均为 0；纯文字题最终 1 步、0 图片工具调用、0 图片资产且内容完整性门通过。

### OPT-20260829-08｜修复灵算网关 Cloudflare 1010 误报

- status: verified
- scope: 灵算五条供应商路线的 HTTP 客户端标识、旧本地配置迁移、连接测试错误分类
- changed: 为灵算新网关固定浏览器兼容 `User-Agent`，并在配置加载层强制使用现行 `.org/v1` 地址，防止旧 `providers.local.json` 恢复不可达地址或空客户端标识；Cloudflare 1010 在 403 权限判断前单独分类为网关客户端拦截。
- trigger: 已确认有效的 `gpt-image-2` Key 仍被页面报为“模型权限不足”；读取脱敏上游响应后发现真实错误为 `HTTP 403: error code: 1010`，标准浏览器标识下同一 Key、模型、端点和请求体立即成功。
- invariants: 不记录或保存诊断使用的 Key；不改变模型、提示词、图片尺寸、计费次数和主模型生图决策；客户端兼容标识只固定到灵算内置路线，其他服务商请求头保持原样。
- do_not_regress: 不得把 Cloudflare 1010 再归类成模型权限不足；不得依赖用户手工删除旧本地供应商配置；灵算文字、图片、Gemini、xAI、Anthropic 路线必须共享可用网关与兼容客户端标识。
- verification: 灵算配置、图片、错误分类及传输定向回归 41 passed；完整 `unittest` 591 passed；模型配置/能力文档同步检查及 `git diff --check` 通过；真实 `gpt-image-2` 调用在默认客户端标识下稳定返回 HTTP 403/1010，加入浏览器兼容标识后成功生成 428836 字节图片，证明 Key、模型、Endpoint 与图片响应解析均有效。精确模型闭环复验：已保存的 `gpt-5.6-sol` 连接测试通过；无图算术任务 1 步完成、0 工具调用、0 图片；需水循环图的生题任务由主模型调用 `gpt-image-2` 1 次，在第 2 步检查并采用 1 张 1024×1024 内容寻址图片，最终引用全部可解析。

### OPT-20260829-07｜灵算 API 切换可用入口

- status: verified
- scope: 灵算 OpenAI、图片、Gemini、xAI、Anthropic 共享服务商配置与直连网络策略
- changed: 将五条灵算路线的基础地址由已发生 TLS 超时的 `lingsuan.top/v1` 统一切换到服务商公告的新入口 `lingsuan.org/v1`；新域名继续沿用灵算默认绕过系统代理的既有网络边界，并同步协议及图片端点回归断言。
- trigger: API Key 配置页真实测试中，文字模型和 `gpt-image-2` 均在旧域名 TLS 握手阶段失败并被归类为网络异常；同机直连新域名可正常完成 TLS 并返回未鉴权的预期响应。
- invariants: 不保存或记录用户 API Key；不改变模型、协议、重试、计费和主模型生图决策；五条灵算路线必须使用同一可达入口，图片仍调用 OpenAI 兼容 Images 端点。
- do_not_regress: 不得恢复不可达旧域名或只修改单条灵算路线；域名切换后仍须区分网络、鉴权与模型权限错误，不能把模型权限不足误报成连接成功。
- verification: 灵算配置、图片、Responses/Chat/Anthropic 协议定向回归 40 passed；完整 `unittest` 590 passed；模型配置/能力文档同步检查及 `git diff --check` 通过；`lingsuan.top/v1/models` 同机直连 TLS 超时，`lingsuan.org/v1/models` TLS 校验成功并返回预期 401；源码服务重启成功，真实 Key 对 `gpt-5.6-sol` 与 `gpt-image-2` 的测试均已到达新入口，错误由 `provider_network` 变为 `provider_permission`，证明网络修复有效且当前剩余问题为该 Key 的模型访问权限。

### OPT-20260829-06｜协作文件回归研发目的与修改原则

- status: verified
- scope: 项目级研发背景、问题判断、修改范围、多模态和工程一致性原则
- changed: 重组 `AGENTS.md` 开头结构，以平台交付目的和程序/模型职责为起点，建立“结果定义问题—证据定位根因—确定最小完整范围—验证端到端结果”的修改主线；将目录、命令、模型登记和依赖同步内容压缩为稳定原则并引用权威专项文档。
- trigger: 用户明确指出 AGENTS 的主要作用应是帮助代理认清平台研发背景与目的，并指导调整和修理；上一版新增内容偏向仓库导航和操作手册，虽然正确但弱化了研发判断主线。
- invariants: 多模态证据连续性、能力登记、配置/依赖同步、风险分级验证、用户数据和发布安全边界均保留；不改变任何产品行为、模型调用、费用或运行数据。
- do_not_regress: 不得把 AGENTS 再扩展成易过时的命令清单或版本手册；后续规则必须能说明它保护的用户结果、失败场景或修改判断，而不只是罗列文件和工具。
- verification: `git diff --check -- AGENTS.md docs/operations/OPTIMIZATION_LOG.md` 通过；`python3 scripts/audit_project_completeness.py` 检查 77 个必需文件且 0 问题；`python3 scripts/sync_model_capability_docs.py --check` 通过；人工结构检查确认文件先说明研发目的，再规定分析/修理、多模态和修改完整性原则，具体操作统一引用专项文档。纯协作规范改动，未运行代码测试。

### OPT-20260829-06｜Gemini 接入主模型自主生图闭环并修复恢复配置

- status: verified
- scope: Gemini Chat Completions、主模型工具回路、按题/知识点生题、单题重生、真题解析任务创建、历史继续与错误诊断
- changed: 为已登记的灵算 Gemini 模型增加原生 Chat Completions 工具调用适配；生图工具结果先按标准 tool message 回传，再将实际图片像素作为同会话多模态输入交给 Gemini 验收；最终结果仍只接受主模型看过的内容寻址 asset_id。生题、单题重生和真题答案入口统一做能力预检且保持传统绘图链路隔离。练习历史现在原子保存 image_orchestration、image_provider、image_model；旧历史可从其完成任务记录恢复之前漏存的图片路由。工具初始化错误不再伪装成“模型返回内容不可用”。
- trigger: Gemini 使用 Chat Completions，而原工具回路只接受 Responses；页面允许选择“主模型自主生图”后，任务会在首次网络调用前失败并被错误归类为 generation_response_invalid。旧历史又只保存编排模式，继续未完成项时丢失图片服务商和模型。
- invariants: 是否调用生图仍只由同一个主模型决定；程序只做能力、调用上限、资产完整性和 ID 绑定；未看过的图片不得进入结果；主模型工具模式不得静默降级到传统绘图；传统绘图模式不进入工具回路；历史继续和单题重生保持用户原选择。
- do_not_regress: 不得把 Chat 工具结果当普通文本而不回传真实像素；不得假定任意 Chat 模型支持工具，只有显式登记能力的模型可用；不得再次只保存 image_orchestration 而丢失 image_provider/image_model；初始化或能力错误不得计为模型已返回无效内容。
- verification: 真实灵算 Gemini 3.7 Flash Medium 返回 1 个原生 generate_image tool_call；受控两步闭环中工具执行 1 次、实际图片像素回传、Gemini 第 2 步验收并引用真实 asset_id。全量 pytest 1823 passed、16 deselected；模型能力文档同步检查、77 文件项目完整性审计及 git diff 检查均通过。

### OPT-20260829-05｜补齐日常开发与多模态协作边界

- status: verified
- scope: 项目级仓库导航、开发与验证入口、模型模态证据、配置和依赖同步规则
- changed: `AGENTS.md` 增加目录职责和权威入口；将模型能力单一数据源、证据连续性、等价路由降级、受限工具回路及图片资产采用条件提升为项目级规则；补充模型登记、生成文档、依赖约束和许可证同步要求；移除已由源码分发文档完整保存的 0.9.18/0.9.19 历史迁移细节。
- trigger: 原协作文件偏重发布与历史事故，日常修改缺少快速定位和统一门禁入口，多模态能力、证据链及配置/依赖联动规则散落在 README、接入标准和历史账本中，容易被局部修改绕过；具体旧版本迁移说明在项目级规则中重复且会随历史积累持续膨胀。
- invariants: 不改变现有业务、模型调用次数、费用、失败率、用户数据、正式启动和发布流程；机器可读能力登记及专项操作文档继续作为易变实现的权威来源。
- do_not_regress: 不得按名称猜测模型能力、静默丢弃必要模态、采用未回灌检查的图片资产或只更新服务商/依赖配置的一部分；不得用开发服务器或单项测试替代受影响的正式入口和质量门禁。
- verification: `git diff --check -- AGENTS.md docs/operations/OPTIMIZATION_LOG.md` 通过；`python3 scripts/audit_project_completeness.py` 检查 77 个必需文件且 0 问题；`python3 scripts/sync_model_capability_docs.py --check` 通过；文档结构与引用检查确认四个新增规则区块、全部权威路径存在，且 AGENTS 中不再保留 0.9.18/0.9.19/update-stable-v2 历史细节。纯协作规范改动，未运行代码测试。

### OPT-20260829-04｜主模型自主生图工具闭环

- status: verified
- scope: 真题解析、生题正式生成、Responses 协议、任务内图片资产与最终配图装配
- changed: 为 Responses 模型增加完整工具调用输出与多轮回灌；主多模态模型可零次或多次调用生图工具、接收并检查真实图片后用资产 ID 决定是否采用；图片按内容寻址保存并校验；解析与生题在能力可用时关闭旧的程序语义判断生图支路，无图任务仍单轮完成。
- trigger: 旧流程由题型/蓝图规则决定是否调用图片模型，无法知道主模型的完整生成意图，也没有把生成图片返回同一主模型确认，可能误生图、漏生图或采用不符合需求的图片。
- invariants: 程序只做能力门、调用上限、资产完整性和 ID 绑定，不判断内容是否需要图；未调用工具不得增加模型轮次；未回传给主模型的图片不得进入结果；图片服务失败由主模型继续完成文本或重试，不中断正常内容；现有用户数据路径与旧协议兼容行为不变。
- do_not_regress: 不得恢复关键词、题型或第三方模型替主模型决定是否调用生图；不得仅返回路径/提示词而不把实际图片回灌；不得接受伪造或未检查的 asset_id；主模型工具模式启用时不得再运行旧图片模型兜底。
- verification: `python3 -m unittest discover -s tests` 590 passed；解析/配图/流水线定向回归 115 passed；生题定向回归 123 passed；新增协议、资产、解析配图与生题工具回路定向回归通过；`py_compile` 与 `git diff --check` 通过。真实百炼联调：普通文字任务 1 步、0 工具调用；水循环任务 1 次生图并回传检查，工具后非标准 JSON 由同一主模型修复，最终绑定 1 个 2048×2048 内容寻址资产；受控坏图被拒绝并触发第 2 次生图，未盲目绑定。当前环境无 `pytest` 命令（未执行发布级 pytest/Ruff/Mypy 门禁）；本机未配置 `gpt-image-2` 提供商密钥，真实生图使用已配置的 `qwen-image-2.0-pro` 验证同一协议闭环。

### OPT-20260829-03｜协作规范改为风险分级和结果导向

- status: verified
- scope: 项目级验证汇报、正式发布边界、Windows 源码分发协作规范
- changed: `AGENTS.md` 补充按影响范围选择验证层级及报告实际证据的要求；将发布规则改为受保护工作流的稳定边界，并为基础设施故障设置需用户明确授权和等价验证的应急条件；Windows 约束改为正式入口、复杂路径、可理解失败和健康运行结果，易变实现细节统一引用专项文档。
- trigger: 原规则把验证原则、当前 CI 版本矩阵、具体工作流名称和历史实现禁令写在同一层，容易随实现变化过时，也缺少验证选级及紧急情况的授权边界。
- invariants: 当前 GitHub 源码 ZIP 主渠道、用户数据隔离、自动发布默认路径、历史标签不可变、正式 Windows 入口及失败可诊断性不变；不得降低发布门禁或把未公开状态表述为已发布。
- do_not_regress: 不得恢复无风险分级的笼统“已验证”；不得在无用户明确授权、无回滚和无等价验证时人工发布；不得把易变测试矩阵和入口实现复制为长期项目原则。
- verification: `git diff --check -- AGENTS.md docs/operations/OPTIMIZATION_LOG.md` 通过；Python 文档结构检查确认风险分级、用户授权边界及两份引用文档均存在。纯协作规范改动，未运行代码测试。

### OPT-20260829-02｜准备 0.9.34 生题恢复与 GLM 限流更新

- status: verified
- scope: 版本元数据、用户变更说明、源码 ZIP 发布候选
- changed: `APP_VERSION`、`VERSION`、发布清单和 `CHANGELOG.md` 同步升级为 0.9.34，发布说明覆盖最新修复候选检查点和 BigModel/GLM 429 共享并发退避。
- trigger: 用户明确要求将已完成的两项修复推送 GitHub 并更新正式版本。
- invariants: 不手工创建或推送标签；只有 `main` 全部远程门禁成功后才自动发布；不包含或覆盖用户数据，不扩展纯文本主模型与识图交接的修复范围。
- do_not_regress: 不得将 Git push 表述为已正式发布；必须等公开附件和稳定更新源完成核验后才宣布发布完成。
- verification: 锁定 Python 3.11 完整门禁通过（1779 passed、16 deselected、69% 分支覆盖率，Ruff、Mypy、公式链路、第三方清单和项目完整度通过）；版本一致性通过；源码 ZIP 包含 378 个白名单文件、反向验证 0 问题；解压包使用隔离数据目录启动成功，`/api/version` 返回 0.9.34 且首页 HTTP 200。

### OPT-20260829-01｜多轮生题修复继承最新候选

- status: verified
- scope: 知识点出题与按题出题共享的正式生成内容门禁、修复诊断及项目级修复规则
- changed: 每次有效修复响应均成为下一轮检查点，下一轮使用最新候选和重新计算的当前问题；诊断记录问题迁移，预算耗尽时按最后候选报错；`AGENTS.md` 增加跨真题解析、按题出题、知识点出题统筹检查同类环节的强制约束。
- trigger: Gemini 首版单选题缺选项，修复版已补选项但新增题干答案泄漏；旧流程丢弃修复版并回退首版，最终错误报告了已经解决的“缺选项”。
- invariants: 只替换当前失败题，已通过同批题保持不变；每题四次生成预算不增加；最新候选仍必须通过全部确定性门禁才能成为成功交付。
- do_not_regress: 修复出现新问题时不得回退到初始版本重复修旧问题；最终错误不得与最后检查的候选版本不一致。
- verification: 知识点/按题出题及生成恢复定向回归 60 passed；真题解析多轮修复与检查点回归 35 passed；修改文件 Ruff 与 `py_compile` 通过。

### OPT-20260828-07｜GLM 429 共享并发与退避治理

- status: verified
- scope: BigModel/GLM 模型请求入场、知识点/按题生题传输重试、生成预算和运行监控
- changed: BigModel 默认在网络边界跨任务共享 2 个并发位；可重试 429 会按 `Retry-After` 与带抖动的指数退避设置提供商级冷却，冷却期内其他 GLM 请求统一等待；GLM 429 记录为真实网络尝试但不消耗内容生成次数，持续限流不再拆成逐题无效请求；监控暴露提供商上限、等待、冷却和 429 计数，重试记录保存实际等待时间。
- trigger: 生题内层默认并发 6、任务并发 2，在没有提供商全局门禁时可同时向 GLM 发出 12 个请求；原有 429 只让当前线程休眠，其他线程仍继续冲击，且限流会错误消耗每题四次生成预算。
- invariants: 仅 BigModel/GLM 的可重试 429 不计入内容生成预算；其他提供商的并发与重试语义不变；额度不足、鉴权或模型权限错误仍必须进入配置处理，不得盲目退避；所有实际网络请求仍独立记账。
- do_not_regress: 不得只在单个生题线程内 sleep 而放任同一 BigModel 提供商的其他请求继续进入；不得将 429 当作题目内容质量失败；不得把额度不足类 429 标记为可自动恢复的限流。
- verification: 定向回归 121 passed；完整门禁 1777 passed、16 deselected、分支覆盖率 69%；Python 编译、Ruff、Mypy、公式链路、第三方清单和项目完整度均通过。

### OPT-20260828-06｜纯文本主模型承接已完成识图结果

- status: verified
- scope: 含图片的知识点/原题材料在识图完成后的蓝图规划、正式生题、任务保存与恢复
- changed: 识图阶段保存可直接供纯文本模型使用的完整 `recognized_content`；当且仅当主模型不支持图片、来源存在图片证据且已有识图结果时，将 `image:*` 证据转换为 `vision_text:image:*` 并把识图文本交给主模型，不再重复附图或因原图未进入主模型而阻断；保留识图文本及证据引用用于任务恢复和诊断。
- trigger: 测试用户选择纯文本主模型后，20 道知识点生题全部在模型调用前报 `generation_evidence_incomplete`，缺少 `image:1`；原图已由识图模型分析，失败来自阶段证据未交接。
- invariants: 不改变无图任务；不改变支持图片输入的主模型继续接收原图的流程；不增加后续补充识图；不修改题干配图的生图阶段；没有已保存识图结果时不得伪造证据或绕过门禁。
- do_not_regress: 纯文本主模型不得再次被要求接收原图；识图失败必须停留在识图阶段；视觉主模型的原图证据合同不得被文本化替换。
- verification: 定向回归 238 passed；完整 pytest 1768 passed、16 deselected；修改文件 Ruff 与 `py_compile` 通过。

### OPT-20260828-05｜对齐 Chromium 发布验收与现行交互契约

- status: verified
- scope: 源码发布的 Chromium 端到端回归
- changed: 验收用例改为显式点击“恢复上一份”、从任务卡“更多”菜单执行次要操作，配置错误只展示配置入口；下载交互只在浏览器层验证触发，实际 HTTP 附件字节继续由独立服务端回归负责；范围和蓝图草稿分别建立新的恢复决策，不绕过候选固定机制。
- trigger: 旧用例仍假设任务次要按钮直接可见、草稿自动恢复、配置错误可原样重试，并试图用 Playwright 路由拦截观察页面导航下载；这些均与 0.9.32 已确立的安全交互不一致。
- invariants: 不修改产品行为来迎合测试；不恢复会覆盖新输入的自动草稿恢复；不削弱真实附件响应内容的服务端验证。
- do_not_regress: 恢复候选在单次入口内必须保持固定，防止后台自动保存悄然替换用户将要恢复的草稿；配置错误不得提供无意义的盲目重试。
- verification: `ANSWER_BOOK_E2E_URL=http://127.0.0.1:18766 python3 -m pytest -q -m e2e tests/e2e/test_platform_smoke.py` 完整通过，13 passed。

### OPT-20260828-04｜准备 0.9.33 正式源码更新

- status: verified
- scope: 版本元数据、用户变更说明、源码 ZIP 发布候选
- changed: `APP_VERSION`、`VERSION`、发布清单和 `CHANGELOG.md` 同步升级为 0.9.33；发布说明如实覆盖依赖治理、MathJax 精简、Pydantic 影子观察及已在工作树中的生题后处理恢复与定向修复。
- trigger: 用户明确要求推送更新版本；0.9.32 已公开，正式发布必须使用新版本并由 `main` 门禁自动创建标签。
- invariants: 不手工创建或推送标签；源码包只能来自 Git 索引白名单并反向验证；发布说明不得虚构付费模型验证；用户数据不得进入提交或 ZIP。
- do_not_regress: 不得把本地 ZIP、Git push 或 CI 入队表述为公开版本已发布；必须等公开附件和稳定更新源完成核验后再宣布正式发布。
- verification: 最终完整门禁通过（含 1765 passed、16 deselected、69% 分支覆盖率、Ruff、Mypy、公式链路、第三方清单和项目完整度）；0.9.33 版本一致性通过，Chromium 端到端 13 passed；源码 ZIP 含 378 个白名单文件，反向验证 0 问题，解压后用隔离数据目录启动成功，`/api/version` 返回 0.9.33 且首页 HTTP 200。

### OPT-20260828-03｜蓝图、生题输出、绘图规格的 Pydantic 影子观察

- status: verified
- scope: 练习蓝图规范化、练习结果规范化、正式绘图规格生成、环境页质量报告
- changed: 在三个结构边界执行本地 Pydantic 校验；记录脱敏字段路径、错误类型、耗时、新问题模式、人工确认问题和误报计数；页面展示样本数和报告；支持本地人工标记 `confirmed_issue` / `false_positive`。
- trigger: 缺字段或类型漂移若到 Word/绘图阶段才暴露，定位晚且可能浪费前序工作；直接启用硬门禁可能因契约不准误杀正常任务。
- invariants: `enforced=false`、`actual_blocked=false`、新增模型调用/Token/网络请求均为 0；观察、校验或日志写入异常不得逃逸到业务流程；影子期避免失败和避免重试必须如实记 0，不能虚报收益。
- do_not_regress: 不得在 observer 中调用模型、触发重试、修改输入对象、抛出异常或自动升级门禁；不得保存模型输出、题目、提示词或教材正文；语义判断不得迁入 Pydantic。
- verification: 定向回归 69 passed；完整门禁 1765 passed、16 deselected、覆盖率 69%；Ruff、Mypy、公式链路、第三方清单和项目完整度通过；隔离用户数据目录启动源码服务成功，影子报告接口与网页入口均返回 200。

### OPT-20260828-02｜MathJax 源码资源精简

- status: verified
- scope: 网页公式预览、源码 ZIP 体积、离线静态资源
- changed: 仅保留实际使用的 TeX/MathML → CHTML 组合、`boldsymbol` 和 CHTML 字体；删除 SVG、语音、Node 适配和重复组合包；修正异步启动与重复排版。
- trigger: 仓库保存约 21 MB 完整目录但页面只使用少数组件；冗余增加下载、扫描和更新成本，异步处理不当可能导致首次显示异常或重复排版。
- invariants: 不改变公式输入、Word 原生公式和页面公式能力；先确认实际字体完整再删除资源。
- do_not_regress: 不得删除 `web/vendor/mathjax/output/chtml/fonts/woff-v2/` 的在用字体；新增 MathJax 组件时必须同步更新离线资源测试和第三方清单。
- verification: 资源约 3.1 MB；离线资源测试、Chromium 页面验证、模拟源码 ZIP 字体请求通过。

### OPT-20260828-01｜源码运行依赖治理

- status: verified
- scope: Python 依赖、跨平台源码启动、环境诊断、第三方许可证
- changed: NumPy 改为直接依赖；增加 macOS/Windows × Python 3.9/3.11 约束；增加第三方用途/许可证清单；环境页非阻断显示版本漂移。
- trigger: 直接使用的包若只靠间接依赖，替换上游后可能缺失；不同平台传递依赖漂移会造成开发机成功、用户机失败；缺少许可证记录会提高升级风险。
- invariants: 保留源码 ZIP 和双击启动方式；版本漂移只诊断，不阻断可运行环境；不引入无明确质量收益的大型框架。
- do_not_regress: 不得移除 NumPy 直接声明；更新依赖时同步四个平台约束与第三方清单；不得让环境建议版本差异阻止启动。
- verification: 完整门禁 1754 passed、16 deselected、覆盖率 69%；Ruff、Mypy、`pip check`、模拟源码 ZIP 启动通过。
