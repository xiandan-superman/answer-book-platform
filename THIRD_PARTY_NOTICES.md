# 第三方软件与资源清单

本项目以源码 ZIP 分发。用户首次启动时，启动器在用户数据目录的独立 Python 虚拟环境中安装运行依赖；前端资源随源码 ZIP 一起分发。本文档是用途、版本与许可证索引，各组件自带的许可证文本仍是法律条款的依据。

## 直接运行依赖

| 包 | 源码锁定版本 | 用途 | 许可证 |
| --- | --- | --- | --- |
| `python-docx` | 1.2.0 | Word 读写与文档生成 | MIT |
| `lxml` | 6.1.2 | DOCX/XML、OMML 解析与审计 | BSD-3-Clause |
| `latex2mathml` | 3.81.0 | LaTeX 转 MathML | MIT |
| `Pillow` | 12.3.0 | 图片读取、转换与嵌入 | MIT-CMU |
| `pydantic` | 2.13.4 | 请求级 JSON Schema 与结构化输出校验 | MIT |
| `litellm` | 1.99.0 | 灵算模型的库模式影子流量与用量对照 | MIT |
| `sympy` | 1.14.0 | 本地符号计算与化简 | BSD-3-Clause |
| `latex2sympy2-extended` | 1.11.0 | LaTeX 到 SymPy 表达式转换 | MIT |
| `math-verify` | 0.9.0 | 数学答案解析与等价性判定 | Apache-2.0 |
| `pypdfium2` | 5.13.0 | PDF 页面渲染与交付审计 | BSD-3-Clause / Apache-2.0；包含 PDFium 相关声明 |
| `bm25s` | 0.3.10 | 教材文本 BM25 召回 | Apache-2.0 |
| `huey` | 3.3.4 | SQLite 持久化出题队列 | MIT |
| `matplotlib` | 3.11.1 | 科学绘图 | Matplotlib License（PSF 风格） |
| `numpy` | 2.4.6 | 绘图代码直接运行能力与 BM25 数组后端 | BSD-3-Clause 为主，发行包含组件附加声明 |
| `pyparsing` | 3.3.2 | Matplotlib 解析依赖 | MIT |
| `pywebview` | 6.2.1 | macOS/Windows 源码启动器轻量窗口 | BSD-3-Clause |
| `pystray` | 0.19.5 | macOS 菜单栏与 Windows 系统托盘 | LGPL-3.0 |
| `pywin32` | 312 | Windows COM 与原生能力 | PSF-2.0 为主；以其发行包内许可证集合为准 |

## 锁定的间接运行依赖

| 包 | 用途来源 | 许可证 |
| --- | --- | --- |
| `annotated-types`, `pydantic-core`, `typing-inspection` | Pydantic | MIT |
| `contourpy`, `cycler`, `fonttools`, `kiwisolver` | Matplotlib | BSD-3-Clause / MIT，以各发行包为准 |
| `packaging`, `python-dateutil`, `six`, `typing-extensions` | 通用运行支持 | Apache-2.0 / BSD-3-Clause / MIT / PSF-2.0，以各发行包为准 |
| `bottle`, `proxy-tools` | pywebview | MIT |
| `pyobjc-core`, `pyobjc-framework-Cocoa`, `pyobjc-framework-Quartz`, `pyobjc-framework-WebKit`, `pyobjc-framework-security`, `pyobjc-framework-UniformTypeIdentifiers` | macOS 启动壳 | MIT |
| `pythonnet`, `clr-loader`, `cffi`, `pycparser` | Windows pywebview 运行时 | MIT / BSD-3-Clause，以各发行包为准 |
| `aiohappyeyeballs`, `aiohttp`, `aiosignal`, `anyio`, `attrs`, `boto3`, `botocore`, `certifi`, `charset-normalizer`, `click`, `filelock`, `frozenlist`, `fsspec`, `h11`, `hf-xet`, `httpcore`, `httpx`, `huggingface-hub`, `idna`, `importlib-metadata`, `jinja2`, `jmespath`, `jsonschema`, `jsonschema-specifications`, `markupsafe`, `multidict`, `propcache`, `pyyaml`, `referencing`, `regex`, `requests`, `rpds-py`, `s3transfer`, `sniffio`, `tiktoken`, `tokenizers`, `tqdm`, `urllib3`, `yarl`, `zipp` | LiteLLM 及其传输、模型注册表和用量依赖 | Apache-2.0 / BSD / MIT，以各发行包为准 |
| `openai`, `jiter`, `distro`, `pydantic-settings`, `python-dotenv` | LiteLLM 模型协议与配置依赖 | Apache-2.0 / MIT，以各发行包为准 |
| `antlr4-python3-runtime`, `fastuuid`, `librt`, `mpmath` | LiteLLM、Math-Verify 与 SymPy | BSD / MIT，以各发行包为准 |

精确版本见 `constraints-py311.txt` 和对应的 `constraints-source-macos-py311.txt`、`constraints-source-windows-py311.txt` 文件。Python 3.10 及以下不再受支持。

## 随源码分发的前端组件

| 组件 | 仓库版本 | 用途 | 许可证/条款 |
| --- | --- | --- | --- |
| MathJax | 3.2.2 | 离线 TeX/MathML → CHTML 公式预览；仅内置使用中的组合组件与动态输入扩展 | Apache-2.0；完整文本见 `web/vendor/mathjax/LICENSE` |
| Lucide | 1.8.0 | 前端图标 | ISC |
| GSAP | 3.15.0 | 页面和任务动效 | GSAP Standard License；见分发文件头部声明和 <https://gsap.com/standard-license/> |

## 按需下载的文档运行时

| 组件 | 固定版本 | 用途 | 许可证 |
| --- | --- | --- | --- |
| `opendatalab/MinerU` / `mineru` | 3.4.5 | PDF/DOCX 主解析、OCR、公式/表格/图片块生成 | MinerU Open Source License（基于 Apache-2.0 并含附加条件）；见 <https://github.com/opendatalab/MinerU> |

OfficeCLI 已退出生产生成路径。MinerU 不进入 Web 主进程依赖，而是按 `requirements-mineru.txt` 在用户数据目录创建固定版本的隔离环境。MinerU 与 Pandoc 均不修改用户 PATH、shell 配置和 Agent skills。

## 开发与验证工具

`pytest`、`coverage`、`ruff`、`mypy` 和 `playwright` 只用于源码质量门禁；`pyinstaller` 仅属于保留的可选桌面包工作流。普通用户双击源码启动器时不会安装这些开发/打包工具。

## 维护规则

- 新增或删除运行依赖、平台锁定包或 vendored 前端组件时，必须同步更新本文档。
- 发布前必须保留 vendored 文件自带的版权头与许可证文本。
- 许可证名称只是索引；如需重新分发、修改或商业授权判断，应复核对应版本发行包中的完整条款。

## 可选 Word C 引擎

Pandoc 3.11（GPL-2.0-or-later，含 texmath）作为独立子进程生成 OMML，python-docx 保留平台模板。官方源码和许可证：https://github.com/jgm/pandoc/tree/3.11 。C 为唯一生产引擎；应用按固定版本准备官方便携包并保留含许可证的完整压缩包，校验压缩包及可执行文件哈希和版本，不增加 Python requirements/constraints。准入运行时为 Windows x64、macOS arm64/x64，以及用于 CI 的 Linux x64；其他架构明确失败，不回退 A/B。
