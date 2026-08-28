# AI 优化变更账本

> 读者：后续修改本项目的 AI 代理。用途：保存不可从单个文件推断出的决策、风险和回归边界。不是用户版更新日志。

## 强制使用规则

1. 改动前完整阅读本文件，确认没有破坏既有 `invariants`。
2. 改动后在“变更记录”最上方追加一条；使用固定字段，不删除或改写历史结论。
3. 只记录实际文件改动。调查未产生改动时不记录。
4. `verification` 只能写实际运行结果；未运行写 `pending`，失败写 `failed`，禁止用计划代替结果。
5. 记录必须简洁，不保存 API Key、提示词、题目、教材内容、用户路径或其他用户数据。

## 固定记录格式

```md
### OPT-YYYYMMDD-NN｜短标题
- status: implementing | verified | reverted
- scope: 涉及的模块或用户流程
- changed: 改了什么
- trigger: 已见错误场景或考虑角度
- invariants: 必须保持的质量、失败率、计费、数据和兼容性边界
- do_not_regress: 后续禁止回退的具体行为
- verification: 实际命令及结果，或 pending
```

## 全局 invariants

- 优先提高最终 Word/PDF/题目质量和任务完成率；不得以降低质量换速度，不得提高任务失败率。
- 用户通过源码 ZIP 更新；macOS 双击 `start_platform.command`，Windows 双击 `启动平台.bat`，启动本地服务并打开网页前端。不要把桌面安装包设为必需品。
- Pydantic 影子观察不得增加模型调用、Token、网络请求、重试、修复、降级或任务阻断。
- Pydantic 从影子模式转为警告或阻断必须取得用户明确确认；即使启用，也只有确定性结构错误可阻断，语义风险继续由现有质量门判断。
- 用户数据位于系统用户数据目录；源码更新可替换代码目录，不得覆盖任务、教材、配置、日志和输出。

## 变更记录（最新在上）

### OPT-20260828-05｜对齐 Chromium 发布验收与现行交互契约

- status: verified
- scope: 源码发布的 Chromium 端到端回归
- changed: 验收用例改为显式点击“恢复上一份”、从任务卡“更多”菜单执行次要操作，配置错误只展示配置入口；下载交互只在浏览器层验证触发，实际 HTTP 附件字节继续由独立服务端回归负责；范围和蓝图草稿分别建立新的恢复决策，不绕过候选固定机制。
- trigger: 旧用例仍假设任务次要按钮直接可见、草稿自动恢复、配置错误可原样重试，并试图用 Playwright 路由拦截观察页面导航下载；这些均与 0.9.32 已确立的安全交互不一致。
- invariants: 不修改产品行为来迎合测试；不恢复会覆盖新输入的自动草稿恢复；不削弱真实附件响应内容的服务端验证。
- do_not_regress: 恢复候选在单次入口内必须保持固定，防止后台自动保存悄然替换用户将要恢复的草稿；配置错误不得提供无意义的盲目重试。
- verification: `ANSWER_BOOK_E2E_URL=http://127.0.0.1:18766 python3 -m pytest -q -m e2e tests/e2e/test_platform_smoke.py` 完整通过，13 passed。

### OPT-20260828-04｜准备 0.9.33 正式源码更新

- status: verified
- scope: 版本元数据、用户变更说明、源码 ZIP 发布候选
- changed: `APP_VERSION`、`VERSION`、发布清单和 `CHANGELOG.md` 同步升级为 0.9.33；发布说明如实覆盖依赖治理、MathJax 精简、Pydantic 影子观察及已在工作树中的生题后处理恢复与定向修复。
- trigger: 用户明确要求推送更新版本；0.9.32 已公开，正式发布必须使用新版本并由 `main` 门禁自动创建标签。
- invariants: 不手工创建或推送标签；源码包只能来自 Git 索引白名单并反向验证；发布说明不得虚构付费模型验证；用户数据不得进入提交或 ZIP。
- do_not_regress: 不得把本地 ZIP、Git push 或 CI 入队表述为公开版本已发布；必须等公开附件和稳定更新源完成核验后再宣布正式发布。
- verification: 最终完整门禁通过（含 1765 passed、16 deselected、69% 分支覆盖率、Ruff、Mypy、公式链路、第三方清单和项目完整度）；0.9.33 版本一致性通过，Chromium 端到端 13 passed；源码 ZIP 含 378 个白名单文件，反向验证 0 问题，解压后用隔离数据目录启动成功，`/api/version` 返回 0.9.33 且首页 HTTP 200。

