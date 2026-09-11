# 供应商模型与并发复验（2026-09-11）

本记录只代表测试时点，不构成供应商长期 SLA。测试使用真实账户请求，但不记录密钥、提示词、响应正文或生成图片。

## WawAPI 全模型协议复验

| 分组 | 模型 | 配置协议 | 结果 | 备注 |
|---|---|---|---|---|
| OpenAI | gpt-5.6-sol | Responses | 2/2 | Chat 也为 2/2 |
| OpenAI | gpt-5.6-terra | Responses | 2/2 | Chat 也为 2/2 |
| OpenAI | gpt-6-astra | Responses | 2/2 | Chat 也为 2/2 |
| Gemini | gemini-3.8-flash | Chat | 2/2 | Responses 也为 2/2 |
| Gemini | gemini-3.7-flash | Chat | 2/2 | Responses 也为 2/2 |
| Gemini | gemini-3.6-flash | Chat | 2/2 | Responses 1/2，出现一次供应商调用异常，不影响当前 Chat 配置 |
| Grok | grok-4.6 | Responses | 1/2 | 一次供应商网络异常；Chat 2/2 |
| Grok | grok-4.5 | Responses | 2/2 | Chat 也为 2/2 |
| GPT 生图 | gpt-image-2 | Images | 2/2 | 可用 |
| Gemini 生图 | gemini-3-pro-image-preview | Chat | 2/2 | 可用，但延迟波动大，约 30–288 秒 |
| Gemini 生图 | gemini-3.1-flash-image-preview | Responses | 2/2 | 可用，但延迟波动大，约 68–273 秒 |
| Grok 生图 | grok-imagine-image-2.0 | Images | 2/2 | 可用 |

结论：WawAPI 当前保留的 12 个模型均有成功的配置路由。Grok 4.6 和 Gemini 3.6 的非默认/默认协议各出现一次供应商瞬时异常，应由有限重试、供应商级降并发和明确责任提示处理，不能解释为平台参数错误，也不能无限重试。

## 灵算 Gemini 3.8 Flash

- `gemini-3.8-flash` 与 `gemini-3.8-flash-preview` 均返回路由不存在，不能接入。
- `gemini-3.8-flash-medium` 和 `gemini-3.8-flash-high` 可调用。
- 公开接入 `gemini-3.8-flash-medium`，显示名为“Gemini 3.8 Flash”。Chat 连续 2/2 成功；Responses 为 1/2，另一次是约 34 秒后的供应商网络异常，因此固定使用 Chat，不做协议回退。
- Chat 与 Responses 的原生图片输入、原生工具调用均已实测成功；平台登记为文本+视觉、支持工具调用、最低思考强度 medium。

## 绕过平台闸门后的并发复验

探针只在独立测试进程中绕过平台现有并发闸门，从而测量供应商账户/模型池，而不是测量平台当前限额。正式任务仍强制经过并发闸门。

| 通道 | 独立模型阶梯结果 | 8 并发混合复核 | 生产值 |
|---|---|---|---|
| WawAPI OpenAI | 3 个模型各在 4/8/12/16 并发共 40/40 成功 | 15/16 | 8 |
| WawAPI Gemini | 3 个模型各 40/40 成功 | 16/16 | 8 |
| WawAPI Grok | grok-4.5 40/40；grok-4.6 39/40 | 16/16 | 8 |
| 灵算共享文本池 | Gemini 40/40；GPT 在 8、16 并发各有一次波动 | GPT+Gemini 16/16 | 8 |
| DeepSeek | 40/40 | 未混合（单模型） | 8 |
| 百炼 | 40/40 | 未混合（代表模型） | 8 |

WawAPI Grok 在多个档位出现约 75–103 秒长尾；WawAPI Gemini 3.6 出现约 51 秒长尾。因此平台采用 8，而不是把短请求峰值 16 直接用于生产。

### 图片并发是独立模型池

| 通道 | 实测 | 生产值 |
|---|---|---|
| WawAPI GPT 生图 | 2 并发 2/2；4 并发 0/4 | 2 |
| WawAPI Gemini 生图 | 顺序调用各 2/2；负载后 2、4 并发均出现供应商过载/失败 | 1 |
| WawAPI Grok 生图 | 4 并发 4/4 | 4 |

图片通道不能继承文本通道的 8 并发。特别是 Gemini 生图，单请求可用与并发容量不足同时成立；当前失败责任属于供应商容量快照，平台必须串行并在连续失败时终止并明确告知用户。

## 本轮发现并修正的平台测试问题

1. 旧并发探针仍经过生产闸门，表面开启 16 线程时实际上可能只向供应商放行 2 路。探针现明确使用进程内绕过并在报告中记录 `platform_admission_gate_bypassed=true`。
2. 多进程并发写诊断记录时，一个进程可能删除另一个进程刚枚举的过期文件，导致成功的供应商响应被包装为平台失败。清理逻辑现容忍文件竞争，并补回归测试。
3. 协议复验报告可机械合并到能力登记表，避免人工只更新白名单而遗漏协议记录。

原始无敏感信息报告保存在本机 `/tmp/*20260911.json`；长期结论写入 `config/model_protocol_verification.json` 与 `config/provider_capacity_profiles.json`。
