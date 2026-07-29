# 另一台电脑运行说明

## 1. 解压项目包

将压缩包解压到另一台电脑，例如：

```bash
cd ~/Documents
unzip answer_book_platform_v1_remote_monitor.zip
cd answer_book_platform_v1
```

## 2. 安装依赖

建议先执行环境检查：

```bash
python3 scripts/check_environment.py
```

如缺少 Python 依赖：

```bash
python3 -m pip install -r requirements.txt
```

## 3. 配置模型 Key

可以在前端页面的“模型 API 配置”里填写并保存本机 API Key。

也可以复制 `.env.example` 为 `.env` 后手动填写：

```bash
cp .env.example .env
```

项目包不会包含原电脑的 `.env`，不会携带原电脑的 API Key。

## 4. 启动服务

只在本机使用：

```bash
python3 scripts/start_platform.py --host 127.0.0.1 --port 8765
```

允许同一局域网内另一台电脑访问：

```bash
python3 scripts/start_platform.py --host 0.0.0.0 --port 8765
```

## 5. 在当前电脑查看另一台电脑

在另一台电脑上查看局域网 IP。

macOS：

```bash
ipconfig getifaddr en0
```

假设 IP 是 `192.168.1.23`，在当前电脑浏览器打开：

```text
http://192.168.1.23:8765
```

进入“任务管理”，页面里的“运行监控”会显示另一台电脑的运行状态、进程号、服务日志和任务事件。点击任务事件可以进入对应任务的日志与问题排查页。

不要把 `8765` 端口直接暴露到公网。跨网络访问建议使用 VPN、Tailscale 或 SSH 隧道。
