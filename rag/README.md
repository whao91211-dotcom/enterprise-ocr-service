# 企业文档处理智能体框架（rag/ 子项目）

基于 InternVL3 微调模型（OCR）+ DeepSeek 大模型的多 Tool 企业文档处理智能体。
**OCR 识别（已完成的服务）作为 Tool 被智能体调用**，配合 RAG 检索回答用户问题。

## 架构

```
用户提问(Chatbot 网页 / API)
        │
        ▼
  [智能体中控 rag/agent.py]
    ChatDeepSeek(deepseek-chat) + function-calling 自动路由
        │
        ├─ Tool: ocr_recognize → 调用 OCR 服务(8000) 识别销售单据 → all_sales.csv(待确认)
        ├─ Tool: rag_query     → BM25 检索已确认销售数据 → 命中行(带来源)
        └─ Tool: rag_rebuild   → 刷新检索索引
```

- 意图识别由 LLM 的 tool-calling 完成：用户要"识别图片"→ OCR Tool；
  "查询数据/问销量"→ RAG Tool；可扩展新 Tool 注册进 `TOOL_REGISTRY`。
- 数据流：OCR 识别写入 `output/all_sales.csv`（状态=待确认）→ 人工确认(Excel/API)
  → `rag_rebuild` 纳入检索 → 用户提问可命中。
- 检索用 BM25（纯 Python，中日英混合二元组分词），数据量小时足够；
  后续可平滑替换为向量检索（ChromaDB + bge）。

## 目录

```
rag/
  bm25.py         纯标准库 BM25（零第三方依赖）
  data_source.py  读 all_sales.csv 已确认行 → 文本化
  retriever.py    索引加载 + 查询 + 格式化命中
  ocr_tool.py     OCR Tool（调用 8000 识别服务）
  deepseek.py     纯 urllib DeepSeek 客户端（备用，agent.py 用 langchain 版）
  agent.py        智能体中控（ChatDeepSeek + 3 Tool + 会话循环）
  web.py          FastAPI 网页问答入口
rag_app.py        Web 启动入口
tests/test_rag.py 测试(与 OCR 服务测试合并共 43 passed)
```

## 快速开始

```powershell
# 1. 依赖（已装 langchain-deepseek/langgraph）
pip install -i https://pypi.tuna.tsinghua.edu.cn/simple langchain-deepseek langgraph

# 2. .env 填 DeepSeek Key
#    DEEPSEEK_API_KEY=sk-xxx   (platform.deepseek.com 申请)

# 3. 启动 OCR 服务(8000) —— 另开终端
uvicorn app.main:app --host 127.0.0.1 --port 8000

# 4. 启动智能体网页(8100)
python rag_app.py        # http://127.0.0.1:8100
```

## 使用示例（网页/API）

- `POST http://127.0.0.1:8100/api/chat` body: `{"message": "帮我查SETソフトウェア2024年买了什么空気清浄機"}` → 智能体自动调 rag_query → 返回答案+来源
- 提问"识别图片 C:\xxx\Sample100.png" → 自动调 ocr_recognize
- `POST /api/rebuild` 新确认数据后重建索引；`GET /api/stats` 状态

## 依赖说明

| 用途 | 库 | 备注 |
|---|---|---|
| DeepSeek 对话 | langchain-deepseek | ChatDeepSeek 官方接入 |
| 中控框架 | langgraph(可选) | 已装，后续可包状态图 |
| 检索 | 纯标准库 BM25 | 无需 embedding 模型 |

> 安装注意：本机默认 PyPI 直连不通，用清华源 `-i https://pypi.tuna.tsinghua.edu.cn/simple`。
