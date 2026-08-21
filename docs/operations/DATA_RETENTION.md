# 本地数据保留与清理规则

平台把源码、用户输入、任务状态、派生产物和可再生缓存分开管理。清理操作必须先做只读盘点，不根据目录大小或失败状态直接删除。

## 只读盘点

```bash
python3 scripts/data_inventory.py
python3 scripts/data_inventory.py --json > /tmp/answer-book-data-inventory.json
```

脚本只统计分类、体积、文件数和最近修改时间，不输出 API Key，不删除文件。

## 保留边界

- `tasks/`、`outputs/`、`practice_history/`、`textbooks/` 属于保护数据；清理前必须核对任务 ID、历史 ID、引用关系和恢复位置。
- `practice_jobs/`、`cache/`、`logs/`、`runtime/`、`tmp/` 是运行数据；只能在确认没有排队、运行或待恢复任务后按保留期处理。
- `archive/local/` 保存从源码树移出的历史二进制证据，Git 忽略但本机保留。
- `docs/archive/` 保存轻量报告和原型，只作审计参考，不参与运行。
- 桌面 App 的 `build/`、`dist/`、图标和打包脚本仍冻结，不由平台清理流程处理。

任何实际删除都应是独立操作，并在执行前保存精确路径清单、checksum 和恢复方式。
