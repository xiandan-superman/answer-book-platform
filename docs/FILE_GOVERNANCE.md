# 文件治理审计与 Dry-run 报告

审计范围为平台工作区，不直接删除历史数据。当前占用最大的目录是运行缓存和任务过程文件：`tasks/` 约 2.7 GB、`cache/` 约 1.2 GB、`textbooks/` 约 501 MB、`dist/` 约 308 MB、`assets/` 约 270 MB。它们的体积不能作为“无用”的唯一依据，必须先判断是否仍被任务或发布流程引用。

## 分类与处理

| 分类 | 当前例子 | 处理规则 |
|---|---|---|
| 规范源代码 | `app/`、`web/`、`tests/`、`config/*.example` | 纳入平台版本控制；公共能力优先通过单独模块暴露 |
| 规范用户数据 | `practice_history/*与内部 history_id 一致`、教材原始包 | 保留；迁移时记录 checksum 和来源 |
| 派生候选 | `*_repaired.json`、`*_semantic_candidate.json` | 不进入任务中心；后续迁移到候选目录，保留引用和生成原因 |
| 运行时产物 | `practice_jobs/`、`tasks/`、`outputs/`、`logs/`、`cache/`、`runtime/`、`output/` | 默认忽略；按保留期和任务状态清理，不提交 |
| 发布/桌面 App 文件 | `build/`、`dist/`、`assets/app-icon/`、`docs/DESKTOP_APP.md`、App 打包脚本 | 当前冻结，平台任务不得修改、构建或提交 |
| 设计与审计材料 | `design-qa*.md`、`final_acceptance_report.md`、`artifacts/final_delivery/` | 作为审计证据归档；截图和报告标注日期与任务 ID |

## 迁移顺序

1. 为历史和候选文件读取 `history_id`，校验文件名、内部 ID、checksum 和任务引用。
2. 将候选与修复产物迁移到独立目录，读取接口同时支持旧路径和新路径。
3. 建立教材内容寻址索引，重复 ZIP 只保留一个物理副本，元数据记录原始文件名。
4. 对已完成且超过保留期的过程目录做可恢复归档，确认无任务引用后再清理。
5. 每次清理前生成清单和 dry-run 报告，禁止使用未解析的通配符递归删除。

本次重构只修正了任务中心的规范历史边界，没有删除 2.5 GB 教材/任务数据，也没有触碰桌面 App 冻结路径。

## 可重复盘点

运行 `python3 scripts/data_inventory.py` 可生成当前工作区的只读分类统计；规则和恢复要求见 `docs/operations/DATA_RETENTION.md`。下表是 2026-08-06 的历史快照，不再作为当前体积依据。

## 2026-08-06 历史盘点

| 路径 | 体积 | 数量/状态 | Dry-run 决策 |
|---|---:|---|---|
| `tasks/` | 2.7 GB | 33 个任务：8 完成、21 失败、4 取消 | 保留。失败任务是模型超时、JSON、图件和最终验收的诊断证据，不按状态直接删除 |
| `cache/` | 1.2 GB | 教材索引与解析缓存 | 保留。需先建立内容 hash 和引用清单，再判断孤立缓存 |
| `textbooks/` | 501 MB | 原始教材包、页码表、共享库 | 保留。属于规范输入数据 |
| `dist/` | 308 MB | 多个桌面 App 历史发布包 | 冻结。当前是平台任务，不得删除或重建 |
| `assets/` | 270 MB | 字体、图标和应用资源 | 保留。须通过代码/打包引用图后再去重 |
| `practice_jobs/` | 5.9 MB | 59 个后台任务记录 | 交由程序保留策略清理；运行/排队任务永不清理 |
| `practice_history/` | 3.6 MB | 含 6 个 repaired/candidate 旁路产物 | 规范历史保留；候选文件待迁移到独立候选目录，不直接删除 |
| `.playwright-cli/` | 7.5 MB | 跨多次审计的截图、快照、日志和测试 Word | 可再生候选。保留最新验收证据后，旧会话可按日期归档 |
| `.DS_Store` / `__pycache__` / `.pytest_cache` | < 1 MB | 89 个 `.DS_Store`/`.pyc` 文件（审计时） | 可再生候选；已被 `.gitignore` 隔离 |

## 安全清理边界

1. 第一批只允许处理 `.DS_Store`、`*.pyc`、`__pycache__/`、`.pytest_cache/` 和已归档的 Playwright 会话文件。
2. `tasks/`、`practice_history/`、`textbooks/`、`artifacts/`、`dist/` 和 `assets/` 必须有逐文件引用证据后才能处理，不允许根据目录大小或任务失败状态删除。
3. 清理前必须输出精确路径、大小、修改时间、所属任务、引用者和恢复方式；实际删除与 dry-run 必须是两次独立操作。
4. 本次 dry-run 不执行删除，不改变任何教材、任务、历史、发布包或交付证据。

## 2026-08-09 整理结果

- 根目录设计审计报告统一归入 `docs/archive/design-audits/2026-08/`。
- 早期界面原型归入 `docs/archive/prototypes/`。
- 一次性最终验收脚本归入 `tools/legacy/final-acceptance-20260801/`。
- 对应 4.8 MB 历史二进制验收产物移动到可恢复的 `archive/local/final-acceptance-20260801/`，未删除。
