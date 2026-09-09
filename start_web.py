"""用 .venv 的 uvicorn 启动服务（绕过任何环境劫持）。

用法: python start_web.py   — 用项目 .venv 的解释器起 uvicorn。
即使外层 shell 的 python 被替换, 这里强制用绝对路径的 .venv。
"""
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
VENV_PY = ROOT / ".venv" / "Scripts" / "python.exe"


def main() -> int:
    venv = VENV_PY if VENV_PY.exists() else sys.executable
    print(f"[start_web] using interpreter: {venv}")
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    proc = subprocess.Popen(
        [str(venv), str(ROOT / "run_web.py")],
        cwd=str(ROOT),
        env=env,
    )
    try:
        proc.wait()
    except KeyboardInterrupt:
        proc.terminate()
    return proc.returncode


if __name__ == "__main__":
    raise SystemExit(main())
