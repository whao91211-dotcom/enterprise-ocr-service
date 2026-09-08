# 企业文档处理智能体 v2（Agent 架构重构版）

基于 LangChain Agent（DeepSeek LLM）的企业文档处理智能体。
**以 Agent 为中心**装配 4 组可插拔 Tool，而非多个独立服务。

## 架构

```
用户(Web 单页面)
   │ 上传图片 / 文字问答 / 改数据 / 看图
   ▼
LangChain Agent (DeepSeek, function-calling 决策调哪个 Tool)
   │
   ├─ Tool1 OCR      ocr_recognize      InternVL(9052) 识别支票/销售图 → SQLite(待确认)
   ├─ Tool2 Correct  correct_list/show/update/confirm   界面+Agent 人工修正识别结果(模型~80%)
   ├─ Tool3 RAG      rag_query/rag_summarize   已确认数据检索/统计(SQLite)
   └─ Tool4 Plot     plot_chart         统计图(matplotlib → charts/*.png)
   │
   ▼
SQLite (data/agent.db): documents(图) + ocr_rows(识别行, status=pending/confirmed)
```

**为什么 4 个 Tool**（都是补 DeepSeek/基础模型的短板）：
1. DeepSeek 是"盲"的，看不了图 → OCR Tool（InternVL 微调）
2. OCR 只有 ~80% 准确率，Agent 需要准确数据 → Correct Tool（人工修改）
3. 多张图全喂 DeepSeek 费 token 且统计差 → RAG Tool（SQLite 检索/聚合）
4. 统计要可视化 → Plot Tool（画图）

## 运行

```powershell
# 1. 依赖(清华源)
pip install -i https://pypi.tuna.tsinghua.edu.cn/simple langchain langchain-deepseek \
    langchain-classic fastapi uvicorn python-multipart httpx matplotlib

# 2. .env: OCR_BASE_URL=http://127.0.0.1:9052/v1, OCR_MODEL=internvl3,
#          DEEPSEEK_API_KEY=sk-xxx

# 3. 启动 InternVL 9052(隧道), 然后:
python run_web.py        # http://127.0.0.1:8100
```

## 目录

```
config.py             .env 配置(OCR 9052 / DeepSeek)
db/                   SQLite(database/schema/crud)
  schema.py           documents + ocr_rows 两级表
  crud.py             插入/查询/确认/修改/聚合
tools/
  ocr_client.py       InternVL 客户端(cols8 中英语义 prompt, S1 实测)
  ocr_tool.py         Tool1
  correct_tool.py     Tool2 (list/show/update/confirm)
  rag_tool.py         Tool3 (query/summarize)
  plot_tool.py        Tool4 (matplotlib, CJK 字体)
agent/
  llm.py              ChatDeepSeek
  agent.py            AgentExecutor + 8 tools
web/main.py + run_web.py   单页面(上传/识别/修改/问答/图表)
tests/                db+tools 单元测试
```

## 测试

```powershell
pytest tests -q
```

> 前置架构记录(git): 分支 feat/agent-v2 从 master 分叉, 旧版(双服务 OCR+rag)在 master 保留。