### OPT-20260828-03｜蓝图、生题输出、绘图规格的 Pydantic 影子观察

- status: verified
- scope: 练习蓝图规范化、练习结果规范化、正式绘图规格生成、环境页质量报告
- changed: 在三个结构边界执行本地 Pydantic 校验；记录脱敏字段路径、错误类型、耗时、新问题模式、人工确认问题和误报计数；页面展示样本数和报告；支持本地人工标记 `confirmed_issue` / `false_positive`。
- trigger: 缺字段或类型漂移若到 Word/绘图阶段才暴露，定位晚且可能浪费前序工作；直接启用硬门禁可能因契约不准误杀正常任务。
- invariants: `enforced=false`、`actual_blocked=false`、新增模型调用/Token/网络请求均为 0；观察、校验或日志写入异常不得逃逸到业务流程；影子期避免失败和避免重试必须如实记 0，不能虚报收益。
- do_not_regress: 不得在 observer 中调用模型、触发重试、修改输入对象、抛出异常或自动升级门禁；不得保存模型输出、题目、提示词或教材正文；语义判断不得迁入 Pydantic。
- verification: 定向回归 69 passed；完整门禁 1765 passed、16 deselected、覆盖率 69%；Ruff、Mypy、公式链路、第三方清单和项目完整度通过；隔离用户数据目录启动源码服务成功，影子报告接口与网页入口均返回 200。

### OPT-20260828-02｜MathJax 源码资源精简

- status: verified
- scope: 网页公式预览、源码 ZIP 体积、离线静态资源
- changed: 仅保留实际使用的 TeX/MathML → CHTML 组合、`boldsymbol` 和 CHTML 字体；删除 SVG、语音、Node 适配和重复组合包；修正异步启动与重复排版。
- trigger: 仓库保存约 21 MB 完整目录但页面只使用少数组件；冗余增加下载、扫描和更新成本，异步处理不当可能导致首次显示异常或重复排版。
- invariants: 不改变公式输入、Word 原生公式和页面公式能力；先确认实际字体完整再删除资源。
- do_not_regress: 不得删除 `web/vendor/mathjax/output/chtml/fonts/woff-v2/` 的在用字体；新增 MathJax 组件时必须同步更新离线资源测试和第三方清单。
- verification: 资源约 3.1 MB；离线资源测试、Chromium 页面验证、模拟源码 ZIP 字体请求通过。

### OPT-20260828-01｜源码运行依赖治理

- status: verified
- scope: Python 依赖、跨平台源码启动、环境诊断、第三方许可证
- changed: NumPy 改为直接依赖；增加 macOS/Windows × Python 3.9/3.11 约束；增加第三方用途/许可证清单；环境页非阻断显示版本漂移。
- trigger: 直接使用的包若只靠间接依赖，替换上游后可能缺失；不同平台传递依赖漂移会造成开发机成功、用户机失败；缺少许可证记录会提高升级风险。
- invariants: 保留源码 ZIP 和双击启动方式；版本漂移只诊断，不阻断可运行环境；不引入无明确质量收益的大型框架。
- do_not_regress: 不得移除 NumPy 直接声明；更新依赖时同步四个平台约束与第三方清单；不得让环境建议版本差异阻止启动。
- verification: 完整门禁 1754 passed、16 deselected、覆盖率 69%；Ruff、Mypy、`pip check`、模拟源码 ZIP 启动通过。
