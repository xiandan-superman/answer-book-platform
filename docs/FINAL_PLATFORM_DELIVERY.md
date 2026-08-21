# 最终平台交付说明

版本：8.0

## 启动

macOS / Linux：

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python scripts/run_platform.py --host 127.0.0.1 --port 8766
```

Windows：

```bat
py -m venv .venv
.venv\Scripts\activate
python -m pip install -r requirements.txt -r requirements-windows.txt
python scripts\run_platform.py --host 127.0.0.1 --port 8766
```

浏览器打开 <http://127.0.0.1:8766>。

## 首次使用

1. 在顶部导航进入“API 配置”，填写自己的服务商 Key，测试连接后保存。
2. 从素材库上传真题或教材，再进入对应的出题流程。
3. 运行数据默认保存在项目目录。可用 `ANSWER_BOOK_DATA_DIR` 指定独立的可写数据目录。

## 交付包边界

- 包含平台后端、Web 前端、必要配置模板、运行脚本和使用文档。回归测试已在交付前执行，不将测试中间件带入使用包。
- 不包含本机 API Key、`.env`、本地教材/真题、历史任务、模型原始响应、验收中间件和缓存。
- 这是平台源码交付包，不是已签名的 macOS / Windows 桌面安装包。

## 验收状态

- 完整回归测试：250 passed。
- 冷启动烟雾测试：首页、`/api/version` 和 `/api/system/status` 均正常。
- 版本：8.0。
