# Platform Architecture

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

第一版支持：

- `openai`
- `deepseek`

两者统一走 OpenAI-compatible `/chat/completions` 接口。

后续新增 GLM、通义、其他兼容接口时，只新增 provider 配置或 adapter，不改业务流程。
