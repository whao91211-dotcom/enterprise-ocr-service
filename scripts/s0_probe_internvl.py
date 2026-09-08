"""S0 契约冻结：用真实 InternVL(9052) 对真实图片调用，确认实际输出。"""
import base64
import time

import httpx

IMG = r"C:\Users\wuhao\Documents\企业文档处理智能体系统\班级7用数据＿\train\Sample100.png"
BASE = "http://127.0.0.1:9052/v1"

SYSTEM = "你是一个专业的图像文本分析助手"

TASK = """
# 任务描述
你需要对图像中的销售消息进行结构化信息提取，输出一个标准CSV格式的数据。

# 销售内容字段定义
desc：代表接受货物的顾客公司
date：代表发生销售行为的日期
from：代表发送货物的公司
item：代表货物名称
amount：代表货物数量
price：代表货物单价
tax：代表货物税率
sum：代表货物金额

# 输入图像
<image>
"""


def call(model: str, image_path: str = IMG) -> str:
    with open(image_path, "rb") as f:
        b64 = base64.b64encode(f.read()).decode("ascii")
    mime = "image/png" if image_path.lower().endswith(".png") else "image/jpeg"
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": SYSTEM},
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": TASK.strip()},
                    {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}},
                ],
            },
        ],
        "temperature": 0,
        "max_tokens": 1024,
    }
    t0 = time.monotonic()
    try:
        r = httpx.post(f"{BASE}/chat/completions", json=payload, headers={"Authorization": "Bearer DUMMY"}, timeout=180.0)
    except Exception as exc:
        return f"[HTTP ERROR] {exc}"
    d = time.monotonic() - t0
    status = r.status_code
    if status != 200:
        return f"[HTTP {status}] {r.text[:600]}"
    try:
        data = r.json()
    except Exception:
        return f"[PARSE {status}] {r.text[:600]}"
    content = (data.get("choices") or [{}])[0].get("message", {}).get("content", "")
    return f"[OK {d:.1f}s model={model}]\n{content}"


if __name__ == "__main__":
    # 网关 /v1/models 只报 gpt-3.5-turbo，但截图用的是 internvl3，脚本用 HF 全名；都试一遍
    for model in ["internvl3", "gpt-3.5-turbo", "OpenGVLab/InternVL3_5-2B-HF"]:
        print("=" * 70)
        print(call(model))
        print()
