# 桌面 App、应用内更新与局域网监控

桌面版将 Python、本地 Web 服务、前端页面、公式与绘图依赖一起打包。用户双击 App 后会打开独立窗口，不需要手动运行 Python 或输入本机网址。

## 运行数据位置

源码运行时仍使用项目目录。打包后的 App 将可写数据与只读程序文件分开：

- macOS：`~/Library/Application Support/Answer Book Platform/`
- Windows：`%LOCALAPPDATA%\Answer Book Platform\`

目录中保存：

- `config/api_keys.json`：软件内部保存的本机 API Key；通过“API 配置”页面管理，更新或替换 App 不会覆盖。
- `tasks/`：任务记录。
- `outputs/`：交付结果。
- `textbooks/`、`exams/`：本机教材和真题。
- `logs/`：运行日志。
- `runtime/lan_access.json`：局域网监控账号与随机密码。

也可以用 `ANSWER_BOOK_DATA_DIR` 指定其他数据目录。

## 应用内更新

顶部导航的“检查更新”会读取 GitHub Releases 中的 `update-manifest.json`：

- 桌面安装版只下载与当前系统匹配、且 SHA256 校验通过的安装包，随后打开安装程序；用户完成安装并重启即可。
- 源码安装版只允许在目标分支无已修改源码、且远程历史可快进时拉取更新；不会强制覆盖本地修改。
- 更新只替换程序文件，不覆盖 API Key、教材、真题、任务历史和输出文件。
- 模型 API 仍由用户自行填写和付费，更新仓库不保存任何 API Key。

正式发布使用私有源码仓库 `xiandan-superman/answer-book-platform` 和公共二进制更新仓库
`xiandan-superman/answer-book-platform-releases`。公共仓库只放安装包、校验清单和更新说明，用户无需 GitHub Token。

## 局域网监控

桌面版默认监听 `0.0.0.0:8766`。启动后在“运行监控”页面查看并复制：

- 局域网访问地址。
- 监控账号。
- 本机随机生成的监控密码。

同一局域网内的管理电脑通过该地址访问。远程请求使用 HTTP Basic Authentication；本机访问不要求输入密码。首次运行时，系统防火墙可能要求允许局域网连接。

如需固定密码，在启动 App 前设置：

```text
ANSWER_BOOK_LAN_PASSWORD=自定义密码
```

API Key 和密码不会进入运行日志。修改教材库发布权限等敏感管理操作仍受本机限制。

首次启动会自动创建 `config/api_keys.json`。旧版 `.env` 或
`config/providers.local.json` 中已有的 Key 会在首次迁移时写入该文件。
用户在首页或顶部导航进入“API 配置”，按平台填写 Key、测试连接并保存；
模型配置页只保留服务商和具体模型选择，不再重复配置 Key。
分享版安装包只包含空白模板，不包含开发者或其他用户的真实 Key。

## macOS 构建

构建必须在 macOS 上执行：

```bash
python3 -m venv .venv-app
source .venv-app/bin/activate
python -m pip install -r requirements.txt -r requirements-build.txt
python scripts/build_macos_app.py
```

输出：

```text
dist/真题解析与生题平台.app
```

当前安装包尚未签名。第一次运行可能被 Gatekeeper 阻止，可在“系统设置 → 隐私与安全性”中确认打开。

## Windows 构建

构建必须在 Windows 上执行：

```bat
py -m venv .venv-app
.venv-app\Scripts\activate
python -m pip install -r requirements.txt -r requirements-windows.txt -r requirements-build.txt
python scripts\build_windows_app.py
```

输出：

```text
dist\真题解析与生题平台\真题解析与生题平台.exe
```

整个 `dist\真题解析与生题平台\` 目录需要一起复制，不能只复制其中的 exe。正式分发可以在此基础上制作 Inno Setup 或 MSI 安装包。

当前安装包尚未签名，Windows SmartScreen 可能显示“未知发布者”，用户可选择继续运行；受单位安全策略管理的电脑可能禁止运行。

## 发布授权

当前采用 `SOFTWARE_LICENSE.md`：用户可下载、安装并在学习、工作和业务中正常使用软件。协议只保留必要的版权、第三方 API 费用与生成内容复核说明。

## 外部软件

App 已包含 Python 运行依赖，但文档渲染仍需要：

- macOS：Microsoft Word 或 LibreOffice。
- Windows：Microsoft Word；也可安装 LibreOffice 作为备用。
- PDF 转 PNG：系统需提供 `pdftoppm`。

没有这些外部软件时，解析和 DOCX 生成仍可运行，但 PDF/PNG 渲染会受限。
