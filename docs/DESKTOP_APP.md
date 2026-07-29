# 桌面 App 与局域网监控

桌面版将 Python、本地 Web 服务、前端页面、公式与绘图依赖一起打包。用户双击 App 后会打开独立窗口，不需要手动运行 Python 或输入本机网址。

## 运行数据位置

源码运行时仍使用项目目录。打包后的 App 将可写数据与只读程序文件分开：

- macOS：`~/Library/Application Support/Answer Book Platform/`
- Windows：`%LOCALAPPDATA%\Answer Book Platform\`

目录中保存：

- `.env`：本机 API Key。
- `tasks/`：任务记录。
- `outputs/`：交付结果。
- `textbooks/`、`exams/`：本机教材和真题。
- `logs/`：运行日志。
- `runtime/lan_access.json`：局域网监控账号与随机密码。

也可以用 `ANSWER_BOOK_DATA_DIR` 指定其他数据目录。

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
dist/真题解析平台.app
```

未签名 App 第一次运行可能被 Gatekeeper 阻止。正式分发时需要 Apple Developer ID 签名和公证。

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
dist\真题解析平台\真题解析平台.exe
```

整个 `dist\真题解析平台\` 目录需要一起复制，不能只复制其中的 exe。正式分发可以在此基础上制作 Inno Setup 或 MSI 安装包。

## 外部软件

App 已包含 Python 运行依赖，但文档渲染仍需要：

- macOS：Microsoft Word 或 LibreOffice。
- Windows：Microsoft Word；也可安装 LibreOffice 作为备用。
- PDF 转 PNG：系统需提供 `pdftoppm`。

没有这些外部软件时，解析和 DOCX 生成仍可运行，但 PDF/PNG 渲染会受限。
