"""用完整字段定义指令测是否输出 8 列契约。"""
import base64

import httpx

IMG = r"C:\Users\wuhao\Documents\企业文档处理智能体系统\班级7用数据＿\train\Sample100.png"
BASE = "http://127.0.0.1:9052/v1"
b64 = base64.b64encode(open(IMG, "rb").read()).decode("ascii")

SYSTEM = "你是一个专业的图像文本分析助手"
TASK = """# 任务描述
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
payload = {
    "model": "internvl3",
    "messages": [
        {"role": "system", "content": SYSTEM},
        {"role": "user", "content": [
            {"type": "text", "text": TASK.strip()},
            {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}},
        ]},
    ],
    "temperature": 0.7, "top_p": 0.8, "max_tokens": 1024,
}
r = httpx.post(f"{BASE}/chat/completions", json=payload, headers={"Authorization": "Bearer DUMMY"}, timeout=180)
data = r.json()
content = (data.get("choices") or [{}])[0].get("message", {}).get("content", "")
with open("scripts/_ocr_out8.txt", "w", encoding="utf-8") as f:
    f.write(content)
print("status", r.status_code, "finish_reason", (data.get("choices") or [{}])[0].get("finish_reason"))
for i, ln in enumerate(content.strip().splitlines()[:3]):
    print(f"row{i} cols={len(ln.split(','))} :: {ln}")
