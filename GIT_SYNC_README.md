# Git 同步说明

## 适合同步的内容

Git 仓库用于同步程序代码、前端页面、脚本、配置模板、文档、示例真题和教材库文件。

以下内容不会进入 Git：

- `.env`
- `config/providers.local.json`
- `tasks/`
- `outputs/`
- `logs/`
- `cache/`
- `tmp/`
- `output/`
- `.playwright-cli/`
- `quality_gates_report.json`
- `textbooks/textbook_page_map.manual.csv`

这些都是本机运行状态、输出结果、日志、缓存或密钥，应该在每台电脑本地保留。

## 第一次推送到远程仓库

先创建一个私有远程仓库，例如 GitHub、Gitee、GitLab 或内网 Git 服务。

然后在当前电脑执行：

```bash
cd /Users/ljj/Documents/真题解析/answer_book_platform_v1
git remote add origin <你的远程仓库地址>
git push -u origin main
```

远程仓库地址示例：

```text
git@github.com:your-name/answer_book_platform_v1.git
https://gitee.com/your-name/answer_book_platform_v1.git
```

## 另一台电脑第一次同步

```bash
git clone <你的远程仓库地址>
cd answer_book_platform_v1
python3 -m pip install -r requirements.txt
python3 scripts/check_environment.py
```

配置本机 API Key：

```bash
cp .env.example .env
```

也可以直接在前端“模型 API 配置”里填写并保存。

## 另一台电脑启动服务

只在另一台电脑本机使用：

```bash
python3 scripts/start_platform.py --host 127.0.0.1 --port 8765
```

允许当前电脑通过局域网访问：

```bash
python3 scripts/start_platform.py --host 0.0.0.0 --port 8765
```

当前电脑打开：

```text
http://另一台电脑IP:8765
```

## 日常更新流程

当前电脑改完程序后：

```bash
git status
git add .
git commit -m "描述本次更新"
git push
```

另一台电脑更新：

```bash
cd answer_book_platform_v1
git pull
```

如果另一台电脑正在运行服务，更新代码后需要重启服务，前端和后端改动才会全部生效。

## 注意

不要把 `.env`、API Key、任务结果、日志和缓存强行加入 Git。

不要把 `8765` 端口直接暴露到公网。跨网络访问建议使用 VPN、Tailscale 或 SSH 隧道。
