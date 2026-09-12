# 当前模型能力登记表（自动生成）

> 数据源：`config/model_capabilities.json`。请勿手工编辑本表；运行 `python3 scripts/sync_model_capability_docs.py` 重新生成。

能力等级：A＝真实任务流程已验证；B＝接口能力已验证、任务基线待补；C＝配置或通道声明；D＝未知/过期。

| 服务商 | 模型 | 类型 | 原生输入 → 输出 | 主模型工具闭环 | 结构化输出 | 推理 | 任务质量输入预算 | 任务适配 | 证据 | 最后验证 |
|---|---|---|---|---|---|---|---|---|---|---|
| 火山方舟 | `doubao-seed-2-0-pro-260215` | text_generation | text、image → text | 已开启（无需探测） | platform_verified_json | unknown | 待验证 | 材料理解:limited；蓝图规划:limited；正式生题:limited；答案生成:limited；正确性复核:unknown | C | 2026-09-10 |
| 火山方舟 | `deepseek-v4-pro-ga-260813` | text_generation | text → text | 已开启（无需探测） | platform_verified_json | unknown | 待验证 | 材料理解:forbidden；蓝图规划:limited；正式生题:limited；答案生成:limited；正确性复核:unknown | C | 2026-09-10 |
| 火山方舟 | `deepseek-v4-flash-ga-260731` | text_generation | text → text | 已开启（无需探测） | platform_verified_json | unknown | 待验证 | 材料理解:forbidden；蓝图规划:limited；正式生题:limited；答案生成:limited；正确性复核:unknown | C | 2026-09-10 |
| 火山方舟 | `doubao-seedream-5-0-260128` | image_generation | text → image | 不适用 | not_applicable | not_applicable | 待验证 | 图片生成:limited | C | 2026-09-10 |
| 火山方舟图片 | `doubao-seedream-5-0-260128` | image_generation | text → image | 不适用 | not_applicable | not_applicable | 待验证 | 图片生成:limited | C | 2026-09-10 |
| 火山方舟图片 | `doubao-seedream-5-0-lite-260128` | image_generation | text → image | 不适用 | not_applicable | not_applicable | 待验证 | 图片生成:limited | C | 2026-09-10 |
| DeepSeek 官方 | `deepseek-flash` | text_generation | text、image → text | 已开启（无需探测） | platform_verified_json | supported | 待验证 | 材料理解:limited；蓝图规划:allowed；正式生题:allowed；答案生成:allowed；正确性复核:limited；格式修复:allowed | B | 2026-09-11 |
| 阿里百炼 | `qwen3.7-max` | text_generation | text → text | 已开启（无需探测） | prompt_and_repair | unknown | 待验证 | 材料理解:forbidden；蓝图规划:limited；正式生题:limited；答案生成:limited；正确性复核:unknown；格式修复:limited | C | unknown |
| 阿里百炼 | `qwen3.7-plus` | text_generation | text、image → text | 已开启（无需探测）；历史实测 responses@2026-08-29 | unknown | unknown | 待验证 | 材料理解:limited；蓝图规划:limited；正式生题:limited；答案生成:limited；正确性复核:unknown | C | unknown |
| 阿里百炼 | `qwen3.7-flash` | text_generation | text、image → text | 已开启（无需探测）；历史实测 responses@2026-08-29 | unknown | unknown | 待验证 | 材料理解:limited；蓝图规划:limited；正式生题:limited；答案生成:limited；正确性复核:unknown | C | unknown |
| 阿里百炼 | `qwen3.6-plus` | text_generation | text、image → text | 已开启（无需探测）；历史实测 responses@2026-08-29 | unknown | unknown | 待验证 | 材料理解:limited；蓝图规划:limited；正式生题:limited；答案生成:limited；正确性复核:unknown | C | unknown |
| 阿里百炼 | `qwen3.6-flash` | text_generation | text、image → text | 已开启（无需探测）；历史实测 responses@2026-08-29 | unknown | unknown | 待验证 | 材料理解:limited；蓝图规划:limited；正式生题:limited；答案生成:limited；正确性复核:unknown | C | unknown |
| 阿里百炼 | `qwen3-vl-flash` | text_generation | text、image → text | 已开启（无需探测）；历史实测 chat_completions@2026-08-29 | unknown | unknown | 待验证 | 材料理解:limited；蓝图规划:limited；正式生题:limited；答案生成:limited；正确性复核:unknown | C | unknown |
| 阿里百炼 | `qwen-vl-max` | text_generation | text、image → text | 已开启（无需探测） | unknown | unknown | 待验证 | 材料理解:limited；蓝图规划:limited；正式生题:limited；答案生成:limited；正确性复核:unknown | C | unknown |
| 阿里百炼 | `qwen-vl-plus` | text_generation | text、image → text | 已开启（无需探测） | unknown | unknown | 待验证 | 材料理解:limited；蓝图规划:limited；正式生题:limited；答案生成:limited；正确性复核:unknown | C | unknown |
| 阿里百炼 | `qwen-vl-ocr` | text_generation | text、image → text | 已开启（无需探测）；历史实测 chat_completions@2026-08-29 | unknown | unknown | 待验证 | 材料理解:limited；蓝图规划:forbidden；正式生题:forbidden；答案生成:forbidden；正确性复核:forbidden；格式修复:limited | C | unknown |
| 阿里百炼 | `qwen-image-2.0-pro` | image_generation | text → image | 不适用 | not_applicable | not_applicable | 待验证 | 图片生成:limited | C | unknown |
| 阿里百炼 | `qwen-image-2.0` | image_generation | text → image | 不适用 | not_applicable | not_applicable | 待验证 | 图片生成:limited | C | unknown |
| 阿里百炼 | `qwen-image-max` | image_generation | text → image | 不适用 | not_applicable | not_applicable | 待验证 | 图片生成:limited | C | unknown |
| 阿里百炼 | `qwen-image-plus` | image_generation | text → image | 不适用 | not_applicable | not_applicable | 待验证 | 图片生成:limited | C | unknown |
| WawAPI · GPT | `gpt-5.6-sol` | text_generation | text、image → text | 已开启（无需探测） | unknown | unknown | 待验证 | 材料理解:limited；蓝图规划:limited；正式生题:limited；答案生成:limited；正确性复核:unknown | C | unknown |
| WawAPI · GPT | `gpt-5.6-terra` | text_generation | text、image → text | 已开启（无需探测） | unknown | unknown | 待验证 | 材料理解:limited；蓝图规划:limited；正式生题:limited；答案生成:limited；正确性复核:unknown | C | unknown |
| WawAPI · GPT | `gpt-6-astra` | text_generation | text、image → text | 已开启（无需探测） | unknown | unknown | 待验证 | 材料理解:limited；蓝图规划:limited；正式生题:limited；答案生成:limited；正确性复核:unknown | C | unknown |
| WawAPI · Gemini | `gemini-3.8-flash` | text_generation | text、image → text | 已开启（无需探测） | unknown | unknown | 待验证 | 材料理解:limited；蓝图规划:limited；正式生题:limited；答案生成:limited；正确性复核:unknown | C | unknown |
| WawAPI · Gemini | `gemini-3.7-flash` | text_generation | text、image → text | 已开启（无需探测） | unknown | unknown | 待验证 | 材料理解:limited；蓝图规划:limited；正式生题:limited；答案生成:limited；正确性复核:unknown | C | unknown |
| WawAPI · Gemini | `gemini-3.6-flash` | text_generation | text、image → text | 已开启（无需探测） | unknown | unknown | 待验证 | 材料理解:limited；蓝图规划:limited；正式生题:limited；答案生成:limited；正确性复核:unknown | C | unknown |
| WawAPI · Grok | `grok-4.6` | text_generation | text、image → text | 已开启（无需探测） | unknown | unknown | 待验证 | 材料理解:limited；蓝图规划:limited；正式生题:limited；答案生成:limited；正确性复核:unknown | C | unknown |
| WawAPI · Grok | `grok-4.5` | text_generation | text、image → text | 已开启（无需探测） | unknown | unknown | 待验证 | 材料理解:limited；蓝图规划:limited；正式生题:limited；答案生成:limited；正确性复核:unknown | C | unknown |
| WawAPI · GPT 图片 | `gpt-image-2` | image_generation | text → image | 不适用 | not_applicable | not_applicable | 待验证 | 图片生成:limited | C | unknown |
| WawAPI · Gemini 图片 | `gemini-3-pro-image-preview` | image_generation | text → image | 不适用 | not_applicable | not_applicable | 待验证 | 图片生成:limited | C | unknown |
| WawAPI · Gemini 图片 | `gemini-3.1-flash-image-preview` | image_generation | text → image | 不适用 | not_applicable | not_applicable | 待验证 | 图片生成:limited | C | unknown |
| WawAPI · Grok 图片 | `grok-imagine-image-2.0` | image_generation | text → image | 不适用 | not_applicable | not_applicable | 待验证 | 图片生成:limited | C | unknown |
| 灵算 OpenAI | `gpt-6-astra` | text_generation | text、image → text | 已开启（无需探测）；历史实测 responses@2026-09-05 | platform_verified_json | unknown | 待验证 | 材料理解:limited；蓝图规划:limited；正式生题:limited；答案生成:limited；正确性复核:unknown | C | 2026-09-05 |
| 灵算 OpenAI | `gpt-5.6-sol` | text_generation | text、image → text | 已开启（无需探测）；历史实测 responses@2026-08-29 | unknown | unknown | 待验证 | 材料理解:limited；蓝图规划:limited；正式生题:limited；答案生成:limited；正确性复核:unknown | C | unknown |
| 灵算 OpenAI | `gpt-5.6-terra` | text_generation | text、image → text | 已开启（无需探测）；历史实测 responses@2026-08-29 | unknown | unknown | 待验证 | 材料理解:limited；蓝图规划:limited；正式生题:limited；答案生成:limited；正确性复核:unknown | C | unknown |
| 灵算 OpenAI | `gpt-5.6-luna` | text_generation | text、image → text | 已开启（无需探测） | unknown | unknown | 待验证 | 材料理解:limited；蓝图规划:limited；正式生题:limited；答案生成:limited；正确性复核:unknown | C | unknown |
| 灵算 OpenAI | `gpt-5.5` | text_generation | text、image → text | 已开启（无需探测）；历史实测 responses@2026-08-29 | unknown | unknown | 待验证 | 材料理解:limited；蓝图规划:limited；正式生题:limited；答案生成:limited；正确性复核:unknown | C | unknown |
| 灵算图片 | `gpt-image-2` | image_generation | text → image | 不适用 | not_applicable | not_applicable | 待验证 | 图片生成:limited | C | unknown |
| 灵算 · 国模分组 | `deepseek-v4-flash` | text_generation | text → text | 已开启（无需探测） | platform_verified_json | auto | 待验证 | 材料理解:limited；蓝图规划:limited；正式生题:limited；答案生成:limited；正确性复核:unknown | C | 2026-09-12 |
| 灵算 · 国模分组 | `deepseek-v4-flash-0731` | text_generation | text → text | 已开启（无需探测） | platform_verified_json | auto | 待验证 | 材料理解:limited；蓝图规划:limited；正式生题:limited；答案生成:limited；正确性复核:unknown | C | 2026-09-12 |
| 灵算 · 国模分组 | `deepseek-v4-pro` | text_generation | text → text | 已开启（无需探测） | platform_verified_json | auto | 待验证 | 材料理解:limited；蓝图规划:limited；正式生题:limited；答案生成:limited；正确性复核:unknown | C | 2026-09-12 |
| 灵算 · 国模分组 | `deepseek-v4-pro-0813` | text_generation | text → text | 已开启（无需探测） | platform_verified_json | auto | 待验证 | 材料理解:limited；蓝图规划:limited；正式生题:limited；答案生成:limited；正确性复核:unknown | C | 2026-09-12 |
| 灵算 · 国模分组 | `glm-5.2` | text_generation | text → text | 已开启（无需探测） | platform_verified_json | auto | 待验证 | 材料理解:limited；蓝图规划:limited；正式生题:limited；答案生成:limited；正确性复核:unknown | C | 2026-09-12 |
| 灵算 · 国模分组 | `glm-5.3` | text_generation | text → text | 已开启（无需探测） | platform_verified_json | auto | 待验证 | 材料理解:limited；蓝图规划:limited；正式生题:limited；答案生成:limited；正确性复核:unknown | C | 2026-09-12 |
| 灵算 · 国模分组 | `glm-5.3-flash` | text_generation | text → text | 已开启（无需探测） | platform_verified_json | auto | 待验证 | 材料理解:limited；蓝图规划:limited；正式生题:limited；答案生成:limited；正确性复核:unknown | C | 2026-09-12 |
| 灵算 · 国模分组 | `kimi-k3` | text_generation | text → text | 已开启（无需探测） | platform_verified_json | auto | 待验证 | 材料理解:limited；蓝图规划:limited；正式生题:limited；答案生成:limited；正确性复核:unknown | C | 2026-09-12 |
| 灵算 · 国模分组 | `qwen3.7-max` | text_generation | text → text | 已开启（无需探测） | platform_verified_json | auto | 待验证 | 材料理解:limited；蓝图规划:limited；正式生题:limited；答案生成:limited；正确性复核:unknown | C | 2026-09-12 |
| 灵算 · 国模分组 | `qwen3.8-max` | text_generation | text → text | 已开启（无需探测） | platform_verified_json | auto | 待验证 | 材料理解:limited；蓝图规划:limited；正式生题:limited；答案生成:limited；正确性复核:unknown | C | 2026-09-12 |
| 灵算 Google | `gemini-3.8-flash-medium` | text_generation | text、image → text | 已开启（无需探测）；历史实测 chat_completions@2026-09-11 | platform_verified_json | minimum_medium | 待验证 | 材料理解:limited；蓝图规划:limited；正式生题:limited；答案生成:limited；正确性复核:unknown | C | 2026-09-11 |
| 灵算 Google | `gemini-3.7-flash-medium` | text_generation | text、image → text | 已开启（无需探测）；历史实测 chat_completions@2026-08-30 | unknown | minimum_medium | 待验证 | 材料理解:limited；蓝图规划:limited；正式生题:limited；答案生成:limited；正确性复核:unknown | C | 2026-08-30 |
| 灵算 Google | `gemini-3.6-flash` | text_generation | text、image → text | 已开启（无需探测）；历史实测 chat_completions@2026-08-29 | unknown | minimum_minimal | 待验证 | 材料理解:limited；蓝图规划:limited；正式生题:limited；答案生成:limited；正确性复核:unknown | C | unknown |
| 灵算 Google | `gemini-3.5-flash` | text_generation | text、image → text | 已开启（无需探测）；历史实测 chat_completions@2026-08-29 | unknown | minimum_minimal | 待验证 | 材料理解:limited；蓝图规划:limited；正式生题:limited；答案生成:limited；正确性复核:unknown | C | unknown |

## 同步规则

新增、删除或更换服务商/模型时，必须同时修改能力注册表并重新生成本表；自动测试会拒绝任何缺失或遗留记录。
