# 第三方软件与资源清单

本项目以源码 ZIP 分发。用户首次启动时，启动器在用户数据目录的独立 Python 虚拟环境中安装运行依赖；前端资源随源码 ZIP 一起分发。本文档是用途、版本与许可证索引，各组件自带的许可证文本仍是法律条款的依据。

## 直接运行依赖

| 包 | 源码锁定版本 | 用途 | 许可证 |
| --- | --- | --- | --- |
| `python-docx` | 1.2.0 | Word 读写与文档生成 | MIT |
| `lxml` | 6.1.2 | DOCX/XML、OMML 解析与审计 | BSD-3-Clause |
| `latex2mathml` | 3.78.1 / 3.81.0 | LaTeX 转 MathML | MIT |
| `mathml-to-omml` | 1.0.5 | MathML 转 Word OMML | MIT |
| `Pillow` | 11.3.0 / 12.3.0 | 图片读取、转换与嵌入 | MIT-CMU |
| `pydantic` | 2.13.4 | V4 结构化答案契约校验 | MIT |
| `pypdfium2` | 5.13.0 | PDF 页面渲染与交付审计 | BSD-3-Clause / Apache-2.0；包含 PDFium 相关声明 |
| `bm25s` | 0.3.10 | 教材文本 BM25 召回 | Apache-2.0 |
| `huey` | 3.3.4 | SQLite 持久化出题队列 | MIT |
| `matplotlib` | 3.9.4 / 3.11.1 | 科学绘图 | Matplotlib License（PSF 风格） |
| `numpy` | 2.0.2 / 2.4.6 | 绘图代码直接运行能力与 BM25 数组后端 | BSD-3-Clause 为主，发行包含组件附加声明 |
| `pyparsing` | 3.1.4 / 3.3.2 | Matplotlib 解析依赖；Python 3.9 兼容锁定 | MIT |
| `pywebview` | 6.2.1 | macOS/Windows 源码启动器轻量窗口 | BSD-3-Clause |
| `pystray` | 0.19.5 | macOS 菜单栏与 Windows 系统托盘 | LGPL-3.0 |
| `pywin32` | 312 | Windows COM 与原生能力 | PSF-2.0 为主；以其发行包内许可证集合为准 |

## 锁定的间接运行依赖

| 包 | 用途来源 | 许可证 |
| --- | --- | --- |
| `annotated-types`, `pydantic-core`, `typing-inspection` | Pydantic | MIT |
| `contourpy`, `cycler`, `fonttools`, `kiwisolver` | Matplotlib | BSD-3-Clause / MIT，以各发行包为准 |
| `importlib-resources`, `zipp` | Python 3.9 兼容支持 | Apache-2.0 / MIT |
| `packaging`, `python-dateutil`, `six`, `typing-extensions` | 通用运行支持 | Apache-2.0 / BSD-3-Clause / MIT / PSF-2.0，以各发行包为准 |
| `bottle`, `proxy-tools` | pywebview | MIT |
| `pyobjc-core`, `pyobjc-framework-Cocoa`, `pyobjc-framework-Quartz`, `pyobjc-framework-WebKit`, `pyobjc-framework-security`, `pyobjc-framework-UniformTypeIdentifiers` | macOS 启动壳 | MIT |
| `pythonnet`, `clr-loader`, `cffi`, `pycparser` | Windows pywebview 运行时 | MIT / BSD-3-Clause，以各发行包为准 |

精确版本见 `constraints-py39.txt`、`constraints-py311.txt` 和对应的 `constraints-source-macos-*`、`constraints-source-windows-*` 文件。Python 3.10 保持 `requirements.txt` 声明的有界兼容路径，不宣称精确复现。

## 随源码分发的前端组件

| 组件 | 仓库版本 | 用途 | 许可证/条款 |
| --- | --- | --- | --- |
| MathJax | 3.2.2 | 离线 TeX/MathML → CHTML 公式预览；仅内置使用中的组合组件与动态输入扩展 | Apache-2.0；完整文本见 `web/vendor/mathjax/LICENSE` |
| Lucide | 1.8.0 | 前端图标 | ISC |
| GSAP | 3.15.0 | 页面和任务动效 | GSAP Standard License；见分发文件头部声明和 <https://gsap.com/standard-license/> |

## 开发与验证工具

`pytest`、`coverage`、`ruff`、`mypy` 和 `playwright` 只用于源码质量门禁；`pyinstaller` 仅属于保留的可选桌面包工作流。普通用户双击源码启动器时不会安装这些开发/打包工具。

## 维护规则

- 新增或删除运行依赖、平台锁定包或 vendored 前端组件时，必须同步更新本文档。
- 发布前必须保留 vendored 文件自带的版权头与许可证文本。
- 许可证名称只是索引；如需重新分发、修改或商业授权判断，应复核对应版本发行包中的完整条款。
