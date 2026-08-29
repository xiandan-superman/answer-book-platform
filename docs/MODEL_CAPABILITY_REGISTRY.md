# 当前模型能力登记表（自动生成）

> 数据源：`config/model_capabilities.json`。请勿手工编辑本表；运行 `python3 scripts/sync_model_capability_docs.py` 重新生成。

能力等级：A＝真实任务流程已验证；B＝接口能力已验证、任务基线待补；C＝配置或通道声明；D＝未知/过期。

| 服务商 | 模型 | 类型 | 原生输入 → 输出 | 原生工具回路 | 结构化输出 | 推理 | 任务质量输入预算 | 任务适配 | 证据 | 最后验证 |
|---|---|---|---|---|---|---|---|---|---|---|
| DeepSeek 官方 | `deepseek-v4-flash` | text_generation | text → text | 未登记/禁用 | native_json | supported | 待验证 | 材料理解:forbidden；蓝图规划:allowed；正式生题:allowed；答案生成:allowed；正确性复核:limited；格式修复:allowed | B | 2026-08-27 |
| DeepSeek 官方 | `deepseek-v4-flash-vision-exp` | text_generation | text、image → text | 已验证（chat_completions，2026-08-29） | unknown | unknown | 待验证 | 材料理解:limited；蓝图规划:unknown；正式生题:limited；答案生成:unknown；正确性复核:unknown | C | unknown |
| 火山方舟 | `doubao-seed-2-1-pro-260628` | text_generation | text、image → text | 未登记/禁用 | unknown | unknown | 待验证 | 材料理解:limited；蓝图规划:limited；正式生题:limited；答案生成:limited；正确性复核:unknown | C | unknown |
| 火山方舟 | `doubao-seed-2-1-turbo-260628` | text_generation | text、image → text | 未登记/禁用 | unknown | unknown | 待验证 | 材料理解:limited；蓝图规划:limited；正式生题:limited；答案生成:limited；正确性复核:unknown | C | unknown |
| 火山方舟 | `doubao-seed-2-0-pro-260215` | text_generation | text、image → text | 未登记/禁用 | unknown | unknown | 待验证 | 材料理解:limited；蓝图规划:limited；正式生题:limited；答案生成:limited；正确性复核:unknown | C | unknown |
| 火山方舟 | `deepseek-v4-flash-260425` | text_generation | text → text | 未登记/禁用 | unknown | unknown | 待验证 | 材料理解:forbidden；蓝图规划:limited；正式生题:limited；答案生成:limited；正确性复核:unknown | C | unknown |
| 火山方舟 | `deepseek-v4-pro-260425` | text_generation | text → text | 未登记/禁用 | unknown | unknown | 待验证 | 材料理解:forbidden；蓝图规划:limited；正式生题:limited；答案生成:limited；正确性复核:unknown | C | unknown |
| 火山方舟 | `glm-5-2-260617` | text_generation | text → text | 未登记/禁用 | unknown | unknown | 待验证 | 材料理解:forbidden；蓝图规划:limited；正式生题:limited；答案生成:limited；正确性复核:unknown | C | unknown |
| 火山方舟 | `kimi-k2` | text_generation | text → text | 未登记/禁用 | unknown | unknown | 待验证 | 材料理解:forbidden；蓝图规划:limited；正式生题:limited；答案生成:limited；正确性复核:unknown | C | unknown |
| 火山方舟 | `doubao-seedream-5-0-260128` | image_generation | text → image | 未登记/禁用 | not_applicable | not_applicable | 待验证 | 图片生成:limited | C | unknown |
| 火山方舟图片 | `doubao-seedream-5-0-260128` | image_generation | text → image | 未登记/禁用 | not_applicable | not_applicable | 待验证 | 图片生成:limited | C | unknown |
| 火山方舟图片 | `doubao-seedream-5-0-lite-260128` | image_generation | text → image | 未登记/禁用 | not_applicable | not_applicable | 待验证 | 图片生成:limited | C | unknown |
| 阿里百炼 | `qwen3.7-max` | text_generation | text → text | 未登记/禁用 | prompt_and_repair | unknown | 待验证 | 材料理解:forbidden；蓝图规划:limited；正式生题:limited；答案生成:limited；正确性复核:unknown；格式修复:limited | C | unknown |
| 阿里百炼 | `qwen3.7-plus` | text_generation | text、image → text | 已验证（responses，2026-08-29） | unknown | unknown | 待验证 | 材料理解:limited；蓝图规划:limited；正式生题:limited；答案生成:limited；正确性复核:unknown | C | unknown |
| 阿里百炼 | `qwen3.7-flash` | text_generation | text、image → text | 已验证（responses，2026-08-29） | unknown | unknown | 待验证 | 材料理解:limited；蓝图规划:limited；正式生题:limited；答案生成:limited；正确性复核:unknown | C | unknown |
| 阿里百炼 | `qwen3.6-plus` | text_generation | text、image → text | 已验证（responses，2026-08-29） | unknown | unknown | 待验证 | 材料理解:limited；蓝图规划:limited；正式生题:limited；答案生成:limited；正确性复核:unknown | C | unknown |
| 阿里百炼 | `qwen3.6-flash` | text_generation | text、image → text | 已验证（responses，2026-08-29） | unknown | unknown | 待验证 | 材料理解:limited；蓝图规划:limited；正式生题:limited；答案生成:limited；正确性复核:unknown | C | unknown |
| 阿里百炼 | `qwen3-vl-flash` | text_generation | text、image → text | 已验证（chat_completions，2026-08-29） | unknown | unknown | 待验证 | 材料理解:limited；蓝图规划:limited；正式生题:limited；答案生成:limited；正确性复核:unknown | C | unknown |
| 阿里百炼 | `qwen-vl-max` | text_generation | text、image → text | 未登记/禁用 | unknown | unknown | 待验证 | 材料理解:limited；蓝图规划:limited；正式生题:limited；答案生成:limited；正确性复核:unknown | C | unknown |
| 阿里百炼 | `qwen-vl-plus` | text_generation | text、image → text | 未登记/禁用 | unknown | unknown | 待验证 | 材料理解:limited；蓝图规划:limited；正式生题:limited；答案生成:limited；正确性复核:unknown | C | unknown |
| 阿里百炼 | `qwen-vl-ocr` | text_generation | text、image → text | 已验证（chat_completions，2026-08-29） | unknown | unknown | 待验证 | 材料理解:limited；蓝图规划:forbidden；正式生题:forbidden；答案生成:forbidden；正确性复核:forbidden；格式修复:limited | C | unknown |
| 阿里百炼 | `qwen-image-2.0-pro` | image_generation | text → image | 未登记/禁用 | not_applicable | not_applicable | 待验证 | 图片生成:limited | C | unknown |
| 阿里百炼 | `qwen-image-2.0` | image_generation | text → image | 未登记/禁用 | not_applicable | not_applicable | 待验证 | 图片生成:limited | C | unknown |
| 阿里百炼 | `qwen-image-max` | image_generation | text → image | 未登记/禁用 | not_applicable | not_applicable | 待验证 | 图片生成:limited | C | unknown |
| 阿里百炼 | `qwen-image-plus` | image_generation | text → image | 未登记/禁用 | not_applicable | not_applicable | 待验证 | 图片生成:limited | C | unknown |
| 商汤日日新 | `sensenova-6.8-flash-lite` | text_generation | text、image → text | 未登记/禁用 | unknown | unknown | 待验证 | 材料理解:limited；蓝图规划:limited；正式生题:limited；答案生成:limited；正确性复核:unknown | C | unknown |
| 商汤日日新 | `deepseek-v4-flash` | text_generation | text → text | 未登记/禁用 | unknown | unknown | 待验证 | 材料理解:forbidden；蓝图规划:limited；正式生题:limited；答案生成:limited；正确性复核:unknown | C | unknown |
| 商汤日日新 | `glm-5.2` | text_generation | text → text | 未登记/禁用 | unknown | unknown | 待验证 | 材料理解:forbidden；蓝图规划:limited；正式生题:limited；答案生成:limited；正确性复核:unknown | C | unknown |
| 商汤日日新 | `sensenova-u1.5-lite` | image_generation | text → image | 未登记/禁用 | not_applicable | not_applicable | 待验证 | 图片生成:limited | C | unknown |
| B.AI | `deepseek-v4-flash` | text_generation | text → text | 未登记/禁用 | provider_declared | unknown | 待验证 | 材料理解:forbidden；蓝图规划:limited；正式生题:limited；答案生成:limited；正确性复核:unknown | C | unknown |
| B.AI | `deepseek-v4-flash-vision-exp` | text_generation | text、image → text | 未登记/禁用 | provider_declared | unknown | 待验证 | 材料理解:limited；蓝图规划:limited；正式生题:limited；答案生成:limited；正确性复核:unknown | C | unknown |
| B.AI | `hy3` | text_generation | text → text | 未登记/禁用 | provider_declared | unknown | 待验证 | 材料理解:forbidden；蓝图规划:limited；正式生题:limited；答案生成:limited；正确性复核:unknown | C | unknown |
| B.AI | `mimo-v2.5` | text_generation | text → text | 未登记/禁用 | provider_declared | unknown | 待验证 | 材料理解:forbidden；蓝图规划:limited；正式生题:limited；答案生成:limited；正确性复核:unknown | C | unknown |
| OpenRouter | `stealth/ox-alpha` | text_generation | text、image → text | 未登记/禁用 | unknown | unknown | 待验证 | 材料理解:limited；蓝图规划:limited；正式生题:limited；答案生成:limited；正确性复核:forbidden | C | unknown |
| OpenRouter | `z-ai/glm-5.2:free` | text_generation | text → text | 未登记/禁用 | unknown | unknown | 待验证 | 材料理解:forbidden；蓝图规划:limited；正式生题:limited；答案生成:limited；正确性复核:forbidden | C | unknown |
| OpenRouter | `minimax/minimax-m3:free` | text_generation | text、image → text | 未登记/禁用 | provider_declared | unknown | 待验证 | 材料理解:limited；蓝图规划:limited；正式生题:limited；答案生成:limited；正确性复核:forbidden | C | unknown |
| Google AI 官方 | `gemini-3.7-flash` | text_generation | text、image、audio、video、pdf → text | 未登记/禁用 | native_json | minimum_low | 待验证 | 材料理解:allowed；蓝图规划:allowed；正式生题:allowed；答案生成:allowed；正确性复核:limited；格式修复:allowed | B | 2026-08-27 |
| Google AI 官方 | `gemini-3.6-flash` | text_generation | text、image、audio、video、pdf → text | 未登记/禁用 | native_json | minimum_low | 待验证 | 材料理解:allowed；蓝图规划:allowed；正式生题:allowed；答案生成:allowed；正确性复核:limited；格式修复:allowed | B | 2026-08-27 |
| Google AI 官方 | `gemini-3.5-flash` | text_generation | text、image、audio、video、pdf → text | 未登记/禁用 | native_json | minimum_low | 待验证 | 材料理解:allowed；蓝图规划:allowed；正式生题:allowed；答案生成:allowed；正确性复核:limited；格式修复:allowed | B | 2026-08-27 |
| Google AI 官方 | `gemini-3.5-flash-lite` | text_generation | text、image、audio、video、pdf → text | 未登记/禁用 | native_json | minimum_low | 待验证 | 材料理解:limited；蓝图规划:limited；正式生题:limited；答案生成:limited；正确性复核:forbidden；格式修复:allowed | B | 2026-08-27 |
| 智谱 BigModel 官方 | `glm-5.3-flash` | text_generation | text、image → text | 未登记/禁用 | platform_verified_json | required | 材料理解:18000 tokens；考查内容规划:14000 tokens；教材证据确认:18000 tokens；蓝图规划:16000 tokens；正式生题:20000 tokens；答案生成:22000 tokens；正确性复核:18000 tokens；语义审查:20000 tokens；格式修复:12000 tokens；插图结构规划:14000 tokens；作图代码生成:14000 tokens | 材料理解:allowed；考查内容规划:allowed；教材证据确认:allowed；蓝图规划:allowed；蓝图规划:allowed；正式生题:allowed；答案生成:allowed；答案生成:allowed；正确性复核:limited；语义审查:limited；格式修复:allowed；插图结构规划:allowed；作图代码生成:allowed | B | 2026-08-27 |
| 元衡 API | `gpt-5.6-luna` | text_generation | text、image → text | 未登记/禁用 | provider_declared | unknown | 待验证 | 材料理解:limited；蓝图规划:limited；正式生题:limited；答案生成:limited；正确性复核:unknown | C | unknown |
| 元衡 API | `gpt-5.6-terra` | text_generation | text、image → text | 未登记/禁用 | provider_declared | unknown | 待验证 | 材料理解:limited；蓝图规划:limited；正式生题:limited；答案生成:limited；正确性复核:unknown | C | unknown |
| 灵算 OpenAI | `gpt-5.6-sol` | text_generation | text、image → text | 已验证（responses，2026-08-29） | unknown | unknown | 待验证 | 材料理解:limited；蓝图规划:limited；正式生题:limited；答案生成:limited；正确性复核:unknown | C | unknown |
| 灵算 OpenAI | `gpt-5.6-terra` | text_generation | text、image → text | 已验证（responses，2026-08-29） | unknown | unknown | 待验证 | 材料理解:limited；蓝图规划:limited；正式生题:limited；答案生成:limited；正确性复核:unknown | C | unknown |
| 灵算 OpenAI | `gpt-5.6-luna` | text_generation | text、image → text | 未登记/禁用 | unknown | unknown | 待验证 | 材料理解:limited；蓝图规划:limited；正式生题:limited；答案生成:limited；正确性复核:unknown | C | unknown |
| 灵算 OpenAI | `gpt-5.5` | text_generation | text、image → text | 已验证（responses，2026-08-29） | unknown | unknown | 待验证 | 材料理解:limited；蓝图规划:limited；正式生题:limited；答案生成:limited；正确性复核:unknown | C | unknown |
| 灵算图片 | `gpt-image-2` | image_generation | text → image | 未登记/禁用 | not_applicable | not_applicable | 待验证 | 图片生成:limited | C | unknown |
| 灵算 Google | `gemini-3.7-flash-low` | text_generation | text、image → text | 已验证（chat_completions，2026-08-29） | native_json | minimum_low | 待验证 | 材料理解:limited；蓝图规划:limited；正式生题:limited；答案生成:limited；正确性复核:unknown | C | 2026-08-27 |
| 灵算 Google | `gemini-3.7-flash-medium` | text_generation | text、image → text | 已验证（chat_completions，2026-08-29） | unknown | minimum_medium | 待验证 | 材料理解:limited；蓝图规划:limited；正式生题:limited；答案生成:limited；正确性复核:unknown | C | unknown |
| 灵算 Google | `gemini-3.7-flash-high` | text_generation | text、image → text | 已验证（chat_completions，2026-08-29） | unknown | minimum_high | 待验证 | 材料理解:limited；蓝图规划:limited；正式生题:limited；答案生成:limited；正确性复核:unknown | C | unknown |
| 灵算 Google | `gemini-3.6-flash` | text_generation | text、image → text | 已验证（chat_completions，2026-08-29） | unknown | minimum_minimal | 待验证 | 材料理解:limited；蓝图规划:limited；正式生题:limited；答案生成:limited；正确性复核:unknown | C | unknown |
| 灵算 Google | `gemini-3.5-flash` | text_generation | text、image → text | 已验证（chat_completions，2026-08-29） | unknown | minimum_minimal | 待验证 | 材料理解:limited；蓝图规划:limited；正式生题:limited；答案生成:limited；正确性复核:unknown | C | unknown |
| 灵算 xAI | `grok-4.5` | text_generation | text、image → text | 未登记/禁用 | unknown | minimum_low | 待验证 | 材料理解:limited；蓝图规划:limited；正式生题:limited；答案生成:limited；正确性复核:unknown | C | unknown |
| 灵算 Anthropic | `claude-opus-5` | text_generation | text、image → text | 未登记/禁用 | unknown | unknown | 待验证 | 材料理解:limited；蓝图规划:limited；正式生题:limited；答案生成:limited；正确性复核:unknown | C | unknown |

## 同步规则

新增、删除或更换服务商/模型时，必须同时修改能力注册表并重新生成本表；自动测试会拒绝任何缺失或遗留记录。
