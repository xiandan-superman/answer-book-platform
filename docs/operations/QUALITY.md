# 平台工程质量门禁

正式版本除本页质量门禁外，还必须执行 [发布操作手册](RELEASE.md)。不要把“本机某组测试通过”“main 已推送”或“本地 ZIP 已生成”误认为正式源码更新已经发布。

## 日常门禁

```bash
python3 scripts/run_quality_gates.py
```

依次检查 Python 语法、`VERSION` 与清单一致性、全量 pytest、公式链路和项目完整度。旧的 4000 行单文件自测已归档，功能回归由可定位、可独立运行的 pytest 用例接管。桌面 App 打包脚本属于冻结范围，不在平台门禁中执行。

## 完整门禁

```bash
python3 -m pip install -r requirements.txt -r requirements-dev.txt -c constraints-py39.txt   # Python 3.9
# Python 3.11+ 改用 constraints-py311.txt
python3 scripts/run_quality_gates.py --full
```

完整门禁增加 Ruff、Mypy 和分支覆盖率。现阶段 lint/type-check 先覆盖新拆出的基础模块，后续按模块扩展，避免一次性把历史告警全部静默忽略。整库分支覆盖率门槛为 60%，由 `pyproject.toml` 和完整质量门共同执行；新增测试后只允许逐步上调。

浏览器端端到端测试放在 `tests/e2e/`，默认测试集不启动真实浏览器；验收环境显式设置地址后单独运行。

GitHub CI 对 Python 3.9 和 3.11 运行平台门禁，并在 Chromium 中执行关键用户流程。正式源码标签还会再次执行完整门禁，并从生成后的源码 ZIP 使用独立数据目录启动服务；CI 使用固定响应和本地测试材料，不调用真实付费模型。
