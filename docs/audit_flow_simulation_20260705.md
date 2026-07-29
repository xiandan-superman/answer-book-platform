# 审查问题模拟流程测试报告

- 生成时间：2026-07-05 11:21:05
- 工作目录：`/Users/ljj/Documents/真题解析/answer_book_platform_v1`
- 模拟输出目录：`/Users/ljj/Documents/真题解析/answer_book_platform_v1/tmp/audit_flow_simulation_20260705`
- 内容质量审查模拟项：27
- DOCX 审核原子问题模拟项：10
- DOCX 后续流程模拟项：3

## 结论

1. 内容质量审查的 issue 不会直接阻止最终文件继续生成；模型回修和程序自修后仍存在的问题会被 `auto_allow_audit_report` 转为 warning，并写入审查记录。
2. 内容质量审查放行后，最终 Word 通常仍按当前 `answer_fragments.json` 生成；这不是占位低质量版，但对应题目可能存在缺图、缺解析、缺公式或引用异常等质量风险。
3. DOCX 审核失败后会先尝试模型回修或程序自修；如果仍失败并被允许继续，只要 `answer_book.docx` 已存在，就沿用当前可生成文件并保留审查 warning。
4. 如果 DOCX 被允许继续但 `answer_book.docx` 不存在，系统先重建正式候选版；候选版仍失败时才生成“待复核版”占位 Word，这是低质量兜底版本。

## 内容质量审查逐项模拟

| 目标问题 | 触发 | 初始 issue/warning | 模型回修目标 | 程序自修后通过 | 自动放行 | 最终 gate | DOCX 可生成 | 观察到的代码 |
|---|---:|---:|---:|---:|---:|---:|---:|---|
| missing_fragment | 是 | 1 | 否 | 否 | 是 | 通过 | 未尝试 | `missing_fragment` |
| missing_draft | 是 | 1 | 否 | 否 | 是 | 通过 | 是 | `missing_draft` |
| missing_answer | 是 | 1 | 否 | 否 | 是 | 通过 | 是 | `missing_answer` |
| missing_analysis | 是 | 1 | 是 | 否 | 是 | 通过 | 是 | `missing_analysis` |
| short_analysis | 是 | 1 | 否 | 是 | 否 | 通过 | 是 | `short_analysis` |
| forbidden_process_text | 是 | 1 | 否 | 否 | 是 | 通过 | 是 | `forbidden_process_text` |
| generic_analysis_phrase | 是 | 1 | 否 | 是 | 否 | 通过 | 是 | `generic_analysis_phrase` |
| unresolved_formula_placeholder | 是 | 1 | 否 | 否 | 是 | 通过 | 是 | `unresolved_formula_placeholder` |
| citation_leaked_into_answer | 是 | 1 | 否 | 否 | 是 | 通过 | 是 | `citation_leaked_into_answer` |
| missing_confirmed_evidence | 是 | 1 | 否 | 否 | 是 | 通过 | 是 | `missing_confirmed_evidence` |
| uses_rejected_evidence | 是 | 1 | 否 | 否 | 是 | 通过 | 是 | `uses_rejected_evidence` |
| choice_missing_option_analysis | 是 | 1 | 否 | 否 | 是 | 通过 | 是 | `choice_missing_option_analysis` |
| missing_answer_summary | 是 | 1 | 否 | 否 | 是 | 通过 | 是 | `missing_answer_summary` |
| calculation_missing_formula | 是 | 1 | 否 | 否 | 是 | 通过 | 否 | `calculation_missing_formula` |
| formula_absence_after_retry | 是 | 1 | 是 | 是 | 否 | 通过 | 否 | `formula_absence_after_retry` |
| calculation_missing_steps | 是 | 2 | 是 | 否 | 是 | 通过 | 是 | `calculation_missing_steps, calculation_steps_missing_formula_refs` |
| calculation_steps_missing_formula_refs | 是 | 1 | 否 | 否 | 是 | 通过 | 是 | `calculation_steps_missing_formula_refs` |
| calculation_formula_dumped_in_analysis | 是 | 2 | 否 | 否 | 是 | 通过 | 是 | `calculation_formula_dumped_in_analysis, calculation_steps_missing_formula_refs, short_analysis` |
| calculation_formula_dumped_in_steps | 是 | 2 | 否 | 否 | 是 | 通过 | 是 | `calculation_formula_dumped_in_steps, calculation_steps_missing_formula_refs` |
| calculation_missing_substitution | 是 | 1 | 是 | 否 | 是 | 通过 | 是 | `calculation_missing_substitution` |
| calculation_steps_not_sequential | 是 | 1 | 否 | 否 | 是 | 通过 | 是 | `calculation_steps_not_sequential` |
| calculation_missing_mistake_notes | 是 | 1 | 是 | 否 | 是 | 通过 | 是 | `calculation_missing_mistake_notes` |
| calculation_answer_missing_unit | 是 | 1 | 是 | 是 | 否 | 通过 | 是 | `calculation_answer_missing_unit` |
| missing_answer_summary | 是 | 1 | 否 | 是 | 否 | 通过 | 是 | `missing_answer_summary` |
| noncalculation_unintegrated_formulas | 是 | 2 | 否 | 是 | 否 | 通过 | 是 | `noncalculation_unintegrated_formulas, short_analysis` |
| missing_required_figure | 是 | 1 | 是 | 否 | 是 | 通过 | 是 | `missing_required_figure` |
| duplicated_mistake_note | 是 | 2 | 否 | 是 | 否 | 通过 | 是 | `duplicated_mistake_note` |

