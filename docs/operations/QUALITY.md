# 平台工程质量门禁

正式版本除本页质量门禁外，还必须执行 [发布操作手册](RELEASE.md)。不要把“本机某组测试通过”“main 已推送”或“本地 ZIP 已生成”误认为正式源码更新已经发布。

## 日常门禁

```bash
python3 scripts/run_quality_gates.py
```

依次检查 Python 语法、`VERSION` 与清单一致性、全量 pytest、公式链路、第三方包许可证记录和项目完整度。旧的 4000 行单文件自测已归档，功能回归由可定位、可独立运行的 pytest 用例接管。桌面 App 打包脚本属于冻结范围，不在平台门禁中执行。

## 完整门禁

```bash
python3.11 -m pip install -r requirements.txt -r requirements-dev.txt -c constraints-py311.txt
python3 scripts/run_quality_gates.py --full
```

完整门禁增加 Ruff、Mypy 和分支覆盖率。Coverage 在这一模式下直接执行唯一一次全量 pytest，不再先运行普通 pytest 后重复整套测试。现阶段 lint/type-check 先覆盖新拆出的基础模块，后续按模块扩展，避免一次性把历史告警全部静默忽略。整库分支覆盖率门槛为 60%，由 `pyproject.toml` 和完整质量门共同执行；新增测试后只允许逐步上调。

浏览器端端到端测试放在 `tests/e2e/`，默认测试集不启动真实浏览器；验收环境显式设置地址后单独运行。

GitHub CI 保留同一个质量工作流和 `python-quality` 检查名：当前 `APP_VERSION` 已有源码标签时，普通 push 与拉取请求只运行日常 Python 3.11 门禁；当前版本尚无标签时，视为发布候选，运行完整 Python 门禁，并行执行 Chromium 关键流程和 macOS/Windows 源码依赖锁验证。最终汇总任务会核对该提交确实通过与发布状态相符的门禁集合，之后源码发布工作流才可能继续。正式发布仍从生成后的源码 ZIP 使用独立数据目录启动服务；CI 使用固定响应和本地测试材料，不调用真实付费模型。
