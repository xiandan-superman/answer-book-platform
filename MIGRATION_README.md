# Migration README

本文件用于把 `answer_book_platform_v1_release.zip` 移植到新设备后复现运行。

## 1. 解压

将压缩包解压到任意本地目录，例如：

```text
answer_book_platform_v1/
```

不要把历史任务目录、输出目录或 `.env` 从旧设备手动复制进来。新任务应重新创建新 `task_id`。

## 2. 系统依赖

必须安装：

- Python 3.9+
- Microsoft Word
- `pdftoppm`
- Python 包依赖：见 `requirements.txt`

建议安装：

- LibreOffice / soffice，用于 Word 自动化失败时兜底导出 PDF。

macOS 安装示例：

```bash
python3 scripts/install_dependencies.py
brew install poppler libreoffice
```

Windows 要求：

- 安装 Microsoft Word。
- 安装 Python 3.9+。
- 安装 Poppler，并确保 `pdftoppm.exe` 在 PATH 中。
- 安装脚本会额外安装 `requirements-windows.txt` 中的 `pywin32`，用于 Microsoft Word COM 自动化。
- 执行：

```bat
python scripts\install_dependencies.py
```

## 3. 启动平台

```bash
cd /path/to/answer_book_platform_v1
python3 scripts/check_environment.py
python3 scripts/start_platform.py
```

浏览器打开：

```text
http://127.0.0.1:8765
```

可通过接口确认平台版本：

```text
http://127.0.0.1:8765/api/version
```

网页顶部也会显示当前版本。若从正式发布包解压，包内应包含 `VERSION` 与 `RELEASE_MANIFEST.json`。

macOS 可以运行：

```bash
./start_platform.command
```

Windows 可以运行：

```bat
start_platform_windows.bat
```

## 4. 配置 API Key

在 Web 页面的“模型配置”中填写并保存：

- OpenAI API Key
- DeepSeek API Key

平台会写入本机 `.env`。接口只显示 `api_key_set`，不会回显密钥。`.env` 不应打包或发送给他人。

## 5. 准备教材和真题

建议目录：

```text
textbooks/
exams/
```

教材文件全部平铺在 `textbooks/` 下。支持：

- MinerU JSON
- Markdown
- TXT

真题使用 DOCX。

## 6. 正式执行顺序

1. 在 Web 中创建任务，填写真题 DOCX 路径和教材文件夹路径。
2. 可点击“刷新任务列表”，选择要执行或复核的任务。
3. 保持“生成 PDF/PNG 渲染复核”勾选。
4. 点击“执行任务”，平台会自动轮询当前阶段直到完成或失败。
5. 点击“审计摘要”查看各阶段结果。
6. 点击“逐题复核”检查题干、答案、教材候选和警告。
7. 如需修复结构化答案，点击“读取结构化答案”，编辑后“保存并校验”。
8. 修复后点击“复用结构化结果”重新生成 DOCX/PDF。
9. 点击“最终验收”。
10. 点击“查看文件”下载 DOCX、PDF、PNG 或审计文件。
11. 点击“导出交付包”生成可移交的任务级压缩包。

最终验收状态说明：

- `passed`：可交付。
- `passed_with_warnings`：硬门禁通过，但必须人工确认警告后再交付。
- `failed`：不可交付。

## 7. 命令行等价流程

```bash
python3 scripts/check_environment.py
python3 scripts/create_task.py --exam "/absolute/path/to/exam.docx" --textbooks "/absolute/path/to/textbooks" --provider deepseek
python3 scripts/run_task.py "<task_id>" --render
python3 scripts/audit_final_acceptance.py "<task_id>"
```

修复结构化答案后复跑：

```bash
python3 scripts/audit_answer_fragments.py "<task_id>"
python3 scripts/audit_answer_coverage.py "<task_id>"
python3 scripts/run_task.py "<task_id>" --reuse-fragments --render
python3 scripts/audit_final_acceptance.py "<task_id>"
```

## 8. 页码校准

如果页码不准，在 Web 中点击“页码校准”并保存。保存结果写入教材目录：

```text
textbook_page_map.manual.csv
```

保存后必须重新执行任务，页码覆盖才会参与教材索引。

## 9. 交付包验证

生成发布包：

```bash
python3 scripts/package_release.py
```

验证发布包：

```bash
python3 scripts/verify_release_package.py
python3 scripts/run_quality_gates.py
```

`verify_release_package.py` 会检查 `RELEASE_MANIFEST.json`、`VERSION`、密钥泄漏、历史任务目录、输出目录和缓存文件。

验证通过应显示：

```json
{"ok": true}
```
