"""S1 探测：InternVL 对真实销售图到底输出几列、什么顺序。

对比两种指令：
  A) baseline 旧短 prompt（不指定列）
  B) 8 列引导（明确指出列顺序 desc,date,from,item,amount,price,tax,sum）

输出：原始 content + 每行按逗号切分的列数 + 行内容，供判定解析列映射。
结果写入 --out 指定的 UTF-8 文件（默认 scripts/probe_out.txt）。
"""
import argparse
import base64

import httpx

IMG = r"C:\Users\wuhao\Documents\企业文档处理智能体系统\班级7用数据＿\train\Sample100.png"
BASE = "http://127.0.0.1:9052/v1"

SHORT = "# 任务描述\n你需要对图像中的销售消息进行结构化信息提取，输出一个标准CSV格式的数据。"
EIGHT_COLS = (
    "# 任务描述\n你需要对图像中的销售消息进行结构化信息提取，输出一个标准CSV格式的数据。"
    "\n每行必须且只能包含 8 列，顺序固定为：desc,date,from,item,amount,price,tax,sum。"
)
TRAIN_FULL = (
    "\n# 任务描述\n你需要对图像中的销售消息进行结构化信息提取，输出一个标准CSV格式的数据。"
    "\n# 销售内容字段定义"
    "\ndesc：代表接受货物的顾客公司"
    "\ndate：代表发生销售行为的日期"
    "\nfrom：代表发送货物的公司"
    "\nitem：代表货物名称"
    "\namount：代表货物数量"
    "\nprice：代表货物单价"
    "\ntax：代表货物税率"
    "\nsum：代表货物金额"
    "\n# 输入图像"
)

VARIANTS = [
    ("A_baseline_short", SHORT, "You are a helpful assistant."),
    ("B_eight_cols", EIGHT_COLS, "You are a helpful assistant."),
    ("C_train_full", TRAIN_FULL, "你是一个专业的图像文本分析助手"),
]


def call(text: str, system: str) -> tuple[int, str, str]:
    b64 = base64.b64encode(open(IMG, "rb").read()).decode("ascii")
    payload = {
        "model": "internvl3",
        "messages": [
            {"role": "system", "content": system},
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": text},
                    {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}},
                ],
            },
        ],
        "temperature": 0.7,
        "top_p": 0.8,
        "max_tokens": 1024,
    }
    r = httpx.post(
        f"{BASE}/chat/completions", json=payload, headers={"Authorization": "Bearer DUMMY"}, timeout=180
    )
    if r.status_code != 200:
        return r.status_code, "", r.text[:300]
    data = r.json()
    model = data.get("model", "")
    content = ((data.get("choices") or [{}])[0].get("message") or {}).get("content", "")
    return r.status_code, model, content


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("variant", nargs="?", default="all")
    parser.add_argument("--out", default="scripts/probe_out.txt")
    args = parser.parse_args()

    buf: list[str] = []
    for name, prompt, system in VARIANTS:
        if args.variant != "all" and args.variant != name:
            continue
        buf.append("=" * 70)
        buf.append(f"[{name}]")
        status, model, content = call(prompt, system)
        buf.append(f"STATUS {status} MODEL {model}")
        if status != 200:
            buf.append(f"ERROR {content}")
            continue
        buf.append("---- RAW CONTENT ----")
        buf.append(content)
        buf.append("---- COLUMN COUNT PER LINE ----")
        for ln in content.splitlines():
            if ln.strip():
                buf.append(f"  cols={len(ln.split(','))}: {ln}")
    with open(args.out, "w", encoding="utf-8") as f:
        f.write("\n".join(buf))
    print(f"written -> {args.out}")


if __name__ == "__main__":
    main()
