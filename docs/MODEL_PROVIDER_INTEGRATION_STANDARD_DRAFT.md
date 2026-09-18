# 模型服务商与模型接入标准（审阅草案）

> 状态：平台级调用门禁与主要任务链已在本地源码实施，真实模型质量基线仍需持续补测
> 配套能力表：[模型能力与任务适配表](./MODEL_CAPABILITY_REGISTRY_DRAFT.md)

机器可读单一数据源为 [`config/model_capabilities.json`](../config/model_capabilities.json)，当前审阅表由 `python3 scripts/sync_model_capability_docs.py` 自动生成。服务商或模型清单与能力表不一致时，配置加载和自动测试会失败。

## 1. 总原则

### 2026-09-16 能力依据与通道状态分离（用户确认）

模型输入能力按模型官方说明登记，同一型号经聚合商调用不因通道短测失败而降为纯文本。供应商请求协议、连接错误与实测记录独立保存，不用于覆盖官方声明的多模态能力。灵算 `deepseek-v4.1-flash` 的文字和图片输入依据 https://api-docs.deepseek.com/ 及 https://api-docs.deepseek.com/guides/vision 。本次 Chat Completions 与 Responses 都有成功观察；平台客户端另有 503 瞬时失败，保留为可用性观察，不改变模型能力。

### 2026-09-12 灵算国模分组与统一 Edge 入口

用户指定四个灵算分组统一使用 `https://edge.lingsuan.org/v1`。新增 `lingsuan_domestic`，其十个模型只共用独立 `LINGSUAN_DOMESTIC_API_KEY`，不迁移或复用其他分组凭证。新组十条 Chat Completions 路线在 Edge 上均完成最小 JSON 调用（auto，不发送额外思考档位），只登记文本、人工显式选择的 limited 资格；不声称 Responses、视觉、长任务质量或独立并发容量已验证。旧分组的既有能力观察仍是历史记录，不改写为新域名实测。

本次官方上游快照：OpenAI Codex main `c4017a87aacc7558002b7cb510025e967c1d765e` 的 `codex-rs/protocol/src/openai_models.rs`；DeepSeek Harness master `c291e7961a515f6d7af9304e7fd1d257929aef26` 的 `packages/client/ui-model-selection/README.md`。其逐模型默认/可选思考档位及请求快照原则保持适用，本网关实际支持仍以独立请求证据为准。

### 2026-09-12 输入能力与接入可用性分离

文本或文本加图片输入继续以机器可读登记为准；已登记多模态模型接入时用一次图文请求验证并永久保存记录，生图模型单独记录。旧模型统一补测一次；健康超时、限流、连续失败或恢复不改写输入模态、任务适配、协议及思考档位。运行中心持续更新供应商公开模型目录、精确路线可用性和共享并发观测，沿用三次供应商连续失败标为不可用、一次成功恢复及账号问题归因规则；历史状态仅提示并影响保守并发，不阻止用户实际尝试，任务本次失败仍按原有预算和停止规则处理。主模型工具调用继续默认允许，不恢复工具能力探测或白名单。详见 [供应商运行中心](operations/PROVIDER_CONTROL_CENTER.md)。

### 2026-09-11 DeepSeek V4.1 Flash 官方直连接入

DeepSeek 官方通道只登记稳定模型别名 `deepseek-flash`，当前对应 V4.1 Flash；已退役的官方直连 `deepseek-v4-flash` 与 `deepseek-v4-flash-vision-exp` 不再恢复。火山方舟中的 DeepSeek 模型是另一条独立供应商路线，本次不删除、不改名、不继承官方直连的验证或容量结论。

使用官方 Key 完成 `/models`、Responses、Chat Completions、Responses/Chat 图片输入、结构化输出、流式结束以及 `disabled/low/high/xhigh` 思考档位实测。官方 Responses 的关闭思考参数为 `thinking.type=disabled`；其余平台档位映射为 `low/high/max`，不能继续套用通用 Responses 的 `none/minimal/medium/high` 参数。1/2/4 并发、每档两轮的 14 个官方 Responses 短请求全部成功；4 并发仅记为当时验证峰值，未完成真实长任务前生产默认仍保守设置为 2。所有延迟与成功率均为 2026-09-11 时间点观察，不构成长期 SLA。

### 2026-09-10 模型/API 实测登记与上线门禁

当前目录已按用户确认收口为火山方舟、阿里云百炼、灵算 GPT/Gemini/GPT 生图、WawAPI GPT/Gemini/Grok 及三类生图通道；未列出的服务商从公开配置、能力注册表和运行时目录移除，旧的本地覆盖文件也不能将其恢复。完整实测快照见 [`docs/operations/MODEL_PROTOCOL_VERIFICATION_2026-09-10.md`](./operations/MODEL_PROTOCOL_VERIFICATION_2026-09-10.md)，机器记录为 [`config/model_protocol_verification.json`](../config/model_protocol_verification.json)。

协议准入单位固定为“服务商通道 + 模型 + API”。短探测分别验证 Responses、Chat Completions 或生图实际协议，禁止协议回退，且不保存凭据、正文或生成图片。实测延迟与失败只代表测试时间点，不能作为长期 SLA 或永久熔断依据。任务开始前仅检查静态配置并读取状态，不额外探测；普通任务页面隐藏请求类型控件，保留已登记/已保存的协议进入持久化、恢复和实际调用，不因健康状态自动切换协议。

今后任何新服务商或新模型必须先同时加入供应商配置、能力表和协议验证记录；缺少真实观察，或只有失败观察而没有任何通过协议时，配置加载与回归门禁必须失败，禁止先上线再补记录。本轮已经在线但只得到失败快照的路线采用明确的有限存量清单保留并在任务前重检，不能用作新增模型豁免。

### 2026-09-08 教材依据独立工具与安全并行

教材依据已收口为统一公开产物合同：题号、知识点、已核验印刷页码。教材原文仅留在任务内部候选与诊断文件，不进入该辅助工具的 Markdown/JSON 结果。新增 `textbook_evidence_only` 任务模式复用题面识别、知识点规划、教材索引、候选检索、证据审定和模型用量记账，完成后立即交付，不调用答案生成、正确性复核、生图和 Word 答案链。

完整真题解析只在纯文本、无作图要求、无可复用答案检查点时，允许“无教材答案草稿”与教材依据链并行。依据审定后必须重新绑定已确认证据，并继续原有内容质量审查和有界回修；投机草稿失败则回到依据完成后的原生成路径。含图/作图题、教材定位辅助工具和恢复任务不投机并行。并行不绕过服务商容量档案、跨任务共享准入、冷却、取消和 Token 记账。

### 2026-09-08 供应商容量实测与 WawAPI 默认值

本次动态核验官方远端与本地检出 origin：OpenAI Codex `https://github.com/openai/codex.git` 默认分支 `main`、HEAD `553df1c691fe8bf7747e50da22f1342984495ae0`，阅读 `codex-rs/core/src/responses_retry.rs` 的 Responses 流错误有界退避、可见重连和保持当前 turn 配置的同请求恢复；DeepSeek Harness `https://github.com/deepseek-ai/deepseek-harness.git` 默认分支 `master`、HEAD `c389f96bf3a9b6807cb71ed6bdad5849be0df6d8`，阅读 `packages/llm/llm-retry/src/index.ts` 的按供应商耐久重试状态、`packages/core/agent-loop/src/tool-calls.ts` 的有界滚动并行池与取消排空。上游没有提供聚合网关账号容量的通用默认值，因此本项目通过真实原生协议探针建立本地容量档案；教学证据阶段还必须保持用户选择的任务推理强度，不以阶段优化名义静默降级。

