# Delivery Checklist

当前 v1 平台交付检查项：

- [x] 独立项目目录。
- [x] OpenAI / DeepSeek 配置。
- [x] Web 保存本机 OpenAI / DeepSeek API Key，接口只返回脱敏状态。
- [x] Web 顶部显示平台版本。
- [x] `/api/version` 版本接口。
- [x] API Key 本地读取且不进入项目文件。
- [x] 本地 Web 控制台。
- [x] Web 任务列表与任务选择。
- [x] Web 任务执行自动轮询状态。
- [x] Web 任务文件列表与安全下载。
- [x] 任务级交付包导出。
- [x] 命令行入口。
- [x] macOS / Windows 启动入口。
- [x] 依赖安装脚本。
- [x] Windows 专用依赖文件，支持 Word COM 自动化。
- [x] 任务 ID 独立生成。
- [x] 环境检查。
- [x] DOCX 真题抽取。
- [x] 教材索引。
- [x] 教材候选检索。
- [x] 逐题模型结构化生成。
- [x] v4 公式字段校验。
- [x] 正文公式泄漏阻断。
- [x] DOCX 生成。
- [x] DOCX XML 审计。
- [x] Microsoft Word PDF 导出。
- [x] PNG 渲染输出。
- [x] PNG 渲染页质量审计。
- [x] pipeline 状态和验收报告。
- [x] 手工页码映射覆盖。
- [x] Web 页码校准读取与保存。
- [x] Web 审计摘要查看。
- [x] Web 默认执行渲染复核。
- [x] Web 结构化答案读取、编辑、保存与 v4 校验。
- [x] 命令行结构化答案整体验证。
- [x] 答案覆盖率审计：漏题、重复题、未知题号硬失败。
- [x] 逐题复核视图与 CSV 导出。
- [x] 最终验收门禁与 `final_acceptance_report.json`。
- [x] 跨设备迁移说明 `MIGRATION_README.md`。
- [x] 发布包反向验证脚本，检查密钥和历史运行文件是否误打包。
- [x] 发布包内置 `VERSION` 与 `RELEASE_MANIFEST.json`。
- [x] 项目完整度审计脚本，检查关键模块、脚本、Web 入口和文档齐备性。
- [x] 发布前一键质量门禁脚本，串联编译、自测、公式、完整度、打包和 release 反向验证。
- [x] 基础图形重绘链路。
- [x] 专业公式链路：`latex2mathml -> mathml2omml.xsl -> Word OMML`。
- [x] 公式链路环境门禁：正式执行要求 `preferred_chain_ready=true`。
- [x] 公式上下标组合、分式、定界符、求和/积分类结构转换自测。
- [x] 已修复结构化答案复用执行，避免模型覆盖人工/程序修复结果。

当前真实测试：

- Provider：DeepSeek
- Model：deepseek-chat
- Demo task：`demo_物理化学真题_20260702_115923`
- DOCX：`outputs/demo_物理化学真题_20260702_115923/answer_book.docx`
- PDF：`outputs/demo_物理化学真题_20260702_115923/word_rendered/answer_book.pdf`
- PNG：`outputs/demo_物理化学真题_20260702_115923/word_rendered/page-1.png`
- DOCX audit：PASS
- Render audit：PASS

尚需继续增强：

- 真实大卷的复杂题型抽取。
- 页码校准的教材原文对照、批量预览和冲突提示。
- 图形重绘 UI 化。
- 极复杂公式语义修正和人工复核界面。
- 任务队列和失败重试 UI。
- Electron/Tauri 桌面壳。
