"""企业文档处理智能体 Web 启动入口。

用法:
    python rag_app.py            # http://127.0.0.1:8100
环境:
    DEEPSEEK_API_KEY (或项目根 .env)
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import uvicorn  # noqa: E402

from rag.web import app  # noqa: E402

if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8100)