WawAPI 真实探针分别使用 GPT-5.6 Sol/Terra Responses、Gemini 3.7 Flash Chat Completions 和 Grok 4.6 Responses，每个模型独立测试 1/2/4 并发、每档 2 轮，另做 Sol/Terra 同账号混合 2/4 并发。修复平台缺少浏览器兼容 User-Agent 导致的 Cloudflare 1010 后，有效样本显示 4 并发能完成短请求，但 Terra 出现 `server_is_overloaded`，Sol 单请求亦有失败，Gemini/Grok 有明显尾延迟；此外既有长任务已观察到 524 与过载。因此 `config/provider_capacity_profiles.json` 为 WawAPI OpenAI、Google、xAI 三个独立密钥通道均设定生产默认 2，保留 4 作为已测短请求峰值而非生产默认。限流、并发拒绝、过载和 524 会在同通道跨任务触发共享冷却；最终请求经内部重试成功不能掩盖底层过载尝试。

今后新供应商不得直接沿用 WawAPI、灵算或官方通道的并发值。接入顺序固定为：原生协议与 User-Agent 连通性 → 单模型 1/2/4 并发短请求 → 同密钥多模型混合波次 → 至少一个平台真实长任务 → 保守地写入生产默认和冷却参数。实测工具 `scripts/probe_provider_capacity.py` 不接受命令行 Key，只读平台本机私密配置，报告不保存凭据或正文。

### 2026-09-08 配置目录与任务模型选择分离

本次动态核验并校验本地检出的官方远端：OpenAI Codex `https://github.com/openai/codex.git` 默认 `main`、HEAD `d6489472f3c15e87d2d7763a5fde033545c530f8`，阅读 `codex-rs/app-server-protocol/src/protocol/v2/model.rs` 的公开模型目录字段和 `codex-rs/app-server/src/message_processor.rs` 的服务商能力读取入口；DeepSeek Harness `https://github.com/deepseek-ai/deepseek-harness.git` 默认 `master`、HEAD `c389f96bf3a9b6807cb71ed6bdad5849be0df6d8`，阅读 `packages/client/ui-model-selection/README.md` 与 `packages/session/session-format-v0-to-v1/src/dispositions.ts` 的服务商分组模型目录、完整 `provider/model/reasoningEffort` 选择和耐久选择事件。

对应本平台，API 页面只承担供应商凭证、通道和已登记模型目录；真题解析、按题出题与知识点出题在进入具体任务时，才从“已保存 Key + 当前任务阶段适配 + 输入模态匹配 + 未记录连接失败”的交集中展示模型。任务选择不再接受未登记的自定义模型 ID，也不按模型名称关键词猜测视觉能力；视觉输入、生成类型和任务适配均来自 `config/model_capabilities.json`，服务端通过脱敏 `/api/providers` 投影给页面。与上游的必要差异是：本平台按教学阶段进一步过滤目录，并在无候选时引导回配置中心；不改变已创建任务的耐久路由，不新增模型调用或自动切换。

### 2026-09-07 渐进式模型配置与能力门

本次动态核验了官方远端：OpenAI Codex `https://github.com/openai/codex.git` 默认 `main`、HEAD `d665e3bbc81b013baa73d067507169c20395b988`；DeepSeek Harness `https://github.com/deepseek-ai/deepseek-harness.git` 默认 `master`、HEAD `b0a7d2ce3b4c19d7452e364b2d7acbfa87e707ed`。实际阅读 Codex `codex-rs/core/src/tools/spec_plan.rs` 的 `image_generation_available`：图片工具只能在功能开关、服务商能力与当前模型图片输入能力同时满足时暴露；同时阅读 DeepSeek `packages/core/agent-loop/src/agent.ts` 的 `preStep`/`turn` 上下文组装和步骤循环。

对应本平台，真题、按题生成与按知识点生成的默认页面只显示主模型：选中不直接读题图的模型时展开独立识图路由；所有主模型均直接开启真实工具调用与自主生图闭环，不再以服务商、模型、协议白名单或运行探测记录作为准入条件。高级文字分工未展开时，推理与正确性复核跟随主解析模型；展开后才允许独立选择。

### 2026-09-07 WawAPI 聚合通道登记

本次新增 WawAPI 通道时动态核验了官方远端：OpenAI Codex `https://github.com/openai/codex.git` 默认 `main`、HEAD `16ff14c266179e6a762dc8081e9dab73a96683e0`；DeepSeek Harness `https://github.com/deepseek-ai/deepseek-harness.git` 默认 `master`、HEAD `b0a7d2ce3b4c19d7452e364b2d7acbfa87e707ed`。此改动只新增本地服务商、模型和密钥隔离登记，未改变请求编排、工具循环或重试逻辑；因此没有将新网关模型声称为已通过 Harness 或平台实测。其公开首页仅声明统一 API 接入，实际模型目录与协议仍须以连接测试和真实任务验收为准。

### 2026-09-06 真实验收后的上下文复查

OPT-20260906-10 再次核验下列官方HEAD未变。真实证据发现完整蓝图规划错误复用了细化的顶层数组schema，而消费者读取 blueprint.exercise_plan；现按明确任务阶段选择不同结构，并保留历史顶层布局的无损读取，遇到双版本冲突不自动择一。此为本项目生产者/消费者合同修复，不改变上游或图片工具循环，也不以schema通过代替教学质量。

同日方向校正后再次动态核验两官方仓库远端 HEAD 与本地 origin，提交与下述记录一致。补查发现生题合同、蓝图与生成将用户要求截断为 1000 字，而审查/单题恢复读取另一个版本；统一为 `practice_requirements.practice_user_focus`，在已有上下文中保留完整原文并随蓝图/结果持久化，不增加调用轮数。缺省恢复沿用保存要求，不根据题目反推或编造历史要求。长要求可能增加实际输入 token，仍受现有请求预算约束；这不是完整耐久会话实现，也不证明模型一定遵守要求。

再次动态核验官方远端 HEAD 和本地精确 origin：Codex `https://github.com/openai/codex.git` 默认 main，`ac192cd7937b0d73edc6dffe009940ae53782dd4`；DeepSeek Harness `https://github.com/deepseek-ai/deepseek-harness.git` 默认 master，`d347e703908d0406b7a7ef80e3a0e594d86b2215`。读取 Codex `codex-rs/core/src/session/turn.rs` 的输入/工具结果续轮（该文件与前次基线无差异），以及 DeepSeek `packages/core/agent-loop/src/agent.ts` 的 preStep/step/buildRequest：每轮显式组装上下文并使用耐久会话消息。本地缺口是另建语义审查请求时遗漏用户原始要求，不能以生成阶段曾接收要求视为审查也已接收。修复在已有审查调用中携带原要求，不新增长审查链、不恢复独立生图后视觉审查；上游本身不保证教学语义或 Word 交付质量。

本轮追加验证（OPT-20260906-09）：上述相同官方 HEAD 再次核验未变。已有内容审查携带完整来源及最终采用的图片像素，并在每个附件前写明来源/交付身份；不恢复独立图片审核阶段。局部修复与主动换题区分，单题复核显式声明子集范围。服务商前置说明中的花括号不得遮蔽完整终末 JSON；此为本地兼容层的无损解码，不声称来自上游实现，也不放宽 schema 或图片采用校验。真实测试证明可减少一类无效语法重试，但内容质量仍需独立验收。

### 2026-09-06 输出保真与逐对象交付实施基线

本次动态核验官方远端与本地 origin：OpenAI Codex `https://github.com/openai/codex.git`，默认 `main`，HEAD `6af345407d9c2a568da9d01b6c4b81a9e61495c0`；DeepSeek Harness `https://github.com/deepseek-ai/deepseek-harness.git`，默认 `master`，HEAD `d347e703908d0406b7a7ef80e3a0e594d86b2215`。读取 Codex `codex-rs/core/src/session/turn.rs` 的结果入会话、后续循环与上下文管理，DeepSeek `packages/core/agent-loop/src/agent.ts`、`tool-calls.ts` 的耐久消息与调用结果关联。对应本平台：回修保留原始依据及最新候选，校验版本与错误绑定，逐对象隔离并保留有效成果；不将事件日志本身称为完整 Harness 闭环。教学范围、题组依赖、Word/OMML、部分交付与集合验收是本项目必要扩展。真实付费任务验收由用户后续提供场景，不能以离线测试替代。

