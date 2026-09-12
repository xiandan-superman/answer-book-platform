# 程序校验与内容判断的边界调整

用户已授权移除程序凭关键词、相似度、学科规则作出的内容判断；保留原有模型审查，不增加模型审查调用。

## 保留的客观检查

- JSON/字段类型、必需块、题号与小问覆盖、明确确认的题型及题数。
- 教材与公式 ID 存在、引用范围、已确认与已拒绝依据绑定。
- 公式对象、原生 Word 转换、明确 LaTeX 源码残留、图片存在及可渲染、交付文件完整。
- 模型主动声明的数值表：索引、ID、有限数值、同单位/同基准的明确分区加和与父子守恒；符号、不同单位或不同基准不凭文字推断。
- 完全相同的题干、选项、公式、表格和图片数据重复；不以相似度、换数字或设计描述相同判断重复。
- 原有模型审查结论、模型提出修复的引用原文核对、主动提供的数值修复表校验。

## 停用的程序内容判断与自动改写

- “剩余、析出、反应”等词触发必填数值表或转变关系。
- 根据答案措辞/符号同名/数值相同跨句匹配结果，自动重绑公式或改写答案。
- 正负方向、强弱比较、组成遗漏、空间关系归属、XRD 专业含义判断。
- 解析长短、空泛、过程性话术、易错点、单位措辞、文字像公式等推测。
- 必考点文字覆盖、范围/边界冲突、难度漂移、解法/题面相似度、图文意义与标签判断。
- 根据图形名称补画或修改物理曲线/节点；根据关键词删除原文或添加易错点。
- 根据审查理由中的措辞改写模型决策，或推断该模型必须补充额外数值表。

退役规则明确列举，未知规则不被一概忽略。历史内容报告只在内存重新解释；原任务文件、原模型输出和历史失败状态不改写。历史检查点重新使用当前结构校验，恢复仍需原有来源指纹和完整性检查。

## 跨场景影响矩阵

| 场景 | 生效路径 | 回归证据 |
|---|---|---|
| 真题解析 | 生成校验、数值表、内容审计、修复 | answer_units、calculation_consistency、content_quality_diagnostics、machine_gate_boundary |
| 按题出题 | 蓝图、题面、重复、图件、导出 | exercise_generation、practice_generation_batching、practice_export_gate |
| 知识点出题 | 同一生成入口；不对知识点文字做范围推断 | knowledge_targeted_blueprint_contract、practice_knowledge_coverage、practice_targeted_repairs |
| 历史恢复 | 当前校验与退役规则解释，原文件不变 | pipeline_checkpoint_recovery、content_quality_repair、machine_gate_boundary |
| Word/PDF 交付 | 保留公式转换、XML、资产和正式交付门；停用正文措辞审查 | docx_contracts、crystallographic_expression_contract、practice_export_gate、完整公式门 |
| 共享审查 | 原有模型调用不变，程序不以关键词覆盖模型决策 | selective_quality_review、quality_governance、quality_metrics、pipeline_quality_routing |

## 原故障只读回放

原第六题草稿以当前数值表校验回放：0 个问题；输入对象深比较不变。专用回归用 6 种措辞验证不再要求转变表，同时故意改坏分区加和、未知引用仍检出。未调用模型、未重跑付费任务。

## 验证状态

- 普通全量 pytest：2383 passed、17 deselected、12 warnings。
- 后续模型审查/历史原文保留专项：43 passed。
- 最终 `.venv/bin/python scripts/run_quality_gates.py --full`：全部通过，2385 passed、17 deselected、12 warnings，分支覆盖率70%；Ruff、Mypy、公式转换、许可、版本与完整性均通过。`git diff --check` 通过。
- 未重启当前服务、未修改原任务状态；未执行真实付费重跑、Windows 实机或浏览器端到端验收。
- 未提交、推送或发布源码/桌面包。供应商图片请求的 HTTP 400 为另一问题，不在本次调整范围。
