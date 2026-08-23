# v4 Formula Chain

v4 公式链路的目标是彻底阻断“公式以普通文本进入 Word”的问题。

## 转换路径

正式执行默认使用专业链路：

```text
LaTeX -> latex2mathml -> MathML -> packaged MathML-to-OMML (Microsoft XSLT when available) -> Word OMML
```

要求：

- `latex2mathml` 已安装。
- `lxml` 已安装。
- 安装包已包含 `math_ml2omml`；若本机 Microsoft Word 可提供 `mathml2omml.xsl`，程序优先使用该链路并在失败时切换到跨平台后端。
- `check_environment.py` 输出 `formula_conversion.preferred_chain_ready = true`。

如果专业链路不完整，正式流水线必须在环境阶段失败。内置最小 OMML 转换器只允许用 `--allow-formula-fallback` 进行环境排查，不作为正式生产默认路径。

## 强制规则

1. 普通正文 segment 中不得出现公式样式文本。
2. 所有公式必须进入 `formulas` 或 `formula_ref`。
3. `relation_steps`、`substitution_steps`、`result_steps` 不允许是普通字符串数组，必须是结构化 segment 数组。
4. DOCX 生成器只从公式对象生成 Word OMML。
5. 审计器必须扫描所有 `<w:t>` 普通文本，不能因为同段存在 `<m:oMath>` 就跳过。
6. 禁止插入空 OMML 作为审计绕过标记。

## 正确结构示例

```json
{
  "question_id": "calc_04_02",
  "blocks": [
    {
      "label": "关系式",
      "segments": [
        {"type": "formula_ref", "formula_id": "f_calc_04_02_01"},
        {"type": "formula_ref", "formula_id": "f_calc_04_02_02"}
      ]
    }
  ],
  "formulas": [
    {
      "formula_id": "f_calc_04_02_01",
      "latex": "\\Delta_r G_m=-nFE",
      "display": true,
      "role": "relation"
    },
    {
      "formula_id": "f_calc_04_02_02",
      "latex": "\\Delta_r S_m=nF(\\partial E/\\partial T)_p",
      "display": true,
      "role": "relation"
    }
  ]
}
```

## 错误结构示例

```json
{
  "analysis": "由 ΔrGm=-nFE 可得..."
}
```

这种结构必须失败并重试。
