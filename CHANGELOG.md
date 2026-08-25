# 版本更新记录

本文件记录普通用户可见的正式版本变化。每次发布必须先增加与 `APP_VERSION` 一致的条目；GitHub Release 和应用内更新说明均从对应条目生成。

## [0.9.22] - 2026-08-25

### 新增

- 新增 OpenRouter 服务商，可在“API 配置”页面独立测试和保存 `OPENROUTER_API_KEY`。
- 首批接入 `stealth/ox-alpha`，支持文本与图片理解，并使用已验证的流式 Responses API。
- 新增 `z-ai/glm-5.2:free` 文本模型和 `minimax/minimax-m3:free` 多模态模型。
- 用户可见任务名称会将三个模型分别简写为“Ox Alpha”、“GLM”和“MiniMax”。

### 验证与注意事项

- Ox Alpha 的普通文本、JSON 结构化输出和程序实际流式 Responses 调用均已通过真实请求验证。
- OpenRouter 官方模型信息声明 Ox Alpha 支持图片输入；测试期间 Stealth 上游共享池曾对图片请求返回临时 429，遇到时应稍后重试。
- MiniMax M3 Free 的文本、JSON 结构化输出和图片理解均已通过真实请求验证；GLM 5.2 Free 模型可见，但测试时上游免费共享池持续限流，暂未完成生成成功验证。
- API Key 仍只保存在系统用户数据目录，程序更新不会覆盖，也不会写入任务产物或发布包。

## [0.9.20] - 2026-08-25

### 新增

- 新增商汤日日新服务商，接入 `sensenova-6.8-flash-lite`、`deepseek-v4-flash`、`glm-5.2` 和图片模型 `sensenova-u1.5-lite`；按要求不提供 `sensenova-u1-fast`。
- 新增 B.AI 服务商，仅接入真实请求已通过的 `deepseek-v4-flash`、`deepseek-v4-flash-vision-exp`、`hy3` 和 `mimo-v2.5`。
- 商汤 6.8 Flash Lite 与 B.AI Vision Exp 可作为图片理解模型；商汤 U1.5 Lite 可生成并立即保存 2K PNG 题图。

### 更新体验

- 更新前拦截运行中与排队任务，避免服务重启中断生题。
- 服务退出后由独立安全更新窗口展示解压、备份、覆盖、依赖检查、启动和失败回滚状态。
- 修复更新失败后反复重试、网页重复打开以及原程序目录可能短暂为空的问题。
- 保持 API Key、教材、任务、日志和输出位于独立用户数据目录，升级与失败回滚均不覆盖用户数据。

## [0.9.19] - 2026-08-24

### 重要升级说明

- 启用 `update-stable-v2.json` 安全更新源，源码替换前先在安装目录旁准备完整新版本并备份旧源码。
- 0.9.18 及更早的源码 ZIP 仍使用旧替换器，需要手动下载安装 0.9.19 或更高版本一次；旧更新源保持停用。
- 新启动器在首次运行时自动创建用户级 Python 环境并安装依赖，缺少 Python 时仅提示用户安装。
