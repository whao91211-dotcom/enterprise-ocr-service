# 基于 InternVL3 微调模型的企业文档处理多智能体框架 —— 项目方案

> 四人团队课程项目 · 基本目标：单据类文档智能处理+问答 Chatbot
> 拓展目标：多文档类目 + 意图识别分配任务

## 一、系统总架构

```
                    ┌──────────────────────────────────────┐
                    │        Chatbot 前端(网页 / API)        │
                    └──────────────────┬───────────────────┘
                                       │
                    ┌──────────────────▼───────────────────┐
                    │   智能体中控 rag/agent.py              │
                    │   ChatDeepSeek(deepseek-chat)          │
                    │   意图识别 → function-calling 路由     │
                    └───┬──────────────┬──────────────┬─────┘
                        │              │              │
        ┌───────────────▼───┐  ┌──────▼──────┐  ┌────▼──────────┐
        │ Tool1: OCR 识别    │  │ Tool2: RAG  │  │ Tool3: 索引刷新│
        │ (InternVL3 9051)  │  │ 查询已确认   │  │ (rag_rebuild) │
        └───────┬───────────┘  │ 数据(BM25)  │  └───────────────┘
                │              └──────┬──────┘
   ┌────────────▼─────────────┐  ┌───▼───────────────┐
   │ OCR 服务(app, 8000)      │  │ all_sales.csv      │
   │ InternVL3 → 8列CSV       │  │ 已确认=人工核对终稿  │
   └──────────────────────────┘  └────────────────────┘
```

## 二、模块与四人分工建议

| # | 模块 | 技术 | 说明 | 分工 |
|---|------|------|------|------|
| 1 | **OCR 识别** | InternVL3 微调 + FastAPI | 图 → 8 列(desc,date,from,item,amount,price,tax,sum)，已在 8000 部署 | 成员A(本机已完成) |
| 2 | **人工确认** | CSV 状态列 | 识别结果 待确认→人工Excel/API核对→已确认；防重识别覆盖 | 成员B |
| 3 | **数据入库/检索** | BM25(纯Py) / 可扩向量 | 已确认行 → 索引 → rag_query 检索 | 成员C |
| 4 | **智能体中控** | langchain-deepseek ChatDeepSeek + function-calling | 意图识别 → Tool 路由 → 工具结果回填 → 回答(带来源) | 成员D |
| 5 | **前端 Chatbot** | FastAPI + 单页 | 网页/API 问答入口 | 成员D |
| 6 | **模型服务** | llama.cpp/InternVL 9051 | OCR 底层模型，服务器运行 | 成员A/运维 |

可插拔扩展：新增文档类目 = 新增「识别模板 + 数据表 + Tool」，在 `TOOL_REGISTRY` 注册；
意图分类关键词在 `rag/intent.py` 扩展。

## 三、数据流(全链路)

```
图片上传 → InternVL3 识别 → all_sales.csv(待确认)
        → 人工核对(Excel/API) → 状态=已确认
        → rag_rebuild 重建索引
        → 用户提问 "某公司2024买了什么空调?"
        → 意图识别=rag_query → BM25 检索命中行
        → DeepSeek 依据命中数据生成回答(附来源图片)
```

## 四、当前进度(可演示)

- ✅ OCR 识别 → 8列 CSV(真实 Sample100/Sample150/Sample751 已识别, 库内 37 行)
- ✅ 人工确认状态机(confirm 接口 + CSV 状态列 + 防覆盖)
- ✅ RAG 检索(BM25, 27 条已确认文档, /api/rebuild + /api/stats 可查)
- ✅ 意图识别路由(demo_intent.py 无 Key 演示: 5 类问题全对)
- ✅ 智能体中控(3 Tool, ChatDeepSeek function-calling, StructuredTool.invoke)
- ✅ 端到端全链路真实验证:
  识别 Sample150 → 3 商品正确 → 入库待确认 → confirm 已确认
  → rebuild(docs 14→27) → "テレビ单价?" 精确回答(14274.00/7台/菱洋電子貿易) + 溯源
- ✅ 流式 SSE 对话: /api/chat/stream 实时展示 意图→工具调用→结果→回答
- ✅ 网页 Chatbot: http://127.0.0.1:8100 (多轮记忆正常)
- ⬜ 拓展类目(如发票/合同模板 + 各自 Tool)

## 五、运行

```powershell
# 1. OCR 服务(另开终端)
uvicorn app.main:app --host 127.0.0.1 --port 8000
# 2. 模型 9051 隧道保持在线
# 3. 智能体网页
python rag_app.py            # http://127.0.0.1:8100
# 4. .env 配置 DEEPSEEK_API_KEY=sk-xxx
```

依赖：langchain-deepseek、langgraph(可选)；安装用清华源
`-i https://pypi.tuna.tsinghua.edu.cn/simple`。

## 六、关键技术结论(汇报素材)

- InternVL3 输出契约=训练 8 列；prompt 用"中英语义引导"而非纯英文 key(否则 from 列错认日期)
- 识别数据必须人工确认后才进 RAG(模型有幻觉/重复行)
- 单槽 llama.cpp 一次一个请求，长 prompt 会卡服务
- DeepSeek 接入用官方 langchain 包 ChatDeepSeek 最稳；本机 PyPI 直连不通需清华源
