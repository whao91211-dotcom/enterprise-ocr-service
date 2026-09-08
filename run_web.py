"""Web 启动入口。

用法: python run_web.py   (http://127.0.0.1:8100)
需要 .env: OCR_BASE_URL(9052) / DEEPSEEK_API_KEY
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import uvicorn  # noqa: E402

if __name__ == "__main__":
    uvicorn.run("web.main:app", host="127.0.0.1", port=8100, reload=False)
