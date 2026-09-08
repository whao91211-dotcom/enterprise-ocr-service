import base64, httpx, time
IMG = r"C:\Users\wuhao\Documents\企业文档处理智能体系统\班级7用数据＿\train\Sample100.png"
b64 = base64.b64encode(open(IMG, "rb").read()).decode("ascii")
payload={"model":"internvl3","messages":[{"role":"system","content":"You are a helpful assistant."},
 {"role":"user","content":[
   {"type":"text","text":"# 任务描述\n你需要对图像中的销售消息进行结构化信息提取，输出一个标准CSV格式的数据。"},
   {"type":"image_url","image_url":{"url":f"data:image/png;base64,{b64}"}}]}],
 "temperature":0.7,"top_p":0.8,"max_tokens":512}
t0=time.monotonic()
try:
    r=httpx.post("http://127.0.0.1:9052/v1/chat/completions",json=payload,headers={"Authorization":"Bearer DUMMY"},timeout=600)
    dt=time.monotonic()-t0
    d=r.json(); c=(d.get("choices") or [{}])[0]
    print(f"status={r.status_code} 耗时={dt:.1f}s finish={c.get('finish_reason')} rows={len([x for x in c.get('message',{}).get('content','').strip().splitlines() if x])}")
except Exception as e:
    print(f"EXC after {time.monotonic()-t0:.1f}s: {e!r}")
