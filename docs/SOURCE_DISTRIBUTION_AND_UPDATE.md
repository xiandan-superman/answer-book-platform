# 源码分发与更新架构

## 普通用户安装入口

普通用户应从 [最新正式版本发布页](https://github.com/xiandan-superman/answer-book-platform-releases/releases/latest) 的 **Assets** 下载 `answer-book-platform-<版本号>-source.zip`。不要下载 `update-manifest.json`，也不要把 GitHub 自动附加的 `Source code (zip)` 当作程序包。

下载后解压整个目录：macOS 双击 `start_platform.command`，Windows 双击 `start_platform_windows.bat`。启动器检查 Python 3.9+，首次运行创建专用虚拟环境、安装依赖、启动本地服务并打开 `http://127.0.0.1:8766`。API 配置、日常启动、停止服务和旧 ZIP 版迁移的完整用户步骤以仓库根目录 `README.md` 为准。

0.9.12 或更早的无 Git ZIP 版不具备监督器自替换能力：它只能下载并打开新 ZIP。用户必须保留旧目录，解压新版，把需要保留的旧数据目录及本地配置复制到新版同名位置，再首次运行新版启动器。0.9.13 及之后由监督器安装的源码版才进入自动替换链路。

## 固定决策

- 普通用户主渠道是 GitHub 源码 ZIP，不要求安装 Git，也不要求安装 DMG/EXE。
- 正式更新由版本标签触发，不由 `main` 的普通 push 触发。
- 用户电脑缺少 Python 时只提示安装 Python 3.9+；程序不下载或捆绑 Python。
- 首次启动自动创建用户级虚拟环境并安装依赖。后续静默核对依赖指纹和关键模块；发生变化或缺失时提示，用户确认更新后再安装。
- 用户数据永远位于系统用户数据目录，源码目录视为可以整体替换的只读程序材料。

## 用户启动链路

macOS 的 `start_platform.command` 和 Windows 的 `start_platform_windows.bat` 只负责发现系统 Python并调用 `scripts/source_launcher.py`。启动监督器负责：

1. 应用待处理的已校验源码更新。
2. 在用户数据目录创建 `runtime/python-env`。
3. 比较 `requirements.txt`、`requirements-windows.txt` 的联合指纹。
4. 首次运行自动安装依赖；已安装环境异常或指纹变化时请求用户确认。
5. 启动 `scripts/start_platform.py`，等待 `/api/version` 健康检查通过。
6. 打开默认浏览器；如果服务已运行，则只打开页面，不启动第二个工作器。
7. 服务以退出码 75 请求更新重启时，监督器应用更新、复核依赖并重新启动。

## 两种源码安装

### Git clone

检测到 `.git` 时，更新操作只允许：clean worktree、当前分支等于配置的稳定分支、本地 HEAD 是远端祖先。程序执行 `fetch` 和 `merge --ff-only`，拒绝覆盖本地修改、切换分支或合并分叉历史。

### GitHub 下载 ZIP

没有 `.git` 时，更新清单选择 `platforms.source`：

1. 下载到用户数据目录 `runtime/updates/<version>`。
2. 校验清单声明的大小和 SHA256。
3. 写入 `pending-source-update.json`，向浏览器返回成功结果。
4. 监督器收到退出码 75 后验证 ZIP 路径，防止 Zip Slip，并确认包中只有一个有效项目根。
5. 把旧程序目录移动到 `runtime/source-backups`，复制新源码。
6. 新源码入口缺失或复制失败时删除不完整目录并恢复旧目录。
7. 成功后删除待更新计划，依赖检查通过后重启。

源码备份属于可恢复安全措施，不与用户任务数据混放。后续可以增加保留数量清理策略，但不得在没有可用新版本和健康检查证据时删除最后一个旧版本。

## 数据目录与旧版迁移

源码运行和桌面运行统一使用：

- macOS：`~/Library/Application Support/Answer Book Platform`
- Windows：`%LOCALAPPDATA%\Answer Book Platform`
- Linux：`${XDG_DATA_HOME:-~/.local/share}/answer-book-platform`

首次由源码监督器启动时，程序把旧仓库内的任务、输出、日志、缓存、教材、试卷和本地配置复制到用户目录。迁移是“只补缺失文件”的复制：用户数据目录中已经存在的 API Key、教材、任务、输出和配置拥有最高优先级，任何同名旧文件都不得覆盖它。迁移完成后写入一次性标记；确认数据无误前保留旧文件便于恢复。显式设置 `ANSWER_BOOK_DATA_DIR` 的开发预览不执行迁移。

## 发布链路

正式源码更新流程：

1. 完成源码修改和独立数据目录的本地预览。
2. 用户确认后提交并推送 Git。
3. 累积到正式版本时同步 `APP_VERSION`、`VERSION`、`RELEASE_MANIFEST.json`。
4. 创建完全匹配的 `v<APP_VERSION>` 标签。
5. `.github/workflows/source-release.yml` 使用 `git archive` 生成固定顶层目录的源码 ZIP。
6. 生成包含版本、附件名、大小、SHA256 和依赖指纹的 `update-manifest.json`。
7. 发布到 `xiandan-superman/answer-book-platform-releases`，更新 `update-stable.json`。
8. 客户端静默检查清单，用户点击确认后才更新。

桌面安装包工作流只保留为手动兼容渠道。不得让桌面打包成功与否阻塞源码更新发布。

## 安全和兼容约束

- 更新 URL 必须为 HTTPS，仓库、标签和附件名必须通过白名单格式验证。
- 任何下载都必须校验大小与 SHA256；失败的 `.part` 文件必须删除。
- 更新 API 只能由运行服务的本机调用。
- 运行中的 Python 服务不直接覆盖自身；所有替换由外层监督器在服务退出后执行。
- 依赖安装只进入用户级虚拟环境，不写系统 Python。
- API Key 不进入源码包、更新清单、日志或备份说明。
- 修改更新协议、数据路径、版本判断或启动入口时，必须同步更新 `AGENTS.md`、README、本文件和对应测试。
