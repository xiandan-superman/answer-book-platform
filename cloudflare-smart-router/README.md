# Cloudflare 智能路由

该 Worker 是模型选择、供应商共享并发、60 秒排队、失败换线和按用户用量统计的权威入口。平台只配置 Worker 地址和分配给用户的访问 Key。

部署前通过 Wrangler Secret 配置：

- `CLIENT_KEYS_JSON`：访问 Key 到用户标识的 JSON，例如 `{"用户Key":"user-001"}`。
- Cloudflare KV `ROUTER_CONFIG`：统一保存 `{ "routes": [...], "provider_concurrency": { "供应商池": 并发数 } }`，集中管理所有 Gemini、GPT、DeepSeek、XAI 和生图路线；路线总配置不放 Secret，避免超过单个 Secret 的 5 KB 限制。
- `ROUTES_JSON`：Gemini 路线数组。每项包含 `id`、`family`、`provider`、`provider_pool`、`model`、`base_url`、`api_key_secret`、`priority`、`model_concurrency`、`capabilities`、`enabled`；需要思考档位下限的模型可增加 `thinking_minimum`。`base_url` 可以是供应商地址，也可以是 AI Gateway 自定义供应商地址；非标准认证头可用 `auth_header`、`auth_scheme` 配置。
- `GPT_ROUTES_JSON`：GPT 路线数组，字段与 `ROUTES_JSON` 相同，并将 `api_protocol` 设为 `responses`。两份路线会在 Worker 内合并，新增 GPT 路线不会覆盖现有 Gemini 配置。
- `ROUTE_BUNDLE_<供应商>_JSON`：后续新增供应商的独立路线包，例如 `ROUTE_BUNDLE_NEWVENDOR_JSON`。Worker 会自动合并所有符合该命名规则的路线包；新增、修改或删除某个供应商时不会覆盖其他供应商，也不需要发布平台客户端。每个路线包可同时包含该供应商的多个模型，并通过相同 `provider_pool` 和 `provider_concurrency` 共享人工配置的供应商总并发。
- `ROUTE_BUNDLE_IMAGE_MODELS_JSON`：独立生图路线包。图片路线使用 `family=image`，普通生图登记 `image_generation`，明确支持参考图编辑的路线再登记 `image_edit`。`image_adapter` 支持标准 Images、火山 Seedream、百炼 DashScope 和嵌入图片的 Chat/Responses 转换。
- 每条路线的 `api_key_secret` 对应一个单独的 Wrangler Secret。不同供应商、不同模型家族可以分别使用不同 Key，例如 `LINGSUAN_GOOGLE_API_KEY`、`LINGSUAN_OPENAI_API_KEY`、`WAWAPI_GOOGLE_API_KEY`、`WAWAPI_OPENAI_API_KEY`；Key 不合并，但并发按 `provider_pool` 合并计算。
- `PROVIDER_CONCURRENCY_JSON`：推荐的统一供应商并发配置，例如 `{"lingsuan":8,"wawapi":8,"topapi":8,"deepseek":4,"xai":4}`。这里按 `provider_pool` 限制同一供应商的总并发，供应商下的 Gemini、GPT、DeepSeek、XAI 等不同模型家族即使使用不同 Key，也共享该总并发。
- `LINGSUAN_TOTAL_CONCURRENCY`、`WAW_TOTAL_CONCURRENCY`、`TOPAPI_TOTAL_CONCURRENCY`：兼容旧配置；统一配置存在时优先使用统一配置。

首批生图上游 Key 使用独立 Secret：`ARK_API_KEY`、`DASHSCOPE_API_KEY`、`WAWAPI_IMAGE_OPENAI_API_KEY`、`WAWAPI_IMAGE_GOOGLE_API_KEY`、`WAWAPI_IMAGE_XAI_API_KEY`、`LINGSUAN_IMAGE_API_KEY`。这些值不得写入路线 JSON 或源码。

同一供应商的 Gemini 与 GPT 路线使用相同 `provider_pool`，因此共享同一个供应商并发上限。`provider` 保留实际通道名称用于前端显示和调用记录。`priority` 数字越小优先级越高；相同优先级的新模型先获得少量试用，样本达到 5 次后按有效响应成功率、平均延迟排序。一次请求遇到网络、超时、限流、认证、空响应或上游 5xx 时，按排序继续尝试尚未尝试的模型；请求内容错误不会换线。所有候选均失败时返回每条路线的失败摘要。

运行 `npm install` 后执行 `npm run check`。Worker 代码变更才需要执行 `npm run deploy`；仅新增符合命名规则的供应商路线包或对应 API Key Secret 时，平台用户无需更新版本。

用户用量可通过 `GET /v1/usage` 查询；候选模型的样本、成功率、平均延迟、冷却状态和当前并发可通过 `GET /v1/router-status?family=gemini|gpt|image` 查询。这两个接口都要求用户访问 Key。
