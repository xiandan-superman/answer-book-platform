# 已定位、待授权实施的问题

> 本文件记录已经有证据定位、但尚未取得实施指令的问题。它不是完成清单；只有代码、回归、部署或发布分别完成后，才能更新对应状态。

## 2026-09-05｜公式后处理改坏模型内容，图片纠错重复提交互斥参数

- status: implementing; local_targeted_tests_passed; full_gate_running; not_deployed; not_released
- affected_flow: 真题解析的答案片段、学术公式审计、主模型图片工具纠错和 Word 交付；公式规范化与图片工具是共享基础设施，两类生题和历史恢复同样受回归保护。
- observed: 影响任务 33 题均已生成答案，但最终公式门抦发现 3 条失败：程序每轮把已规范化的“常数”再套一层字体命令，并将正文中成对的 `{t_0}` / `{t_i}` 提取为少左括号的非法公式。另一张生成图将储能模量 `E'` 标为 `E''`；视觉审计正确发现后，修复模型连续提交了同时含本地原图与“最近图片”的互斥选择器，4 次工具调用均未产生新图。
- attribution: 公式主因是 `deterministic_postprocess`，次要缺口是末端审计直接终止而未进入 `harness_orchestration` 纠错回路；图片错标主因是 `model_output`，重复互斥参数是 `harness_orchestration` 未对可机器确认的唯一有效选择器做无损规范化。
- harness_comparison: 2026-09-05 10:44 CST 核验 OpenAI Codex `https://github.com/openai/codex.git` 默认分支 `main` @ `3fde89f628281b9b049376fb0cec4d1577bfeaec`，阅读 `codex-rs/core/src/tools/context.rs` 与 `codex-rs/codex-api/src/provider.rs`；核验 DeepSeek Harness `https://github.com/deepseek-ai/deepseek-harness.git` 默认分支 `master` @ `d347e703908d0406b7a7ef80e3a0e594d86b2215`，阅读 `packages/llm/llm-pi-ai/src/context.ts` 和 `packages/session/session-persistence/README.md`。对应共同合同是保留工具/错误结果并回灌后续回路；Word/OMML 语义门是本项目必要差异。
- implemented_repairs: 公式规范化改为幂等并折叠历史重复嵌套；行内公式提取吸收可证明成对的外层花括号；公式门拦只将失败题及精确错误回传最新候选，每轮重跑 Word 预检，默认最多 3 轮；图片纠错首次调用无任何最近生成图时，显式登记的本地原图是唯一有效选择器，程序无损忽略不可能的“最近图”；一旦存在最近图则继续返回结构化歧义错误。
- required_verification: 锁定 Python 3.11 定向与完整门禁；不调模型的历史答案片段回放必须清除 3 条公式失败；只复用已通过结果回修当前图片；最终 Word 验收通过后重启服务再复跑。

## 2026-09-04｜MinerU 基础包通过命令检查但默认 hybrid 后端缺本地依赖

- status: implementing; v0.9.45_candidate; user_task_verification_in_progress
- affected_flow: 真题与教材 PDF/DOCX 的 MinerU 主解析及其到题目结构的桥接；最初安装故障发生在模型请求前，图片归属故障会让后续模型收到缺失视觉输入的空题。
- observed: Python 3.11 与 `mineru==3.4.5` 基础 CLI 安装成功后，真实 DOCX 解析仍在 6% 失败；补齐 pipeline 后又发现上游 OCR 导入缺少 `six`。环境修复后，真实试卷的浮动图片被 MinerU 排在纯题号 `2、` 之前，平台按线性顺序把该图误挂到第 1 题，导致第 2 题进入知识规划时题干、图片和检索词全空，随后以“无检索候选”停止；同一 MinerU 结果还给大题标题包了 Markdown 加粗符号，旧分节器因而把多个大题误并到第一大题。
- attribution: 前两项是确定性依赖配置和安装验收缺陷；第三项是 MinerU 输出到本项目题目结构之间的确定性桥接缺陷，直接造成模型输入不完整。模型对空输入返回结构合法的空计划不是根因，供应商波动和 Word 文档工具也不是本次失败原因。
- harness_comparison: 优质 Harness 仍要求调用前保留完整用户输入和工具资产身份，并把不可执行的缺失输入作为显式错误；参考 Harness 不能凭空恢复未提供给模型的图片。本项目应在解析边界恢复图片锚点归属并使旧检查点失效，而不是要求模型猜题或在检索阶段重试。
- implemented_repairs: 依赖改为固定 `mineru[pipeline]==3.4.5` 及上游 OCR 代码漏声明的 `six==1.17.0`；隔离运行目录加入 pipeline 身份；执行命令显式传入 `-b pipeline`；安装标记绑定依赖 SHA-256，并在复用环境前实际导入 torch 和 MinerU pipeline 模块。桥接层识别“连续图片后紧跟纯题号”的 Word 浮动锚点顺序并把题号移到图片之前，同时清除完整大题标题外层的 Markdown 强调符号；题目分组策略升至 v10，旧的错误结构不会被复用；完全无题干、图、表或子题的条目在模型调用前阻断。
- required_verification: 定向、全量、发布包和公开发布门禁；用受影响用户原 DOCX 完成真实 MinerU 解析、整任务交付验收和重启后复跑。

## 2026-09-04｜Python 3.14 被误用为平台运行时导致 MinerU 安装失败