接入对象不是只有“服务商”，而是 **服务商通道 + 模型 ID + 协议版本**。同名模型通过官方直连和代理商调用时，必须建立两条独立记录，不能继承另一通道的验证结论。

新增模型的完成标准不是“设置页可选、测试按钮成功”，而是：

1. 能力信息已完整登记；
2. 声明支持的输入真实调用通过；
3. 至少一个平台真实任务流程通过；
4. 输出合同、错误翻译、限流和降级策略已验证；
5. 任务开始前能判断该模型是否兼容当前输入和任务阶段。

### 2026-09-08 请求类型与推理强度准入合同

核对时间：2026-09-08（Asia/Shanghai）。动态核验 OpenAI Codex 官方远端 `https://github.com/openai/codex.git` 默认分支 `main`、完整 SHA `2cbbf0c9b542a36a1c3284b5e804917635b6f666`，阅读 `codex-rs/protocol/src/openai_models.rs` 的逐模型 `supported_reasoning_efforts`、默认推理强度及 Responses 推理参数合同，并核对核心测试使用 `/v1/responses`；动态核验 DeepSeek Harness 官方远端 `https://github.com/deepseek-ai/deepseek-harness.git` 默认分支 `master`、完整 SHA `c389f96bf3a9b6807cb71ed6bdad5849be0df6d8`，阅读 `packages/api/session-controller/src/types.ts` 与 `packages/subagent/tool-subagent/src/list-models.ts` 的精确供应商/模型路由、可选推理强度和默认强度目录。

因此，新服务商或新模型的准入单位进一步固定为 **服务商通道 + 模型 ID + 请求类型 + 推理强度**。请求类型必须通过真实调用明确登记为 `responses`、`chat_completions`、`anthropic_messages` 或其他原生协议；不能因接口兼容 OpenAI、模型名称相同或另一供应商已经验证，就默认选择 Chat 或 Responses。每条请求类型都要分别验证支持的推理档位、默认档位、最低档位、线上参数映射和不支持时的错误表现。新记录缺少任一项时只能处于待验证状态，不得出现在任务模型选择器中。

GPT 文本模型优先使用 Responses；若某个代理通道的同一模型只有 Chat Completions 通过了完整实测，可将该精确“服务商 + 模型 + Chat”路线单独登记，Responses 仍保持未准入。不得把 Chat 当作 Responses 失败后的运行时回退，也不得根据一般超时、限流或 5xx 静默改协议。Gemini、Grok 及其他模型同样以该供应商该模型的实测结论确定 Chat、Responses 或原生协议，而不是按模型家族写死。用户选择的推理强度必须随任务持久化；重试、恢复、并行分支和结构纠错不得改变模型、请求类型或推理强度。

### 1.1 官方 Harness 身份与刷新纪律

所有模型调用相关的实现、故障和设计，优先对照以下两个官方开源上游；同名项目、Fork、镜像、包管理器页面、搜索摘要和第三方解读都不能替代官方源码：

| 上游 | 唯一认可的官方 Git 地址 | 2026-09-08 核验快照 |
|---|---|---|
| OpenAI Codex Harness | `https://github.com/openai/codex.git` | 默认分支 `main`；`2cbbf0c9b542a36a1c3284b5e804917635b6f666` |
| DeepSeek Harness | `https://github.com/deepseek-ai/deepseek-harness.git` | 默认分支 `master`；`c389f96bf3a9b6807cb71ed6bdad5849be0df6d8` |

快照只用于判断参考是否过期，不代表永久锁定分支或提交。每次相关排障或修改开始前必须查询官方远端 `HEAD`，动态取得默认分支和最新提交；使用本地检出时还必须确认 `origin` 精确匹配上表 Git 地址并取得最新远端引用。实际结论必须记录仓库 URL、默认分支、完整提交 SHA、核对时间和阅读文件，不能只写“参考 Codex/DeepSeek”。

若最新提交、引用路径或相关合同与上一次记录不同，先重新阅读变化并更新本节的核验快照、对应专题、能力登记和变更账本，再修改平台。网络不可用或官方身份无法核验时，只能把现有材料标为“未核验旧参考”；不得宣称它代表当前官方实现，也不得据此作依赖上游现行行为的设计结论。

优先对照范围覆盖请求/响应协议、上下文组装与压缩、结构化输出、工具注册与事件循环、重试/退避与错误分类、并发/取消、会话持久化/恢复、能力门、模型/协议切换和多模态输入输出。若某一 Harness 没有同类实现，必须如实记录“未覆盖”，再说明本项目因教学质量、完成率、费用、隐私或跨平台边界所需的自定义方案，不能虚构上游能力，也不能无差异照搬。

### 1.2 主模型自主生图的 Harness 基线

这条链路不得由平台自行发明内容决策协议。实现、排障和后续修改都必须先对照以下官方源码（查阅日期：2026-08-29）：

