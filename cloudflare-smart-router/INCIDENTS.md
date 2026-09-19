# 智能路由故障记录

## 2026-09-19：GPT 智能路由认证失败

### 现象

一项 GPT 智能路由任务在“考查内容判断 / 知识点规划”阶段失败，全部题目均未进入模型路由。任务日志记录：

```text
SyntaxError: "[object Object]" is not valid JSON
at authenticate (index.js:64:27)
```

### 根因

智能路由入口把 `CLIENT_KEYS_JSON` 统一转换成字符串后再执行 `JSON.parse`。当部署环境直接提供对象配置时，转换结果是 `[object Object]`，认证阶段立即抛出异常，请求无法进入路由协调器。

### 修复

统一使用 `parseJsonValue` / `parseJsonObject`：

- 已解析的对象直接使用；
- JSON 字符串才执行解析；
- 空值使用安全默认值；
- 数组或其他错误类型返回清晰的配置错误。

同一解析逻辑同时覆盖 `CLIENT_KEYS_JSON`、`ROUTER_CONFIG_JSON`、路线数组和路线包，避免其他智能路由或后续新增路线复现同类问题。

### 发布前检查

1. `npm run check` 必须通过。
2. 使用对象形式和 JSON 字符串形式分别验证认证配置。
3. GPT、Gemini、Claude、图片智能路由至少各完成一次真实请求或健康检查。
4. 任务日志必须记录实际供应商、模型和路由切换原因。

### 部署注意事项

本次修复首次发布时，Wrangler 检测到远端 `CLIENT_KEYS_JSON` 不在本地配置中。普通部署会删除该远端变量，导致所有用户收到“用户访问 Key 无效”。已恢复原变量并重新发布。

后续生产发布必须使用 `npx wrangler deploy --keep-vars`，发布后检查版本绑定中仍存在 `CLIENT_KEYS_JSON`，再进行四类智能路由连接测试。
