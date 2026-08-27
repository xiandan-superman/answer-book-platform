# 源码分发与更新架构

## 普通用户安装入口

普通用户应从 [最新正式版本发布页](https://github.com/xiandan-superman/answer-book-platform-releases/releases/latest) 的 **Assets** 下载 `answer-book-platform-<版本号>-source.zip`。不要下载 `update-manifest.json`，也不要把 GitHub 自动附加的 `Source code (zip)` 当作程序包。

下载后解压整个目录：macOS 双击 `start_platform.command`，Windows 双击 `启动平台.bat`（英文兼容名为 `start_platform_windows.bat`）。两个系统都进入同一个轻量桌面壳，由用户选择“仅本机使用”或“开启局域网监控”。启动器检查 Python 3.9+，首次运行创建专用虚拟环境、安装依赖、启动本地服务并打开 `http://127.0.0.1:8766`。API 配置、日常启动、停止服务和旧 ZIP 版迁移的完整用户步骤以仓库根目录 `README.md` 为准。Windows 入口先由纯标准库 bootstrap 启动图形壳；显示窗口前如发生异常，必须写入用户数据目录下的 `runtime/launcher-bootstrap.log` 并弹出错误，不得因 `pythonw` 无控制台而静默失败。

0.9.18 及更早的无 Git ZIP 版必须手动安装 0.9.19 一次。旧替换器会先把程序目录移动到用户数据备份区；程序目录与用户数据目录跨磁盘时，这个移动实际是慢速复制，用户在空目录窗口期间关闭启动器会中断替换。旧版固定读取的 `update-stable.json` 保持暂停状态。0.9.19 起改读 `update-stable-v2.json`，进入不会先移走可见程序目录的安全替换链路。

## 固定决策

- 普通用户主渠道是 GitHub 源码 ZIP，不要求安装 Git，也不要求安装 DMG/EXE。
- 正式更新由 `main` 完整门禁成功后自动生成的版本标签触发；版本号未增加的普通 push 只检查、不重复发布，禁止人工提前创建正式标签。
- 用户电脑缺少 Python 时只提示安装 Python 3.9+；程序不下载或捆绑 Python。
- 首次启动自动创建用户级虚拟环境并安装依赖。后续静默核对依赖指纹和关键模块；发生变化或缺失时提示，用户确认更新后再安装。
- 用户数据永远位于系统用户数据目录，源码目录视为可以整体替换的只读程序材料。

## 用户启动链路

macOS 的 `start_platform.command` 和 Windows 的 `启动平台.bat` / `start_platform_windows.bat` 只负责发现系统 Python 并打开启动链路。Windows 先进入 `scripts/windows_launcher_bootstrap.py`，再启动 `scripts/source_launcher_gui.py`；macOS 直接进入图形启动器。该入口使用仅监听 `127.0.0.1` 的标准库临时服务承载启动页面，再通过 Windows WebView2 或 macOS WKWebView 的轻量桌面壳显示，负责模式选择、启动状态、打开平台和停止操作。首次运行会先在用户级运行环境中准备壳依赖。旧的 LAN 脚本只作为兼容入口，预选局域网模式后仍走同一链路，禁止直接使用系统 Python 调用 `scripts/start_platform.py`。启动监督器负责：

1. 应用待处理的已校验源码更新。
2. 在用户数据目录创建 `runtime/python-env`。
3. 按当前 Python 与平台选择锁定约束（Python 3.9 使用 `constraints-py39.txt`，Python 3.11+ 使用 `constraints-py311.txt`），并比较运行依赖、Windows 补充依赖和所选约束的联合指纹。Python 3.10 保持有界依赖兼容路径。
4. 首次运行自动安装依赖；已安装环境异常或指纹变化时请求用户确认。
5. 启动 `scripts/start_platform.py`，等待 `/api/version` 健康检查通过。
6. 持续等待 `/api/version` 就绪后只打开一次默认浏览器，不设置会导致慢启动漏开网页的固定 30 秒截止时间；默认浏览器调用失败时使用系统打开命令兜底。如果服务已运行，则只打开页面，不启动第二个工作器。
7. 服务以退出码 75 请求更新重启时，监督器应用更新、复核依赖并重新启动。
8. 服务退出后，监督器通过独立原生窗口和 `runtime/update-progress.json` 同步展示解压、备份、覆盖、依赖检查及启动进度；新版健康检查通过后才标记完成。更新触发前已有浏览器页面时不会重复打开新标签页。

启动入口的跨平台差异保持在最外层：Windows 优先使用 `pyw/pythonw`、隐藏命令行窗口并使用系统 WebView2；macOS 的 `.command` 负责通过系统允许的方式启动独立后台进程，并使用系统 WKWebView，首次打开仍可能需要右键确认。Windows 不得用 `os.execv` 转交到用户级虚拟环境：默认目录 `Answer Book Platform` 含空格，已在 0.9.29 实际触发参数拆分和无控制台静默退出。必须使用参数列表的 `subprocess` 调用、传播子进程退出码，并以真实含空格路径执行 BAT 健康检查。桌面壳使用同一份 HTML/CSS，因此两端视觉和交互一致；`pywebview` 只提供轻量桥接，不捆绑浏览器内核。两端都不捆绑 Python。局域网模式统一传递 `--host 0.0.0.0`，但健康检查和本机浏览器始终使用 `127.0.0.1`，避免打开不可用的 `0.0.0.0` 地址。首次允许外部连接时，Windows 防火墙或 macOS 防火墙可能请求用户确认。

平台健康检查通过后，桌面壳自动隐藏到 Windows 系统托盘或 macOS 菜单栏。窗口关闭事件只隐藏窗口，不停止监督器或服务；再次双击启动文件会唤醒已有桌面壳。托盘/菜单栏必须提供打开平台、显示启动器、停止并退出三个操作。无法创建托盘图标时退化为最小化到任务栏，禁止因用户关闭启动界面而直接终止正在运行或排队的任务。

## 两种源码安装

### Git clone

检测到 `.git` 时，更新操作只允许：clean worktree、当前分支等于配置的稳定分支、本地 HEAD 是远端祖先。程序执行 `fetch` 和 `merge --ff-only`，拒绝覆盖本地修改、切换分支或合并分叉历史。

### GitHub 下载 ZIP

没有 `.git` 时，更新清单选择 `platforms.source`：

1. 下载到用户数据目录 `runtime/updates/<version>`。
2. 校验清单声明的大小和 SHA256。
3. 写入 `pending-source-update.json`，向浏览器返回成功结果。
4. 监督器收到退出码 75 后验证 ZIP 路径，防止 Zip Slip，并确认包中只有一个有效项目根。
5. 先把新源码完整复制到安装目录旁的临时目录，并验证通用入口与 Windows 启动入口。
6. 保持原程序目录原位不动，完整复制旧源码到 `runtime/source-backups`；备份校验通过后才把已准备的新源码覆盖到停止运行的程序目录。
7. 覆盖失败时用备份原位恢复，但绝不删除或移走用户可见的程序目录；成功后删除待更新计划，依赖检查通过后重启。若进程在覆盖期间中断，启动文件和待更新计划仍存在，下次启动可安全重试。

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
3. 累积到正式版本时同步 `APP_VERSION`、`VERSION`、`RELEASE_MANIFEST.json` 和对应的 `CHANGELOG.md` 条目，只推送 `main`，不人工创建标签。
4. `.github/workflows/quality.yml` 完成 Python 3.9、Python 3.11 和 Chromium 门禁；失败只停留在预检阶段，不产生正式版本标签。
5. 门禁全部成功后，`.github/workflows/source-release.yml` 仅为尚未公开的新版本运行。它使用 `scripts/package_release.py` 从 Git 索引白名单生成源码 ZIP，再由 `scripts/verify_release_package.py` 反向检查启动文件、Logo、版本清单和运行数据排除规则；禁止直接用未筛选的 `git archive` 作为普通用户附件。打包反向验证和启动冒烟全部通过后，工作流才自动创建完全匹配的 `v<APP_VERSION>` 标签。
6. 生成包含版本、附件名、大小、SHA256 和依赖指纹的 `update-manifest.json`。
7. 发布到 `xiandan-superman/answer-book-platform-releases`，更新仅供 0.9.19+ 安全替换器使用的 `update-stable-v2.json`。上传和稳定源更新使用有限重试；结束时重新下载公开附件，核对大小、SHA256 和稳定源。若稳定源更新中断，后续运行从已发布清单幂等恢复。旧版固定读取的 `update-stable.json` 保持禁用，防止旧替换逻辑再次触发。
   GitHub Release 说明顶部必须自动生成带版本的正式附件直链，并明确警告不要下载 GitHub 自动生成的 `Source code (zip)`；发布仓库的自动源码包不含平台启动器。
8. 客户端启动后自动静默检查清单。发现旧版本时显示非阻塞提醒；用户查看说明并确认后才更新。
9. 更新前读取统一运行监控；存在运行中或排队任务时拒绝重启更新，并提示用户在任务完成后重试。
10. 更新请求进入独立后台线程，网页通过本机进度接口显示检查、下载、SHA256 校验和准备替换状态；服务退出后由监督器的独立更新窗口继续展示真实安装进度，页面自动等待并重新连接。
11. 覆盖或依赖准备失败时，监督器原位恢复备份、隔离失败计划并重新启动旧版本；页面恢复后明确显示更新失败、回滚成功和可继续使用，不在后续每次启动时自动重复同一失败计划。

自动发布只接收 `main` 的完整质量工作流成功事件，并确认该提交仍是最新 `main`；旧成功运行会安全跳过。生成源码 ZIP 后还会用独立数据目录实际启动压缩包并检查 `/api/version`，之后才创建标签。质量门禁不会调用真实付费模型。

桌面安装包工作流只保留为手动兼容渠道。不得让桌面打包成功与否阻塞源码更新发布。

## 安全和兼容约束

- 更新 URL 必须为 HTTPS，仓库、标签和附件名必须通过白名单格式验证。
- 任何下载都必须校验大小与 SHA256；失败的 `.part` 文件必须删除。
- 更新 API 只能由运行服务的本机调用。
- 更新进度只记录版本、阶段、字节数和公开错误，不记录 API Key、任务材料或用户输出内容。
- 运行中的 Python 服务不直接覆盖自身；所有替换由外层监督器在服务退出后执行。
- 有运行中或排队任务时不得开始会重启服务的程序更新。
- 依赖安装只进入用户级虚拟环境，不写系统 Python。
- API Key 不进入源码包、更新清单、日志或备份说明。
- 修改更新协议、数据路径、版本判断或启动入口时，必须同步更新 `AGENTS.md`、README、本文件和对应测试。