### 内容质量审查覆盖结果

- 未触发目标代码：无
- 模型回修白名单：`calculation_answer_missing_unit, calculation_missing_mistake_notes, calculation_missing_steps, calculation_missing_substitution, formula_absence_after_retry, missing_analysis, missing_required_figure`
- 非白名单 issue 会跳过模型回修，直接进入程序自修；程序自修主要处理公式占位/文本公式对象化，不会补全真实答案质量。
- 自动放行不是修复内容，而是把阻断性 issue 记录为 warning，供最终审查报告和存疑题目文档提示。

## DOCX 审核原子问题模拟

| 目标问题 | 触发 | issue 数 | 分类代码 | 可模型回修 issue |
|---|---:|---:|---|---|
| omml_formula_count_below_expected | 是 | 1 | `omml_formula_count_below_expected` | `` |
| math_object_empty | 是 | 2 | `docx_audit_issue` | `` |
| math_object_raw_latex | 是 | 2 | `raw_latex_marker` | `` |
| math_run_not_italic | 是 | 1 | `docx_audit_issue` | `` |
| paragraph_formula_placeholder | 是 | 1 | `docx_audit_issue` | `` |
| paragraph_raw_latex_marker | 是 | 1 | `raw_latex_marker` | `` |
| paragraph_raw_latex_word | 是 | 1 | `raw_latex_marker` | `` |
| paragraph_raw_radical | 是 | 1 | `raw_radical_normal_text` | `` |
| paragraph_raw_subscript | 是 | 1 | `raw_subscript_normal_text` | `` |
| valid_italic_math_control | 是 | 0 | `` | `` |

### DOCX 审核覆盖结果

- 未触发目标问题：无
- 当前 DOCX 模型回修白名单只有 `formula_like_normal_text`；raw LaTeX、下标、根号、OMML 数量不足等会走程序自修或自动放行。

## DOCX 后续流程模拟

| 场景 | 结果 | 最终 Word | 是否占位低质量版 | 关键后续流程 |
|---|---|---:|---:|---|
| docx_local_repair | 通过 | 是 | 否 | initial 审核失败 -> docx_model_repair skipped -> docx_repair applied -> after_repair 重新审核 |
| docx_placeholder_after_candidate_failure | 占位兜底 | 是 | 是 | initial 构建失败 -> docx_repair skipped -> auto_allow -> candidate failed -> placeholder applied |
| docx_user_allowed_candidate_success | 候选版生成 | 是 | 否 | auto_allow 后发现 docx 缺失 -> user_allowed_candidate applied |

## 对最终文件的影响判断

- 内容质量 issue：默认最终仍可生成正式 `answer_book.docx`，但质量风险会转为 warning；不会自动生成低质量占位版。
- DOCX 审核 issue 且 `answer_book.docx` 已存在：最终文件继续存在，风险写入 `docx_audit.json` 和最终审查报告；不会生成占位版。
- DOCX 构建/审核后 `answer_book.docx` 不存在：先尝试 `docx_user_allowed_candidate` 重建正式候选版；只有候选版失败，才生成 `docx_placeholder` 待复核版。
- 待复核占位版的内容只保留题号、答案、warning 和 review flag，不包含完整解析排版，因此属于低质量兜底交付物。

## 机器可复查产物

- 内容质量逐项 JSON：`/Users/ljj/Documents/真题解析/answer_book_platform_v1/tmp/audit_flow_simulation_20260705/content_results.json`
- DOCX 审核逐项 JSON：`/Users/ljj/Documents/真题解析/answer_book_platform_v1/tmp/audit_flow_simulation_20260705/docx_audit_results.json`
- DOCX 流程 JSON：`/Users/ljj/Documents/真题解析/answer_book_platform_v1/tmp/audit_flow_simulation_20260705/docx_flow_results.json`