- status: resolved; v0.9.43_released_and_deployed; failed_task_preserved_not_rerun
- affected_flow: 源码启动器、环境检查、真题与教材 PDF/DOCX 的 MinerU 主解析；按题出题、知识点出题和 Word/PDF 交付不直接触发该次失败，但共享同一平台运行环境。
- observed: Windows 用户机只有 Python 3.14 时，0.9.41 的“3.11 或更高”检查错误放行，并由 3.14 创建运行环境；新任务环境阶段显示通过，随后在 `extract_exam` 按需安装 MinerU 3.4.5 时因上游要求 `>=3.10,<3.14` 失败。任务在模型调用前终止，原材料与配置保留。
- attribution: 主因是 `delivery_environment` 和启动/依赖管理的确定性程序缺陷；模型输入、模型输出、供应商传输和模型 Harness 未参与，不应通过模型重试处理。
- implemented_repairs: 所有用户启动入口、bootstrap、监督器、环境页和流水线统一固定 Python 3.11；错误版本创建的同名运行环境改为先隔离保留再重建；MinerU 改用带 `py311` 身份的独立目录，并在 pip 前校验解释器，安装失败只返回简短可操作提示。部署中又发现 Windows `pythonw.exe` 可创建缺少 pip 的半成品虚拟环境，启动器已增加 `ensurepip` 自动修复与复核。
- required_verification: Python 3.11 定向与完整门禁；Windows 同时安装 3.11/3.14 时入口必须选择 3.11；从正式入口创建两个隔离环境、安装 MinerU、运行失败任务到越过解析阶段；确认无用户数据覆盖、无失败前模型调用。
- authorization_boundary: 用户已授权处理；受影响用户机已并排安装 Python 3.11.9，3.14.6 保留，0.9.42 正式源码已部署，新环境依赖和 MinerU 3.4.5 已安装，局域网服务以 Python 3.11 恢复。未自动重跑会产生付费模型调用的失败任务。

## 2026-09-03｜复合 LaTeX 公式被排版拆分后导致 Word 生成失败

- status: implemented_locally; verification_in_progress; not_deployed; not_released
- affected_flow: 真题解析的答案片段到 Word/OMML 交付；同一共享显示公式渲染器的按题出题、知识点出题和历史任务恢复也必须复查。
- observed: 模型返回的完整 `cases` 公式单独通过现行公式转换；长反应式排版函数随后从环境内部的第一条箭头处分割，生成只有 `begin`、没有配对 `end` 的半截公式，最终触发公式转换失败。外层只报告 Word 未生成，模型修复因错误不在白名单而跳过。
- attribution: 主因是 `deterministic_postprocess`；次要缺口是 `harness_orchestration`。没有证据表明该硬失败由模型输入缺失或原始模型公式无效导致。供应商连接失败经重试后恢复，与最终 Word 失败并非同一根因。
- harness_comparison: 2026-09-03 动态核验 OpenAI Codex `https://github.com/openai/codex.git` 默认分支 `main`、提交 `6d7f6dcd2285de70a3892d4f05b2a8ff44aa3350` 的工具路由、执行编排和文档 artifact operation；核验 DeepSeek Harness `https://github.com/deepseek-ai/deepseek-harness.git` 默认分支 `master`、提交 `76fda729799fe9b3848dbe2c211d4b231032b81e` 的 `agent-loop/src/tool-calls.ts`。两者都保留工具调用/结果身份并把结构化失败作为后续模型上下文；Codex 核心不提供本项目专有的 DOCX/OMML 公式拆分规则，因此环境感知的确定性保护仍须由本项目实现。
- codex_feasibility: 具备完整题目、答案候选、文档编辑/执行工具和渲染反馈时，Codex Harness + 相应 GPT 模型可以通过“执行 → 看到真实错误 → 修改候选或工具实现 → 重跑 → 验收”完成此类任务；关键差异是闭环能力，不是模型保证一次输出无误。本项目当前固定流水线在排版阶段改坏正确输出，又没有把精确错误送回可行动闭环。
- related_findings: 同次诊断还发现内容质量门留下两条缺图和两条计算一致性问题，以及小数除法被截取成除零表达式的格式审计误判。这些与公式崩溃是独立问题，实施时不得只修最终异常而忽略内容闭环。
- implemented_repairs: 已保护 `cases/aligned/matrix/array` 等结构化环境不在内部箭头处分割；普通长反应式的候选拆分改为逐段 OMML 预验证，任一失败则无损回退完整公式；新增文档工具结果合同，以配对的 `call_id` 保存操作、主责层、精确字段/公式 ID、内容哈希、产物哈希和下一步建议，不保存题目或答案正文；真题 Word、练习 Word 与渲染验收已接入该合同。小数除法审计改为识别完整操作数，结果摘要中的除法关系和科学计数法会确定性提升为公式对象。内容修复首轮现在携带精确的本地算术诊断，后续每轮携带最新候选和结构化校验结果，默认允许三轮有界纠错。
- remaining_repairs: 同次诊断的两条缺图和两条计算一致性问题已有更完整的纠错机制，但尚未重跑历史任务证明具体四题已修复；不得因最终 Word 已可生成而隐藏这些内容问题。
- required_verification: 最小复现必须证明完整复合公式在拆分前后保持可转换；覆盖普通长反应式、复合环境、行内/显示公式；分别验证真题解析、按题出题、知识点出题、历史失败任务恢复、Word/PDF 交付；确认模型调用不因确定性修复增加，未解决内容问题不会被标为成功。
- authorization_boundary: 用户已明确授权按 Harness + 文档工具 + 现有教学规则方案修改。当前已修改本地源码；尚未重跑该历史失败任务，尚未部署或发布。
