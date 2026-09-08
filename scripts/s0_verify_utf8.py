"""把 InternVL 输出以 UTF-8 落盘核对真实列。"""
import base64

import httpx

IMG = r"C:\Users\wuhao\Documents\企业文档处理智能体系统\班级7用数据＿\train\Sample100.png"
BASE = "http://127.0.0.1:9052/v1"
b64 = base64.b64encode(open(IMG, "rb").read()).decode("ascii")
payload = {
    "model": "internvl3",
    "messages": [
        {"role": "system", "content": "You are a helpful assistant."},
        {"role": "user", "content": [
            {"type": "text", "text": "# 任务描述\n你需要对图像中的销售消息进行结构化信息提取，输出一个标准CSV格式的数据。"},
            {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}},
        ]},
    ],
    "temperature": 0.7, "top_p": 0.8, "max_tokens": 512,
}
r = httpx.post(f"{BASE}/chat/completions", json=payload, headers={"Authorization": "Bearer DUMMY"}, timeout=180)
data = r.json()
content = (data.get("choices") or [{}])[0].get("message", {}).get("content", "")
with open("scripts/_ocr_out.txt", "w", encoding="utf-8") as f:
    f.write(content)
lines = content.strip().splitlines()
print("总行数:", len(lines), " finish_reason:", (data.get("choices") or [{}])[0].get("finish_reason"))
for i, ln in enumerate(lines[:3]):
    print(f"行{i} 列数={len(ln.split(','))}: {ln}")
print("已写 scripts/_ocr_out.txt (UTF-8)")