- OpenAI Codex Harness 的 [`spec_plan.rs`](https://github.com/openai/codex/blob/main/codex-rs/core/src/tools/spec_plan.rs) 先按功能开关、账户/服务商能力和主模型图片输入能力决定是否向模型暴露图片工具；[`image-generation/src/tool.rs`](https://github.com/openai/codex/blob/main/codex-rs/ext/image-generation/src/tool.rs) 暴露 `referenced_image_paths` 与 `num_last_images_to_include`：两者均未提供时执行 `Generate`，任一参考选择器存在时执行 `ImageEditRequest`，两者不得并用、单次最多 5 张，本地参考图按原始画质读取；生成的真实图片字节再作为 `InputImage` 放入同一工具调用结果，供原主模型下一步检查。它是本平台“能力门 + 原图编辑/从零生成分流 + 图片真实回灌”的直接实现参考。
- DeepSeek Harness 的 [`tools/README.md`](https://github.com/deepseek-ai/deepseek-harness/blob/master/packages/core/tools/README.md)、[`tool-calls.ts`](https://github.com/deepseek-ai/deepseek-harness/blob/master/packages/core/agent-loop/src/tool-calls.ts) 和 [`core.md`](https://github.com/deepseek-ai/deepseek-harness/blob/master/docs/subsystems/core.md) 定义通用工具注册、前后置门禁、模型可见 `ContentBlock[]`、追加上下文、按模型顺序提交结果及耐久会话事件。DeepSeek Harness 核心没有内置图片生成工具，因此只作为跨模型/协议工具循环的抽象参考，不得把不存在的专用生图实现写成本项目依据。

2026-08-29 再次逐项复核后的实现对照如下。这里的“未采用”不是遗漏免责，而是明确的架构边界；一旦平台增加第二类可并行工具或需要进程中断后原位续跑，必须重新评估。

| 上游合同 | 本平台状态 | 对应实现或必要差异 |
|---|---|---|
| 主模型按需调用生图工具 | 已采用 | `tool_loop_supported` 仅保留构造原生工具请求所需的传输适配检查；不再查询逐模型白名单或账号探测状态。 |
| Codex `deny_unknown_fields`、严格选择器及 1–5 张限制 | 已采用 | 模型可见 JSON schema 和执行前运行时校验同时生效；未知字段、错误类型、未登记路径和互斥选择器在付费图片请求前返回 `INVALID_TOOL_ARGUMENTS`。 |
| Codex Generate/Edit 分流、原始参考像素、真实图片回传、请求元数据 | 已采用 | 未选参考图才 Generate；来源图和最近生成图执行 Edit；结果校验后以真实图片回到同一主模型；可用时保留 `request_id`/`revised_prompt`。平台生成图上限 25 MB，比 Codex 的 32 MB 更严格。 |
| Codex started/completed/failed 工具事件 | 已采用等价实现 | 每个任务图片目录写入追加式 `tool_events.jsonl`，在调用前、结果后立即 flush/fsync；同时记录主模型请求、完成或请求错误的模型/协议/摘要哈希。 |
| DeepSeek 参数快照、调用身份、结构化失败及错误后继续循环 | 已采用 | 参数在策略/执行前做 JSON 快照；同一 `call_id` + 同一参数在同一答案/修复事务内幂等复用，不同参数复用同一 ID 返回 `TOOL_CALL_ID_REUSED`；未知工具、参数、执行失败和预算耗尽均作为模型可见结构化结果，不直接终止整题。 |
| DeepSeek 重复调用提醒 | 已采用 | 连续第三次相同工具与规范化参数时追加可耐久、模型可见的 advisory；不硬阻断合法的随机重绘，仍由主模型决定改参、结束或继续。 |
| DeepSeek 并发分类、有界滚动池、按模型顺序提交 | 单工具场景采用串行等价 | 生图是计费、状态相关且“最近 N 张”依赖顺序的 exclusive 工具，故不并行；结果天然按模型调用顺序提交。增加其他并行安全工具前不得直接复用这一简化。 |
| DeepSeek 声明式工具 deadline 与协作取消 | 部分采用 | 图片工具声明独立 240 秒供应商请求期限并传入生成/编辑 HTTP 调用；当前同步 `urllib` 客户端没有贯穿任务取消信号，无法承诺 Harness 的 cooperative quiescence。 |
| DeepSeek 通用 pre/guard/around/post/finalize/observe 插件流水线 | 暂未整体移植 | 当前只有隔离的 `generate_image`，参数门、预算、执行、结果校验和观察仍是显式固定流水线；扩展为多工具平台时应抽成通用注册与单调 guard，不能继续堆在模型循环中。 |
| DeepSeek 完整会话事件重建、压缩后续跑和请求重放不变量 | 部分采用 | 已增加完整请求组成计量和只裁剪旧失败工具结果的确定性压缩，并保持 call/result 配对；仍不保存可无损重建的全部 provider 原始请求、不做模型摘要，也不支持进程崩溃后从未完成工具调用原位续跑；不得把现有账本表述为完整 session replay。 |

本平台只允许在两者共同闭环上增加教学业务约束：

1. 能力登记和任务级用户开关决定工具是否可以暴露，不决定内容是否需要图片；
2. 负责当前答案或题目的主模型看到完整任务后，自主选择零次或多次调用图片工具；
3. 图片工具返回真实像素及内容寻址资产标识，实际像素必须进入同一主模型后续上下文；
4. 主模型选择来源原图时，平台只允许任务内已登记路径并把原始图片字节送入图片编辑接口；未选参考图才从零生成。单次最多 5 张，编辑失败不得静默改成从零生成；
5. 只有主模型已经看过并在结构化结果中明确采用的资产标识可以进入题目、答案和 Word；
6. 重试、审核修复、任务恢复及模型切换必须保留上述闭环；没有已验证的同能力模型时明示失败，不得静默改走纯文本或传统绘图；
7. 传统程序绘图是用户选择的另一条隔离链路，不得作为主模型工具失败后的隐式降级。

遇到新问题时必须先形成“Codex 直接实现、DeepSeek 通用合同、本项目教学约束”三列对照，再决定最小完整修改。若上游源码已经变化，以当前官方实现为准并更新本节查阅日期、能力登记、回归和变更账本；禁止用关键词、题型规则、第三方分类器或代理主观判断取代主模型调用决策。

### 1.3 Token 计量与上下文压缩边界

2026-09-03 针对任务调用预算再次动态核验：OpenAI Codex `728cb12fe5794b0c3a8e776fb4994b1650b973a8` 的 `codex-rs/core/src/responses_retry.rs` 将传输重试限制、指数退避、`Retry-After` 和回退作为独立策略，`codex-rs/core/src/rollout_budget.rs` 另以加权 token 消耗控制 rollout；DeepSeek Harness `76fda729799fe9b3848dbe2c211d4b231032b81e` 的 `packages/llm/llm-retry/src/index.ts`、`packages/llm/llm/src/retry-policy.ts` 和 `packages/llm/llm-retry/README.md` 同样采用服务商归属的有限重试、瞬时错误分类、指数退避/抖动、可取消等待和耐久重试事件，并明确重试会重复计费、多个有限预算会叠加。两者都没有按教学题量和教材证据阶段估算整项任务调用次数，因此本平台保留独立的任务级硬预算，并作以下必要扩展：

- 题目结构确认后再按任务类型、实际题量及教材证据链计算正常调用预估；默认上限为预估量的 180% 并向上取整，同时保持 120 次下限和 500 次硬上限。小任务不降低原保护边界，大任务可容纳逐题规划、证据选择、答案生成和有限瞬时失败。
- `QUALITY_MODEL_CALL_HEADROOM_PERCENT` 可在 100%–200% 内调整默认余量；显式设置 `QUALITY_MAX_MODEL_CALLS_PER_RUN` 时视为运维固定上限并优先于动态计算。最终预算和预估量写入任务遥测，且同一运行首次模型请求后冻结，不随中途环境或阶段变化漂移。
- 调用次数、token、总耗时和服务商熔断继续是彼此独立的硬边界；放宽调用次数不等于允许无限重试、无限费用或绕过不稳定通道的冷却。失败请求仍计入调用次数，避免供应商故障形成无界计费循环。

2026-09-03 重新对照 OpenAI Codex `fc953e5234f2452e393310b2be2b29a482c4d907` 的 [`responses_retry.rs`](https://github.com/openai/codex/blob/main/codex-rs/core/src/responses_retry.rs)、[`retained_context.rs`](https://github.com/openai/codex/blob/main/codex-rs/history/src/retained_context.rs)，以及 DeepSeek Harness `49a606bc5b5934603f22a26957a07dc799ab0291` 的 [`llm-retry`](https://github.com/deepseek-ai/deepseek-harness/blob/master/packages/llm/llm-retry/src/index.ts)、[`agent-loop`](https://github.com/deepseek-ai/deepseek-harness/blob/master/packages/core/agent-loop/README.md) 和 [`session-persistence`](https://github.com/deepseek-ai/deepseek-harness/blob/master/packages/session/session-persistence/README.md) 后，平台采用以下边界：

- Token Meter 独立于压缩策略，计量实际请求中的 system/user/assistant/tool 消息、工具 schema、历史工具结果、图片估算和结构开销；同一服务商通道与模型累计至少 3 条 provider-reported usage 后，使用中位比例校准本地估算。现有任务质量预算仍保持原权威，新增完整计量先作为并列观察，防止估算变化提前提高失败率。
- DeepSeek Harness 会先裁剪超大工具结果、重新计量并保持工具调用/结果边界平衡；本平台当前只在主模型工具循环超过质量预算时，确定性压缩较旧且失败的工具结果正文，保留最近 2 条失败及所有调用身份。成功结果、图片像素、采用资产、题干、答案约束、教材/真题证据、用户消息和工具 schema 均不可压缩。
- 通用 JSON 重试只允许原服务商、原模型、原协议、原消息和原思考深度的再试；不得关闭思考、改用备选模型或调用业务证据压缩回调。确定性鉴权、权限、配额、模型/端点和明确参数错误立即停止；仅已分类为可重试的网络/超时/限流/5xx 故障在原路由再试。
- 可重试故障使用有界指数退避，并尊重服务商 `Retry-After`；同一“服务商 + 模型 + 协议”连续 3 次可重试故障后进入冷却和单探针，不得阻断同服务商的其他模型或协议。代理网关明确表示“当前账号组无可用账号”的 404 属于可恢复路由池故障，不等同于模型永久不存在。
- 考查内容规划中的每题临时故障最多使用原路由共 3 次尝试；部分题失败时保留其他成功题和可见风险，全部题均失败时任务明确失败并提示用户稍后重试，不再把本地关键词兜底伪装成模型规划成功。
- Responses/Messages 协议不支持时，只有通道已显式允许、上游明确返回端点级 404/405/501、请求为纯文本且同一服务商/模型时，适配器才能单次切换到 Chat；模糊 400、超时、429、5xx、图片、工具与业务 JSON 修复不得触发协议切换。内置 Responses 路由默认不允许回退；灵算 Gemini 使用已登记的 Chat 路由直达。
- 当前不调用模型生成上下文摘要，也已删除教材证据选择重试中的截断式压缩；不宣称具备 Codex/DeepSeek 的完整会话压缩、跨进程 replay 或上下文溢出自动重放。不可分割的教材/真题证据若超出硬上下文容量，必须保留原文并显式失败或等待用户授权的等价大上下文路由，不得用摘要换取成功。未来扩大到历史对话摘要前，必须先用固定真实任务语料验证答案质量和任务完成率，并取得用户确认。

### 1.4 多图输入数量与省略边界

2026-09-03 按 1.1 的官方身份重新核验：OpenAI Codex `fc953e5234f2452e393310b2be2b29a482c4d907` 仍逐图准备附件；DeepSeek Harness `49a606bc5b5934603f22a26957a07dc799ab0291` 的附件和输出保留合同继续要求明确保留内容、精确省略量和恢复入口，不允许把容量截断伪装成完整输入。

本平台据此采用以下边界：

- `quality_limits.<stage>.max_images` 是既有质量建议，只进入诊断；第 9 张以后图片不得因此设置 `too_many_images`、阻断请求或静默截断。只有模型能力登记的 `limits.max_images_per_request` 才是可执行硬上限。
- 生题材料单次模型请求保持 24 张图的边界，但同一任务可按原始顺序保留并分批分析最多 600 张页图/内嵌图；每批继续使用原服务商、原模型、原协议和原思考深度。超过总上限必须明确停止并要求拆分文件，不得只取前 24 张后声称已完整分析。
- 短材料把全部已接收图片交给同一次主模型分析，保留跨图比较；长文本只按文本段落长度分段，DOCX 图片依靠稳定 `IMAGE_REF` 编号进入相关段，未锚定的独立图片也必须至少进入一个分析段。
- 本平台实现的 600 张是任务内按 24 张窗口的确定性分批，不是 DeepSeek 的历史附件最旧前缀卸载，两者不得混称。任一页的结构化与视觉表示均失败时仍必须显式停止。

### 1.5 文件输入与局部表示失败

2026-09-03 动态核验 OpenAI Codex `fc953e5234f2452e393310b2be2b29a482c4d907` 的 `codex-rs/core/src/mcp_openai_file.rs`、`codex-rs/history/src/retained_context.rs` 与 `codex-rs/core/src/responses_retry.rs`，以及 DeepSeek Harness `49a606bc5b5934603f22a26957a07dc799ab0291` 的 `packages/core/agent-loop/README.md`、`packages/session/session-persistence/README.md`、`packages/util/output-retention/README.md` 和 `packages/llm/llm-retry/src/index.ts`。最新上游没有推翻 1.3/1.4 的重试和附件结论，但进一步明确以下差异：

- Codex 普通 `UserInput` 没有通用本地文档变体；Apps/MCP 工具仅对 `openai/fileParams` 声明的参数读取本地文件、上传至 OpenAI 文件存储并改写工具参数。它不提供 Word 内部段落、公式或图表的局部成功报告。
- DeepSeek Harness 当前附件合同只覆盖图片：保存内容寻址的耐久标准化对象，再按路由生成确定性请求版本；Files 上传解析失败时以相同请求图片重建整次请求为内联 Base64，陈旧文件 ID 只允许一次替换尝试。它没有 Word 文档解析或 Word 局部失败恢复合同。
- 本平台因真题和教材质量要求采用必要扩展：原文件继续保存在用户数据目录；文本、公式、表格、内嵌图片和页面视觉是独立且可诊断的表示；一种表示失败时只使用同一原文件生成的等价表示补偿，不换材料、不摘要题面、不换模型。Word 公式结构化失败时生成原始页面视觉并只送入受影响题目的视觉理解链路；该页面视觉不得被误当作原题配图写入最终 Word。
- PDF 同时保留文字和有界页面视觉；DOCX 优先结构化提取 OMML、表格与内嵌图片，在文字不足、公式结构退化或直接教材 Word 含复杂视觉内容时生成页面视觉补偿；图片保留原始像素；TXT/Markdown 保留完整文字并报告解码替代。所有表示均记录 `ready/degraded/failed/not_required` 和局部失败码。
- 至少一种足以覆盖必要内容的表示可用时继续任务并保留风险；同一必要内容的结构化与视觉表示都不可用时明确停止，不把空白或缺失内容交给模型。超过有界页图预算时列出未覆盖页，不能宣称已完整分析。

### 1.6 任务恢复、工具结果与 Word 发布边界

2026-09-05 10:44 CST 重新核验上表两个官方远程及本地 `origin`：OpenAI Codex 阅读 `codex-rs/core/src/tools/context.rs` 的模型可见工具输出和 `codex-rs/codex-api/src/provider.rs` 的有界传输重试；DeepSeek Harness 阅读 `packages/llm/llm-pi-ai/src/context.ts` 的 `toolResult` 上下文组装、`packages/session/session-persistence/README.md` 的失败结果恢复与耐久事件合同。两者都把工具/校验失败作为后续回路可见输入，而不把“一次 HTTP 成功”当成任务成功。Codex/DeepSeek 都不提供本项目专有的 Word/OMML 公式语义门，因此本项目保留确定性 Word 预检，但必须把精确失败只回传给受影响题目的最新候选，有界重试并每轮复验；程序能无损解决的幂等规范化和无歧义工具参数不增加模型调用。

- 全平台文本和结构化模型请求共用传输错误分类；只有未接收部分输出的可重试故障才使用原路由、原请求有界重试，等待期可被任务取消中断。结构化输出修复只处理已完成响应的 JSON 语法/合同错误，不再把网络错误文本伪装成模型上一版答案。
- 工具调用在执行前持久化 `started`，结果后持久化 `result`；恢复时同一稳定会话中只有 `started` 而无 `result` 的调用标记为 `TOOL_OUTCOME_UNKNOWN`，禁止自动重放可能已产生外部副作用的操作。
- 任何 Word 修复改写答案片段后，必须针对最新片段重跑内容质量门并绑定 SHA-256；未渲染的 Word 只能是待复核候选件。最终验收先在中性候选名上完成，再通过同目录原子替换发布；发布前后保存候选与成品哈希及事务状态，交付 ZIP 也必须先在临时名上完整校验后再原子替换。

### 1.7 结构化内容到 Word 的输出合同

2026-09-03 核对 OpenAI Codex `fc953e5234f2452e393310b2be2b29a482c4d907` 的 `codex-rs/core-plugins/src/artifact_operation.rs` 和结构化输出指引：Codex Harness 只跟踪 `documents` 产物操作，不在核心中实现 LaTeX→DOCX；官方 documents 运行时要求生成后渲染、检查页图并在缺陷时重做。DeepSeek Harness `49a606bc5b5934603f22a26957a07dc799ab0291` 的工具输出使用声明 schema 和运行时校验，但同样没有 DOCX/OMML 转换层。本平台因原生 Word 公式交付需要作必要扩展：

- 答案摘要保留人可读文本，同时在生成、修复和历史迁移边界确定性生成 `answer_summary_segments`；数学内容只以 `formula_ref` 指向 `formulas`，DOCX 渲染器优先消费已校验的结构段。
- 多行 `$$...$$` 是一个完整显示公式，内部换行只作排版空白；不得按行或按 `\text{}` 内容拆成普通文本。
- 结构校验同时检查摘要段类型、原始 LaTeX 分隔符残留和公式引用完整性；DOCX 转换错误必须包含题目 ID 与 `answer_summary` 字段。
- 旧检查点在 Word 本地修复阶段升级为同一结构合同，再重建、审计和渲染；不增加模型请求，不改写已确认的答案语义。

### 1.8 文档工具的 Harness 执行与纠错合同

2026-09-03 实施前动态核验了四个上游。OpenAI Codex `https://github.com/openai/codex.git` 默认分支 `main`、提交 `6d7f6dcd2285de70a3892d4f05b2a8ff44aa3350`，实际阅读 `codex-rs/core/src/tools/parallel.rs`、`context.rs` 和 `orchestrator.rs`；DeepSeek Harness `https://github.com/deepseek-ai/deepseek-harness.git` 默认分支 `master`、提交 `76fda729799fe9b3848dbe2c211d4b231032b81e`，实际阅读 `packages/core/agent-loop/src/tool-calls.ts`。两者共同要点是：工具调用和结果以同一 `call_id` 配对，失败也作为正式结果回到上下文，已开始的调用在取消或异常时不能无结果消失。

文档执行层另外核验了两个 MIT 许可的社区项目：AIOffice `https://github.com/onecer/AIOffice.git` 默认分支 `main`、提交 `5159cb743193fc445862c1bd450cc65686946bbf`，阅读 `AIOffice.Core/Envelope.cs`、`AIOffice.Cli/Program.cs`、`AIOffice.Core/Equations/LatexParser.cs` 和 Word 公式测试；docx-mcp-server `https://github.com/knorq-ai/docx-mcp-server.git` 默认分支 `main`、提交 `9b36ea3e5aaa71af0acb13328d05e439ebeb7e1c`，阅读 `src/engine/docx-io.ts`、`file-lock.ts`、`anchors.ts` 和结构编辑测试。本项目未复制上游源码、未增加 .NET/Node 运行依赖，只采用其稳定合同思路：

- 所有文档操作统一返回 `ok/data/error/meta`；错误必须有稳定代码、主责层、是否可重试和可执行的下一步建议。事件只保存位置、合同问题和内容哈希，不保存题目或答案正文。
- 真题 Word 的“构建 → 校验 → 本地无损修复 → 有界模型修复 → 重建”每次尝试都产生可耐久配对的工具结果；练习 Word 的构建、合同校验和原子落盘作为一个文档事务。
- Word 到 PDF/PNG 的渲染和一致性审计同样是结构化工具操作；候选件、页图和失败结果保留到同一任务证据链，未通过不发布。
- `cases/aligned/matrix/array` 等环境必须先作为完整公式语法树处理，不允许按内部箭头分割。普通长反应式的视觉换行必须先验证所有候选段都可独立转为 OMML，任一失败则原子回退为未拆分公式。这是本项目试题排版规则，不交给模型猜测。
- 只有确认属于模型内容的问题才能进入原模型有界修复；程序在排版阶段改坏正确公式时，必须本地修复或回退，不增加模型调用来掩盖缺陷。
- 内容修复不再只收到“计算存在矛盾”这类泛化结论：首轮请求同时携带程序重算的具体公式索引、正确值和被拒绝值；候选验收失败后返回 `ok/error/meta` 结构化校验结果、候选哈希和可执行建议，下一轮必须基于最新完整候选继续。默认最多 3 轮、硬上限 4 轮，每轮均重跑确定性合同，未通过的候选不落盘。

### 1.9 Word 工具 A/B 执行基线

2026-09-04 再次动态核验 OpenAI Codex `https://github.com/openai/codex.git` 默认分支 `main`、提交 `f46671b14aa3bc37d4ee9a67c06385cb9ec8e2d3`，阅读 `codex-rs/core/src/tools/context.rs`、`parallel.rs`、`orchestrator.rs` 和 `executed_tool_calls.rs`；核验 DeepSeek Harness `https://github.com/deepseek-ai/deepseek-harness.git` 默认分支 `master`、提交 `76fda729799fe9b3848dbe2c211d4b231032b81e`，阅读 `packages/core/agent-loop/src/tool-calls.ts` 与 `agent.ts`。两者仍要求工具调用/结果配对、失败结构化返回、并发受控及恢复时不盲目重放结果不确定的变更。

Word B 版直接核验 `https://github.com/iOfficeAI/OfficeCLI.git` 默认分支 `main`、提交 `b94f3906fd52d450c64f8e40370e376b9e15079e`，采用 1.0.147 运行时；阅读 `sdk/python/officecli.py`、`src/officecli/CommandBuilder.Batch.cs`、`CommandBuilder.Check.cs`、`Handlers/WordHandler.cs`、`ResidentServer.cs` 和 `skills/officecli-docx/SKILL.md`。本平台对应关系如下：

- A 保留当前 Python/OMML 工具链；B 使用 OfficeCLI 实际执行 DOCX `create/batch/save/validate/close`，不是先生成 A 再用 OfficeCLI 做一次验证。
- B 的命令计划直接包含段落、run、equation、picture、table、section、header、footer 和 field；试题、答案、题图可见性、公式全斜体、中文字体、页面尺寸及题号区块仍由平台教学合同决定。
- 批处理采用 OfficeCLI 默认原子模式并启用 `--stop-on-error`；resident 变更显式 `save` 后再 schema 校验，最后 `close`，失败候选删除且不自动回退 A。
- 上游安装脚本会修改 PATH/shell 和安装 Agent skills，超出平台运行时边界；因此平台只按需下载固定版本二进制，校验官方发布 SHA-256 后保存到用户数据缓存。这是必要差异，不复制安装脚本副作用。
- 默认选择 B，`config/word_tool.json`、用户数据覆盖文件或 `ANSWER_BOOK_WORD_TOOL_VARIANT` 可明确切换 A/B；练习缓存键包含版本。完整实验记录见 `docs/operations/WORD_TOOL_AB_TEST.md`。

## 2. 强制登记字段

每个模型必须补充以下内容，缺失字段写 `unknown`，不得留空或根据名称猜测：

| 类别 | 强制字段 |
|---|---|
| 身份 | 服务商、通道类型、base URL 类别、模型 ID、显示名、协议、版本/快照、是否允许自定义 ID |
| 生命周期 | 可用/预览/实验/下线、官方发布日期、平台启用日期、最后验证日期、负责人 |
| 原生输入 | 文本、图片、音频、视频、PDF、其他文件；每种能力分别声明 |
| 输入限制 | 官方上下文 tokens、平台按任务实测的质量输入预算、单图大小、图片数量、支持格式、文件大小、页数、URL/base64 支持 |
| 平台输入 | 原图直传、OCR 辅助、PDF 解析、PDF 渲染、Word 解析、多文件分批及汇总策略 |
| 输出 | 文本、图片、结构化数据；单次最大输出与平台安全上限 |
| 协议与参数 | Responses/Chat/Messages、流式、JSON 模式、schema、推理模式、必须省略或固定的参数 |
| 任务适配 | 每个任务阶段的 `recommended/allowed/limited/forbidden` 及原因 |
| 质量策略 | schema 校验、业务规则、重试次数、分批阈值、复核模型要求、失败是否可降级 |
| 运行限制 | 超时、并发、RPM/TPM、账户限额、地区限制、免费池或动态上游风险 |
| 错误处理 | 鉴权、余额、限流、并发、内容安全、输入过大、模型不存在、超时和上游故障的用户文案 |
| 证据 | 官方文档 URL、查阅日期、测试用例、脱敏请求/响应摘要、测试结果、已知问题 |

## 3. 强制测试矩阵

| 测试 | 所有文本模型 | 声明视觉的模型 | 图片生成模型 |
|---|---:|---:|---:|
| 最小文本调用 | 必须 | 必须 | 不适用 |
| 严格 JSON 输出并通过 schema | 必须 | 必须 | 不适用 |
| 接近平台安全上限的长输出 | 必须 | 必须 | 不适用 |
| 单图理解 | 不适用 | 必须 | 不适用 |
| 多图顺序与跨页关联 | 不适用 | 必须 | 不适用 |
| 公式、表格、图表与低清扫描件 | 不适用 | 必须 | 不适用 |
| 教学图片生成 | 不适用 | 不适用 | 必须 |
| 生成图片的题意一致性复核 | 不适用 | 作为复核模型必须 | 必须 |
| 非法参数与不支持能力 | 必须 | 必须 | 必须 |
| 鉴权、限流、并发、超时、上游错误翻译 | 必须 | 必须 | 必须 |
| 真实“材料理解 → 蓝图 → 生题”样本 | 至少适用阶段 | 至少适用阶段 | 若参与流程则必须 |
| 真实“题面理解 → 答案 → 复核”样本 | 至少适用阶段 | 至少适用阶段 | 不适用 |

测试 Key 只能存放在本地密钥配置或环境变量中，严禁写入示例配置、测试快照、日志、文档和提交记录。

## 4. 接入门禁

模型只有同时满足以下条件，才可以出现在正式任务的自动路由中：

- 能力表不存在 `unknown` 的关键字段；
- 当前通道取得 A 级证据；B 级模型须在对应真实任务样本通过后升级为 A；
- 所声明的输入模式真实调用通过；
- 对应任务阶段的真实样本通过质量基线；
- 错误已转换为用户可理解、可行动的文案；
- 有明确的超时、重试、限流和同能力降级策略；
- 回归测试覆盖模型能力声明与任务前置检查。

仅“连接测试成功”的模型最多进入人工显式选择，不得自动用于生产任务。

## 5. 任务开始前的强制校验

平台在创建任务前必须完成：

1. 识别输入内容类型、图片数量、文件页数和预计 token 量；
2. 根据任务阶段读取模型能力，而不是读取服务商级 `supports_vision`；
3. 判断主模型是否能接收任务必需的原始证据；
4. 计算是否需要预处理、分批或摘要，并向后续步骤保留来源映射；
5. 如需换模型，必须换到经过同一任务阶段验证的模型；
6. 不允许静默丢弃图片、截断文本、关闭必要推理或取消结构化输出；
7. 无兼容路径时在任务开始前告诉用户原因和解决办法。

模型官方最大上下文只能作为硬上限，不能直接作为平台组装目标。材料理解、蓝图、正式生成和质量审查必须分别登记 `recommended_input_tokens`；超过质量预算时优先按来源和题目边界拆批，禁止为了减少调用次数把整份材料塞入一次请求。

每次调用还必须生成可审计的上下文计划，至少记录任务阶段、模型、绑定题目、预计输入量、质量预算、文字证据、视觉证据和缺失证据。任何不可由摘要替代的必要图片未进入上下文时，应在请求发出前停止该批次。

该规则适用于平台所有模型调用，不限于生题流程。统一调用入口必须覆盖：题面理解、考查内容规划、教材证据确认、答案生成、正确性复核、按题/知识点生题、格式修复、插图结构规划、作图代码生成和插图视觉审查。业务模块可以进一步缩小预算或拆批，但不得绕过统一的模型能力、模态、图片数量、证据完整性与质量预算记录。

推荐的判断顺序：

```text
任务阶段与输出合同
  → 用户输入类型和容量
  → 主模型能力是否满足
  → 是否可通过无损平台转换满足
  → 是否存在已验证的同能力模型
  → 执行 / 明示降级 / 阻止任务
```

## 6. 输出质量门禁

服务商返回 HTTP 200 不代表任务成功。平台至少进行三层检查：

1. **协议层**：响应完整、未截断、没有混入推理标签或服务商异常文本。
2. **结构层**：JSON/schema、必填字段、类型、题号和引用格式正确。
3. **业务层**：题量守恒、答案与解析一致、知识点与难度符合蓝图、视觉引用仍可定位、局部重生未误改其他内容。

高风险任务还应使用与生成模型相互独立的正确性复核；如果只能使用同一模型，界面和日志必须标记为“同模型自检”，不能表现成独立复核。

## 7. 降级与错误原则

- 降级必须保持任务所需能力等价。例如视觉模型限流，只能切换到已验证视觉模型，不能切换到文本模型后忽略图片。
- 网络错误可重试；输入不兼容、参数不支持和内容过大必须先调整请求，不能原样重试。
- 免费池或动态上游模型不能作为唯一的最终复核模型。
- 所有服务商原始错误只写入脱敏诊断信息；用户只看到可理解的原因、影响范围和下一步操作。
- 自动降级、模型切换、输入转换、截断或分批必须写入任务诊断记录。

### 7.1 请求级结构化输出与 LiteLLM 影子边界

- 正式结构输出以 Pydantic 模型为真源；每次调用从该模型生成独立 JSON Schema，并随本次请求传给协议适配器。Responses 与 Anthropic Messages 在通道支持时发送原生 `json_schema`，Chat 兼容协议将同一 schema 放入模型可见 system 消息。任何路径最终都必须经过本地 Pydantic 校验。
- 结构校验不得依赖进程级可变 schema/工具注册表、惰性全局初始化或跨任务共享的临时状态；并发任务只能持有各自请求内的 schema、纠错历史和结果。确定性校验错误最多一次回给同一服务商、同一模型、同一协议纠正，不允许借结构修复换模型或换路由。
- 原生协议的 schema 仅作为生成约束，不作为最终可信边界：代理网关可能只支持 JSON object、可能拒绝部分 JSON Schema 关键字，或忽略 `strict`。因此协议明确报格式参数不支持时可按既有规则退回同请求的 prompt schema，最终仍由本地 Pydantic 判定；结构正确不代表答案语义正确。
- 所有底层请求继续调用平台 `LLMClientProtocol`，不得自行创建绕过调用次数、Token、超时、取消、并发和用量账本的第二套客户端。一次可恢复校验失败不得向任务进度上报最终失败；只有纠错预算耗尽才上报失败。
- LiteLLM 当前只用于灵算影子对照，不参与主路由、重试、降级或结果选择。默认 10% 样本、单 worker、最多 4 个待处理、零重试；队列满、图片内嵌或影子失败时直接跳过/记录，正式响应不受影响。
- 影子调用仍是实际付费调用并计入平台账本；影子日志只能保存服务商/模型、耗时、用量、状态、JSON 可解析性和内容摘要，不保存提示词、原始响应或密钥。

#### 2026-09-05 请求级 schema 并发核验

核对时间：2026-09-05 20:08 CST。OpenAI Codex 官方远端 `https://github.com/openai/codex.git`，默认分支 `main`，完整 SHA `ddf04ad26789d040f9ef6a96736f76602e35a6cc`；阅读 `codex-rs/codex-api/src/common.rs` 与 `codex-rs/core/tests/suite/json_result.rs`，确认最终输出 schema 是单次 Responses 请求的 `text.format` 内容。DeepSeek Harness 官方远端 `https://github.com/deepseek-ai/deepseek-harness.git`，默认分支 `master`，完整 SHA `d347e703908d0406b7a7ef80e3a0e594d86b2215`；阅读 `packages/subagent/subagent-in-process-driver/src/structured.ts`、`src/index.ts` 与 `tests/structured.spec.ts`，确认结构工具在子任务发布前按子任务作用域同步附加，且并发结构任务各自保持自己的 schema。

本平台采用相同的作用域原则，但不复制 Harness 的完整 agent/tool runtime：`structured_completion` 创建请求局部 schema 和纠错消息，Responses/Messages/Chat 三类适配器只消费本次调用显式参数，本地 Pydantic 是最终边界。这样消除 Instructor 1.16.0 v2 惰性全局注册表在多 worker 冷启动时的竞态。代价是 Chat 兼容通道会重复携带 schema、增加少量输入 Token；原生 schema 子集在不同代理网关上不完全一致；一次纠错仍增加调用成本。上述代价必须由任务预算、格式不支持回退、一次纠错上限和协议回归共同约束。

## 8. 配置与代码要求

后续实现时必须将当前 `model_capabilities: [text, vision]` 升级为逐模型结构化记录，并满足：

- 内置模型不得通过名称规则推断能力；
- 服务商级能力只能描述接口是否具备某功能，不能覆盖模型级结论；
- 任何新增 `model_options` 必须同时新增能力记录和测试；
- 每个“服务商通道 + 模型 ID”必须显式登记并实测 `api_protocol`，区分 `responses`、`chat_completions`、`anthropic_messages` 或其他原生请求类型；不得按模型名称推断，也不得跨服务商、跨协议复用结论；
- 每个已登记请求类型必须列出 `supported_thinking_modes`、`default_thinking_mode`、可选的 `thinking_minimum`、线上参数映射及验证日期；“支持思考”这一布尔值不能代替可选档位清单；
- 新模型在请求类型或推理强度仍为 `unknown`、未完成真实探针、默认档位不在支持集合中，或用户所选档位没有对应线上参数时，不得发布到任务选择器；
- 任务界面只能展示当前精确路由支持的推理档位，并把最终采用的模型、请求类型和推理强度共同写入任务与调用账本；
- 重试、结构纠错、恢复和并行分支必须保持原请求类型与原推理强度；协议切换只能走已单独验证并显式允许的等价路径；
- 示例配置与实际本地配置使用同一 schema，但实际密钥永不写入能力表；
- 能力表变更应触发相关模型合同测试；
- 模型或上游版本变化后，原验证结论自动过期并重新测试；
- 管理页面应能展示“已验证能力、验证日期、适用任务、限制”，而不是只展示营销名称。

2026-09-08 已落地第一阶段运行合同：真题解析将教材依据与题目解析的模型、推理强度和请求类型分别固化到任务记录，恢复与并行分支继续使用创建时快照；题目解析的正确性复核沿用题目解析强度。按题出题和知识点出题各自只保存一个主模型、一个推理强度和一个请求类型，不再读取真题解析页的全局 Thinking 值。前端按逐模型 `supported_thinking_modes` 和 `thinking_minimum` 收窄选项，并显示实际请求类型；尚未迁移到新字段的既有路由保留原可选范围以兼容历史配置，但后续新增路由不得使用该兼容分支。服务端拒绝低于已登记最低值、超出显式支持集合、任务中擅改协议以及非 Responses 的 GPT 文本路由。

## 9. 发布审查清单

- [ ] 新模型有独立能力记录，不依赖同系列模型推断
- [ ] 已对该供应商通道和模型分别实测请求类型，并明确登记 Responses、Chat Completions、Anthropic Messages 或其他原生协议
- [ ] 已实测并登记全部可用推理档位、默认档位、最低档位及每个协议的参数映射；未知或未测档位不向用户展示
- [ ] 用户选择器只显示精确路由支持的推理档位，任务与调用账本能追溯实际模型、请求类型和推理强度
- [ ] 重试、恢复、并行分支和结构纠错保持原请求类型与推理强度；任何协议回退均已独立验证、显式允许且能力等价
- [ ] 官方文档与查阅日期已登记
- [ ] 输入、输出、上下文、图片和结构化限制已填写
- [ ] 文本/图片/JSON/长输出测试按声明完成
- [ ] 至少一个对应的真实任务流程通过
- [ ] 任务前置兼容性检查已覆盖
- [ ] 用户错误文案和诊断信息已覆盖
- [ ] 降级路径能力等价且可追踪
- [ ] 测试密钥、请求内容和用户材料没有进入仓库
- [ ] 能力表、代码、测试、设置页说明同步更新

### 2026-09-05 灵算 GPT-6 Astra 准入

核对时间：2026-09-05（Asia/Shanghai）。官方远端 HEAD 保持为 OpenAI Codex `https://github.com/openai/codex.git` 默认分支 `main`、完整 SHA `ddf04ad26789d040f9ef6a96736f76602e35a6cc`，阅读 `codex-rs/core/tests/suite/json_result.rs` 的 Responses `json_schema` 最终输出合同；DeepSeek Harness `https://github.com/deepseek-ai/deepseek-harness.git` 默认分支 `master`、完整 SHA `d347e703908d0406b7a7ef80e3a0e594d86b2215`，阅读 `packages/subagent/subagent-in-process-driver/src/structured.ts` 及对应结构化输出测试。上游共同原则是让 schema 随请求或执行作用域隔离；本项目已改为请求级 schema、协议适配器约束、本地 Pydantic 校验和一次有界同路由纠错。

灵算 `gpt-6-astra` 已在该代理通道真实通过普通 JSON、`AnswerDraftOutput`、64×64 纯色 PNG 图片理解和 Responses 原生函数调用探测，因此登记为文本+图片模型并开放现有主模型工具循环；默认模型仍为 `gpt-5.6-sol`。本次证据只支持显式选择下的 limited 任务资格，不代表完整真题、按题出题、知识点出题、长输出、限流和错误恢复质量已经验证，也不用于自动替换其他模型。

### 2026-09-05 取消独立生成图视觉审查

用户明确取消历史遗留的生图后独立视觉审查及其自动修复。本次实施前动态核验 OpenAI Codex `https://github.com/openai/codex.git` 默认分支 `main`、SHA `459a79eb85400af759e9220c7bafb4429ae07516`，阅读 `codex-rs/core/src/tools/context.rs`；DeepSeek Harness `https://github.com/deepseek-ai/deepseek-harness.git` 默认分支 `master`、SHA `d347e703908d0406b7a7ef80e3a0e594d86b2215`，校验本地 origin/HEAD 并阅读 `packages/core/agent-loop/src/tool-calls.ts` 与 `packages/llm/llm-pi-ai/src/context.ts`。共同参考是工具结果按调用身份回到模型上下文；本项目现有真实图片回灌、主模型明确采用和有界工具循环保持不变。取消独立审查是用户确定的产品合同，不将其虚构为上游保证图片正确。保留题目/材料识图、内容审查及文件存在、可解码、资产绑定、Word/PDF 等确定性检查；历史独立视觉报告仅作诊断档案，不再参与新运行或重新验收。

### 2026-09-05 用户取消默认整任务 token 硬上限

核对时间：2026-09-05（Asia/Shanghai）。通过官方远端 HEAD 动态确认并校验本地 origin：OpenAI Codex `https://github.com/openai/codex.git`，默认分支 main，完整 SHA `ddf04ad26789d040f9ef6a96736f76602e35a6cc`；DeepSeek Harness `https://github.com/deepseek-ai/deepseek-harness.git`，默认分支 master，完整 SHA `d347e703908d0406b7a7ef80e3a0e594d86b2215`。读取前者 `codex-rs/core/src/rollout_budget.rs`，后者 `packages/llm/llm/src/retry-policy.ts`。Codex 的此预算对象在未 configure 时不触发预算耗尽，并在配置后按权重累计使用量和提醒；DeepSeek 将网络重试作为独立的提供商策略，normal 模式有限次退避。两者均不构成教学任务固定 200 万 token 阈值的依据。

本项目此前默认固定 200 万 token 与正常多阶段教材任务冲突，用户明确要求取消默认值。现以 0 表示不启用整任务 token 硬上限；仍累积真实 usage，显式设置正数环境预算保持生效。调用次数、运行时间、提供商熔断、单次请求超时、内容修复及工具循环上限不变。不修改模型输入、响应、上下文、质量门或已完成内容，不增加失败重试次数；没有付费模型验证。此前任务级 token 必须始终有固定上限的历史约束被本次用户指令取代。
## 智能路由产品分类

智能路由只按能力分为两类：多模态模型路由和生图模型路由。Gemini、GPT 及后续其他非生图智能路线统一归入多模态模型路由，其候选模型必须支持文字输入、图片输入和文字输出；不再新增纯文本智能路由。多模态路线不得混入图片生成模型，“多模态”也不表示具备图片生成能力。图片生成模型后续接入独立的生图智能路由，候选池、调用链和用户展示与多模态路线分开。模型家族、供应商和请求协议仍作为多模态候选池内部的精确路由属性，不再作为产品层面的能力分类。
