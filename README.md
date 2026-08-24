# 真题解析与生题平台

本项目是可独立运行的本地真题解析、知识点出题和专项练习平台。`APP_VERSION` 是用户可见正式版本号的唯一来源，`VERSION` 与发布清单必须保持一致；当前版本为 0.9.14。

## 普通用户安装与使用

不需要安装 Git，也不要直接双击 `web/index.html`。程序需要 Python 3.9 或更高版本，并通过本地启动文件打开网页服务。

### 1. 下载正确的程序包

1. 打开 [最新正式版本下载页面](https://github.com/xiandan-superman/answer-book-platform-releases/releases/latest)。
2. 展开 **Assets**，下载名为 `answer-book-platform-<版本号>-source.zip` 的附件。例如 0.9.13 对应 `answer-book-platform-0.9.13-source.zip`。
3. 不要下载 `update-manifest.json`，也不要下载页面底部由 GitHub 自动生成的 `Source code (zip)` 或 `Source code (tar.gz)`；它们不是普通用户启动包。
4. 解压 ZIP，把解压后的整个程序文件夹放在一个长期保留且可写的位置，例如“文稿/真题解析平台”。不要只复制其中某一个启动文件。

### 2. macOS 首次启动

1. 如果电脑没有 Python 3.9+，先从 [Python macOS 下载页](https://www.python.org/downloads/macos/) 安装，然后重新打开程序文件夹。
2. 在程序文件夹中双击 `start_platform.command`。如果 macOS 首次阻止打开，请右键该文件，选择“打开”，再确认一次。
3. 第一次运行会自动创建本平台专用的 Python 环境并安装依赖，所需时间取决于网络速度。安装期间不要关闭终端窗口。
4. 准备完成后，程序会自动打开浏览器并访问 `http://127.0.0.1:8766`。

### 3. Windows 首次启动

1. 如果电脑没有 Python 3.9+，先从 [Python Windows 下载页](https://www.python.org/downloads/windows/) 安装。安装时建议启用 `Add python.exe to PATH`，完成后重新打开程序文件夹。
2. 双击 `start_platform_windows.bat`。
3. 第一次运行会自动创建本平台专用的 Python 环境并安装依赖。安装期间不要关闭命令窗口。
4. 准备完成后，程序会自动打开浏览器并访问 `http://127.0.0.1:8766`。

### 4. 第一次进入平台

1. 在网页顶部进入“API 配置”。
2. 选择使用的服务商，填写 API Key，并先执行连接测试。
3. 测试成功后保存。API Key 只保存在本机用户数据目录，不会上传到 GitHub，也不会在程序更新时被覆盖。
4. 返回首页后，即可上传题目或教材并使用真题解析、按题生题和知识点生题功能。

### 5. 日常启动、关闭与更新

- 每次使用都双击原程序文件夹中的 `start_platform.command`（macOS）或 `start_platform_windows.bat`（Windows），不要直接打开 HTML 文件。
- 网页显示后可以关闭浏览器标签页，但这不会停止服务。需要完全退出时，回到启动程序打开的终端或命令窗口，按 `Control-C`，或直接关闭该窗口。
- 从 0.9.13 开始，网页顶部会静默检查正式更新。发现新版本后点击“检查更新”并确认，程序会校验更新包、备份旧源码、替换程序并重启；任务、教材、输出和 API Key 不会被覆盖。

### 从 0.9.12 或更早的 ZIP 版迁移

旧 ZIP 版只能把 0.9.13 更新包下载并打开，不能自动替换自身，因此需要手动迁移一次：

1. 保留旧程序文件夹，不要删除或覆盖它。
2. 按上面的下载步骤解压最新正式版，得到一个新的程序文件夹。
3. 如需保留旧数据，把旧文件夹中的 `tasks`、`outputs`、`textbooks`、`exams`、`practice_history` 等数据目录复制到新程序文件夹的同名位置；把旧 `config/api_keys.json` 和 `config/providers.local.json`（如果存在）复制到新文件夹的 `config` 目录。复制前不要删除旧文件夹，以便恢复。
4. 双击新文件夹中的启动文件。首次启动会把这些旧数据再复制到系统用户数据目录；确认网页中的任务、教材和 API 配置正常后，再自行归档旧文件夹。

如果旧平台是通过 `git clone` 获取且源码没有本地修改，可以直接在旧网页点击更新，再重新双击启动文件，不需要上述手动复制。

## 核心原则

- 程序主控任务流程，模型只作为局部判断器。
- 第一版内置 OpenAI 与 DeepSeek，均通过 OpenAI-compatible Chat Completions 接口调用。
- API Key 通过独立的“API 配置”页面统一管理，底层保存在程序数据目录的 `config/api_keys.json`，不写入任务产物和日志，更新程序不会覆盖。
- v4 公式链路强制要求：公式不得混入普通正文，必须进入公式对象字段。
- 生成、审计、渲染由平台程序控制，执行任务时不得临时修改工具链。
- 当前本地平台采用单服务进程、多线程工作器架构。正式启动入口和命令行解析入口共享进程锁；重复启动会在恢复队列或调用模型前停止，避免重复任务和额外费用。异常退出后操作系统会自动释放锁，可直接重新启动。
- 多模态模型直接接收原题图片；文本模型才使用单独的视觉备用模型。模型能力来自本地配置并按“服务商 + 模型 + 能力声明”缓存，不会为了识别能力重复调用或试探模型。
- 推荐使用 GitHub 源码启动版。首次启动自动创建独立 Python 环境并安装依赖；顶部“检查更新”只接受版本标签发布且通过 SHA256 校验的源码更新。用户数据和 API Key 位于程序目录之外，不会被源码替换覆盖。

## 开发者启动

开发者可直接运行：

```bash
python3 scripts/start_platform.py
```

直接运行不会接管依赖安装和自动重启，日常用户验收应使用双击启动器。

## 源码更新

- 网页启动后静默检查稳定更新；发现版本时只改变“检查更新”提示，不会自动修改本机。
- 只有仓库创建与 `APP_VERSION` 一致的版本标签后，普通用户才会收到更新；普通 `main` push 不会下发。
- 用户确认后，程序下载源码 ZIP、校验大小和 SHA256、退出旧服务、备份旧源码、替换代码并自动重启。
- Git clone 用户使用安全的 `fetch + fast-forward merge`；本地有源码修改、分支不符或历史分叉时会拒绝自动更新。
- 新版本若改变依赖清单，确认框会提前说明；重启时检查并安装缺失依赖。依赖没有变化时不会重复安装。
- 更新失败会恢复旧源码。API Key、教材、任务、日志和输出位于 macOS `~/Library/Application Support/Answer Book Platform` 或 Windows `%LOCALAPPDATA%\Answer Book Platform`。
- 旧数据迁移只复制用户数据目录中尚不存在的文件；已有 API Key、教材、任务、输出和配置不会被同名旧文件或新版程序包覆盖。

开发与发布细节见 [源码分发与更新架构](docs/SOURCE_DISTRIBUTION_AND_UPDATE.md)。

## 配置模型

首次启动会自动创建内部配置文件 `config/api_keys.json`。普通用户不需要打开文件：
在首页或顶部导航进入“API 配置”，选择已接入的平台，填写 Key，测试成功后保存。
同一个页面可替换或删除已保存的 Key，所有需要模型的模块统一读取这份配置。
灵算的 GPT 文本模型与 `gpt-image-2` 使用独立 Key：分别配置“灵算 · OpenAI”和“灵算 · OpenAI 图片”，平台不会在两类请求之间混用 Key。
专项练习可以单独选择正式生题的服务商和模型，API Key 仍统一从“API 配置”页面读取。用户选择的主模型固定负责蓝图设计、正式生题、单题重生和题图修复；视觉备用模型只在尚未解析的图片需要识别时使用，图片识别完成后不会因为原附件仍存在而静默替换正式生题模型。

专项练习题图采用可同时渲染到网页和 Word 的结构化图形数据。题目正文生成后，题图会独立完成元素、曲线采样、状态点坐标和可证明几何关系检查；不合格时只修复当前题图，不重写题目或同批其它题。P-V 等坐标图中的节点与曲线使用同一数据坐标，程序会补齐可确定的坐标轴、状态点和终压辅助线。

正式生题按小批次调用模型；如果模型只返回批次中的部分题目，平台会保留序号唯一且结构有效的题目，并对缺失或重复序号逐题独立补生两次。仍未补齐时只标记对应题目失败，页面会展示安全的结构诊断，不再让同批已成功题目一起作废。

蓝图审查页可选“每个蓝图生成多道题”。关闭时保持一项蓝图对应一题；开启时每项生成 2～3 道变式，可选择基础到挑战的递进训练或保持同难度。平台为每道变式分配独立身份，并按父蓝图分组显示，因此单题编辑、重新生成、勾选和 Word 导出互不覆盖。最终题量只做耗时、费用和配图处理量提示，不设置强制总数上限。

“知识点出题”是独立于真题专项练习的入口。可以只填写知识点名称，也可以粘贴教材原文或混合上传图片、PDF、Word、TXT、Markdown；生成前可选择题目数量、题型、难度方向和补充要求。平台会先生成可审查的出题蓝图，确认后再生成具体模拟题，并复用现有编辑、重新生成、复制和 Word 导出能力。

- macOS：`~/Library/Application Support/Answer Book Platform/config/api_keys.json`
- Windows：`%LOCALAPPDATA%\Answer Book Platform\config\api_keys.json`

也可以使用环境变量覆盖文件中的值：

```bash
export OPENAI_API_KEY="..."
export DEEPSEEK_API_KEY="..."
export ARK_API_KEY="..."
export DASHSCOPE_API_KEY="..."
export ARK_IMAGE_MODEL="doubao-seedream-5-0-260128"
export BAILIAN_IMAGE_MODEL="qwen-image-2.0-pro"
export ANSWER_BOOK_IMAGE_SIZE="2048x2048"
```

旧版 `.env` 和 `config/providers.local.json` 中已有的 Key 会在首次启动时自动迁移。
这两个旧文件仍兼容，但新版本以独立 Key 文件为主要配置入口。

“API 配置”页面集中管理 OpenAI、DeepSeek、火山方舟、智谱、阿里云百炼和云雾等已接入平台。新 Key 必须先通过连接测试才能保存；模型配置页面只负责选择具体模型，不再重复输入 Key。页面和接口只返回 `api_key_set`，不会回显已保存密钥；发布包脚本会排除真实 Key 文件。

真题模型配置中可单独选择“高风险正确性复核”模型。它仅用于计算/作图综合题的证据复核，以及硬校验失败题的单题纠错；不配置时自动复用结构化解析模型，不会额外引入另一条隐式模型路线。命令行可用 `--correctness-provider` 和 `--correctness-model` 显式指定。

火山方舟使用 OpenAI 兼容接口，默认地址为 `https://ark.cn-beijing.volces.com/api/v3`。模型选择处可以直接填写方舟控制台里的模型 ID 或推理接入点 ID，例如 `doubao-seed-1-6-250615` 或 `ep-...`。

阿里云百炼使用 OpenAI 兼容 Chat Completions 接口调用通义千问多模态模型，默认地址为 `https://dashscope.aliyuncs.com/compatible-mode/v1`；如需使用百炼业务空间专属域名，可在 `providers.local.json` 中把 `bailian.base_url` 改为 `https://{WorkspaceId}.cn-beijing.maas.aliyuncs.com/compatible-mode/v1`。百炼图片生成默认使用 Qwen-Image `qwen-image-2.0-pro`，通过 DashScope 多模态生成接口调用。

作图题会优先使用图片模型直接生成 PNG，再插入最终文档；如果图片接口不可用，会自动回退到程序绘图。图片模型可在 `providers.local.json` 中配置 `image_model`，或用 `ARK_IMAGE_MODEL` / `ANSWER_BOOK_IMAGE_MODEL` 环境变量指定。

Seedream 4.5 / 5.0 系列要求 2K 级以上输出尺寸，建议 `ANSWER_BOOK_IMAGE_SIZE=2048x2048`。程序会在调用 Seedream 4.5 / 5.0 时把过小的像素尺寸自动提升到合规范围，避免 `1024x1024` 被方舟接口拒绝。

## 命令行完整流程

1. 检查环境：

```bash
python3 scripts/check_environment.py
```

2. 创建任务：

```bash
python3 scripts/create_task.py \
  --exam "/absolute/path/to/exam.docx" \
  --textbooks "/absolute/path/to/textbooks" \
  --provider deepseek
```

命令行入口会在创建任务前发现该目录中的受支持教材文件、建立或复用教材索引，并把精确文件清单绑定到任务。目录为空、教材包无效或页码映射失败时会立即报错，不会先进入模型流水线。

3. 执行任务：

```bash
python3 scripts/run_task.py "<task_id>" --render
```

正式执行默认要求高质量公式链路就绪，即 `check_environment.py` 中：

```text
formula_conversion.preferred_chain_ready = true
```

如果只验证程序主控流程，不调用模型：

```bash
python3 scripts/run_task.py "<task_id>" --no-model --render
```

如果已经人工或程序修复了：

```text
tasks/<task_id>/stage_outputs/answer_fragments.json
```

并且只想重新生成 Word、PDF 与审计结果，不允许再次调用模型覆盖结构化答案：

```bash
python3 scripts/run_task.py "<task_id>" --reuse-fragments --render
```

仅在排查环境时，才允许临时放宽公式链路：

```bash
python3 scripts/run_task.py "<task_id>" --allow-formula-fallback --render
```

该参数不作为正式生产流程使用。

4. 查看产物：

```text
outputs/<task_id>/answer_book.docx
outputs/<task_id>/word_rendered/answer_book.pdf
outputs/<task_id>/word_rendered/page-*.png
tasks/<task_id>/stage_outputs/pipeline_status.json
tasks/<task_id>/stage_outputs/acceptance_report.json
```

## 本地 Demo 自测

```bash
python3 scripts/create_demo_inputs.py
python3 scripts/create_task.py \
  --exam "exams/demo_物理化学真题.docx" \
  --textbooks "textbooks" \
  --provider deepseek
python3 scripts/run_task.py "<task_id>" --render
```

## 平台质量检查

日常开发先运行平台质量门禁：

```bash
python3 scripts/run_quality_gates.py
```

安装 `requirements-dev.txt` 后可运行包含 lint、类型检查和覆盖率的完整门禁：

```bash
python3 -m pip install -r requirements-dev.txt
python3 scripts/run_quality_gates.py --full
```

跨任务查看 Shadow 质量规则的样本量、影响范围和无人值守动作上限：

```bash
python3 scripts/audit_quality_metrics.py --output cache/quality_metrics_report.json
```

真题流程还会生成 `academic_expression_audit.json`，把公式、单位、向量、矩阵、反应式和学科符号统一记录为跨学科表达节点。该审计完全本地运行，不调用额外模型，也不改写原答案。

只有本地审计无法判定的少量高风险表达才会进入 `selective_quality_review.json`。默认上限是单任务 8 个候选、1 个批次、1 次真实请求，并使用内容指纹缓存；请求失败会降级为风险告警，不默认触发紧凑重试或备用模型。默认只在出图前做这一次复核；内容质量阶段结束后的同类检查仅写入 Shadow 报告。

版本一致性和本地数据只读盘点可单独运行：

```bash
python3 scripts/check_version_consistency.py
python3 scripts/data_inventory.py
```

跨设备迁移见 [MIGRATION_README.md](MIGRATION_README.md)，工程门禁见 [docs/operations/QUALITY.md](docs/operations/QUALITY.md)，数据保留规则见 [docs/operations/DATA_RETENTION.md](docs/operations/DATA_RETENTION.md)。

## 当前版本范围

当前平台已实现：

- 本地平台目录结构。
- 本地 Web 控制台。
- Web 任务列表与任务选择。
- Web 任务执行自动轮询状态。
- Web 任务文件列表与安全下载。
- 任务级交付包导出，打包 DOCX、PDF、渲染页和关键审计报告。
- macOS / Windows 启动入口。
- 依赖安装脚本。
- Windows 专用依赖文件 `requirements-windows.txt`，用于 Word COM 自动化。
- OpenAI / DeepSeek / 火山方舟 / 阿里云百炼 provider 配置。
- 独立 API 配置页面，按平台测试并保存到程序数据目录的 `config/api_keys.json`，所有模型模块统一读取且不回显密钥。
- `/api/version` 版本接口与 Web 顶部版本显示。
- API Key 脱敏读取。
- 发布包内置 `VERSION` 与 `RELEASE_MANIFEST.json`。
- 发布包反向验证，检查密钥、任务历史、输出结果和缓存是否被误打包。
- 项目完整度审计，检查关键模块、脚本、Web 入口和文档是否齐备。
- 发布前一键质量门禁，串联编译、自测、公式、完整度、打包和 release 反向验证。
- OpenAI-compatible 调用适配层。
- v4 结构化答案 schema 文档。
- v4 公式泄漏审计。
- 任务创建与任务状态文件。
- Huey + SQLite 持久出题队列；支持服务重启后安全恢复，队列数据库不复制上传正文。
- 环境检查。
- Provider 配置自检。
- DOCX 真题结构抽取。
- MinerU JSON / Markdown / TXT 教材索引。
- 教材候选检索。
- 逐题 API 结构化生成。
- v4 公式对象校验。
- DOCX 生成。
- DOCX 普通正文公式泄漏审计。
- Microsoft Word 导出 PDF。
- PDF 渲染 PNG。
- PNG 渲染页尺寸/非空审计。
- 验收报告。
- Web 审计摘要查看。
- Web 页码校准查看与保存。
- Web 结构化答案读取、编辑、保存与 v4 校验。
- 答案覆盖率审计：防止漏题、重复题、未知题号进入正式 Word。
- 逐题复核视图与 CSV 导出：合并题干、答案、覆盖率提示和教材证据候选。
- 最终验收门禁：统一检查环境、题目结构、检索、覆盖率、DOCX、渲染和输出文件存在性。
- 复用已存在 v4 结构化答案片段重新生成 DOCX / PDF，避免模型覆盖修复结果。
- `textbook_page_map.manual.csv` 手工页码覆盖。
- 基础图形重绘：相图、折线图、电子衍射斑点图。
- matplotlib 中文字体自动配置；随包内置 `dolbydu/font` 仓库中的完整字体集合，其他用户下载完整程序包后可直接使用这些字体渲染图中文字。
- 公式使用 `latex2mathml -> 跨平台 MathML-to-OMML -> Word OMML` 专业链路；安装了 Microsoft Word 时也可优先复用其 `mathml2omml.xsl`。
- 内置最小 OMML 转换支持分式、上下标、上下标组合。

当前持续改进范围：

- 页码校准的批量预览、教材原文对照和冲突提示。
- 更大规模题库的队列容量压测，以及需要多机部署时从 SQLite 切换到 Redis/PostgreSQL 后端。
- 极复杂 LaTeX 的公式语义自动修正、独立模型复检与可机器验证的降级策略。

当前平台已经能完成程序主控的生产流水线；后续会继续增强图形、页码校准和复杂公式排版能力。不复用旧版空 OMML 绕过逻辑。

## 质量约束

- 执行任务时不允许模型修改工具链。
- 模型输出必须通过 v4 schema。
- 高质量公式链路要求 `latex2mathml`、`lxml` 和随安装包分发的 `math_ml2omml`；不再强制依赖用户安装 Microsoft Word。`check_environment.py` 会显示 `preferred_chain_ready`。
- 正式流水线默认要求 `preferred_chain_ready=true`，否则环境阶段失败。
- 使用 `--reuse-fragments` 时，平台必须先重新校验已有 `answer_fragments.json`，不合格则停止。
- 普通 text segment 中出现公式样式内容会失败。
- `answer` 字段中出现公式样式内容会失败或被程序归一化为“见解析”。
- DOCX 中 `<w:t>` 普通文本残留公式会失败。
- OMML 公式对象不能为空。
- OMML 显示文本中不得残留 LaTeX 反斜杠命令。

## 手工页码校准

如果教材自动页码识别不准，在教材文件夹放置：

```text
textbook_page_map.manual.csv
```

字段参考：

```csv
textbook,pdf_page_idx,printed_page,citation_textbook,page_source,verified,confidence,notes
物理化学第6版下3,12,210,物理化学第6版下,manual,true,high,manual checked
```

平台会用该文件覆盖自动页码映射。

Web 控制台中可以点击：

```text
页码校准
```

读取当前任务生成的 `textbook_page_map.csv` 和教材目录下的 `textbook_page_map.manual.csv`。编辑区保存的是 JSON 数组，保存后会写回教材目录：

```text
textbooks/textbook_page_map.manual.csv
```

保存页码后，需要重新执行任务，手工页码才会重新参与教材索引与引用生成。

## 结构化答案复核

Web 控制台中可以点击：

```text
读取结构化答案
保存并校验
```

读取和编辑当前任务：

```text
tasks/<task_id>/stage_outputs/answer_fragments.json
```

保存时必须通过 v4 校验；否则平台拒绝覆盖文件。命令行也可以校验：

```bash
python3 scripts/audit_answer_fragments.py "<task_id>"
```

答案覆盖率也可以单独校验：

```bash
python3 scripts/audit_answer_coverage.py "<task_id>"
```

覆盖率硬失败包括：真题题目缺少答案片段、答案片段重复、答案片段题号不存在于真题结构、答案为空。教材证据为空、章节/小题号不一致会进入警告，用于人工复核。

修复并保存后，用复用模式重新生成正式文档：

```bash
python3 scripts/run_task.py "<task_id>" --reuse-fragments --render
```

## 逐题复核

Web 控制台中可以点击：

```text
读取逐题复核
导出复核 CSV
```

逐题复核会合并：

- 真题题干
- 结构化答案
- 答案覆盖率提示
- 已引用教材证据
- 前 5 个检索候选

命令行导出：

```bash
python3 scripts/export_question_review.py "<task_id>"
```

默认输出：

```text
tasks/<task_id>/stage_outputs/question_review.csv
```

## 渲染复核

Web 控制台默认勾选：

```text
生成 PDF/PNG 渲染复核
```

正式任务应保持勾选。macOS 下平台会优先尝试 Microsoft Word 导出 PDF，默认 25 秒超时、尝试 1 次；失败后使用 LibreOffice/soffice 兜底。可用环境变量调整：

```bash
export WORD_EXPORT_TIMEOUT_SECONDS=25
export WORD_EXPORT_ATTEMPTS=1
```

## 最终验收

Web 控制台中可以点击：

```text
最终验收
```

命令行：

```bash
python3 scripts/audit_final_acceptance.py "<task_id>"
```

最终验收会写出：

```text
tasks/<task_id>/stage_outputs/final_acceptance_report.json
```

硬门禁包括：专业公式链路就绪、题目结构通过、教材检索通过、答案覆盖率通过、DOCX 审计通过、渲染审计通过、DOCX/PDF 文件存在、流水线没有失败阶段。

最终验收状态：

- `passed`：硬门禁通过且无警告。
- `passed_with_warnings`：硬门禁通过，但存在需要人工确认的警告。
- `completed_with_issues`：确定性交付门禁已通过，但最终引用的图片被发现科学性或语义风险；可下载附带风险报告的交付包，但不视为最终验收通过。
- `failed`：存在硬错误，不能交付。

报告中 `delivery_ready` 表示机器可证明的文件交付门禁，`formal_acceptance_passed` 表示是否可对用户声称“最终验收通过”；两者不得再由同一个布尔值混用。

## 文件下载

Web 控制台中点击：

```text
查看文件
```

会列出当前任务的阶段文件和输出文件，可直接下载。下载接口只允许访问当前任务的 `stage_outputs` 和 `outputs` 下的文件。

## 专项生题的长材料与超时策略

专项生题上传的文件会先保存为内容寻址资源，后台任务只持有 `resource_id`，不再在每个阶段重复保存 Base64。原文、公式、表格和图片锚点由平台确定性抽取并缓存；范围解析模型只返回来源边界、知识点、约束和 `content_refs`，完整 `source_content` 由平台按引用重建。

范围解析与蓝图规划使用分层超时：连接超时保持较短，首字节等待随阶段总预算自适应，读取阶段按空闲时间判断。默认范围解析安全上限为 900 秒，蓝图规划为 600 秒；有部分流式输出的硬截止请求不会自动整段重试。可通过以下环境变量灰度调整：

```bash
export PRACTICE_ANALYZE_TIMEOUT_SECONDS=900
export PRACTICE_PLAN_TIMEOUT_SECONDS=600
export PRACTICE_MODEL_CONNECT_TIMEOUT_SECONDS=15
export PRACTICE_MODEL_FIRST_BYTE_TIMEOUT_SECONDS=180
export PRACTICE_MODEL_READ_IDLE_TIMEOUT_SECONDS=45
export PRACTICE_ANALYZE_MAX_OUTPUT_TOKENS=6000  # 可选硬覆盖；默认会按材料复杂度在 6000/9000/12000 间自适应
```

模型调用台账会在请求发出前记录估算输入，并在流式响应期间累计部分输出。供应商返回正式 usage 后会覆盖估算值；`usage_source` 用于区分 `platform_estimated`、`platform_estimated_partial` 和 `provider_reported`。

## 任务交付包

Web 控制台中点击：

```text
导出交付包
```

会在任务输出目录生成：

```text
outputs/<task_id>/delivery/<task_id>_delivery.zip
```

交付包包含：

- `answer_book.docx`
- `answer_book.pdf`
- 渲染页 PNG
- 最终验收报告
- 逐题复核 CSV
- 关键审计 JSON

如果最终验收为 `failed`，默认拒绝生成交付包。`passed_with_warnings` 可以生成，但警告必须人工确认。`completed_with_issues` 也可生成交付包，但界面和清单必须明确标记“待复核”，不得显示为验收通过。

## 图形重绘

任务阶段目录可放置：

```text
tasks/<task_id>/stage_outputs/figure_specs.json
```

支持 `phase_diagram`、`line_chart`、`diffraction_pattern`。示例见：

[figure_specs.example.json](config/figure_specs.example.json)
