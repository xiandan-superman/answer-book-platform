# Gitee 同步说明

平台代码默认同步到 Gitee；桌面 App 发布当前冻结，不属于普通平台推送范围。

## 会进入 Git 的内容

- `app/`、`web/`、`tests/`、`scripts/` 中的平台源码与测试
- 配置示例、依赖清单、版本文件和正式文档
- 受控的示例输入

以下本地状态已由 `.gitignore` 隔离：`.env`、`config/api_keys.json`、`config/providers.local.json`、`tasks/`、`outputs/`、`logs/`、`cache/`、`runtime/`、`practice_jobs/`、`practice_history/`、`build/`、`dist/` 和 `archive/local/`。

## 首次关联 Gitee

```bash
cd <项目目录>
git remote add gitee-material <你的 Gitee 仓库地址>
git fetch gitee-material
git switch codex/v6.8-material-figure-schema-flow
```

如果本地还没有该分支，基于远程分支创建：

```bash
git switch -c codex/v6.8-material-figure-schema-flow --track gitee-material/codex/v6.8-material-figure-schema-flow
```

## 每次推送前

```bash
python3 scripts/check_version_consistency.py
python3 scripts/run_quality_gates.py
git fetch gitee-material
git status --short
git diff --cached --name-only
```

只显式暂存本次平台文件，不使用 `git add .`。确认远程、分支、`VERSION` 和暂存清单后再提交、推送；不得强推或覆盖远程历史。

```bash
git add <本次平台文件...>
git commit -m "描述本次平台更新"
git push gitee-material codex/v6.8-material-figure-schema-flow
```

## 另一台电脑同步

```bash
git clone <你的 Gitee 仓库地址>
cd <仓库目录>
git switch codex/v6.8-material-figure-schema-flow
python3.11 -m pip install -r requirements.txt -c constraints-py311.txt
python3 scripts/check_environment.py
python3 scripts/start_platform.py
```

不要提交真实 API Key、任务结果、日志和缓存。局域网监听只用于可信网络；跨网络访问使用 Tailscale、VPN 或 HTTPS 反向代理。
