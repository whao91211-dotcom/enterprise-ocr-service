# 企业文档处理智能体 — 技术方案文档

> 版本：v2（Agent 架构） · 分支：`feat/agent-v2` · 状态：开发中（OCR 真实联调待 9052）
> 面向对象：团队同事 / 评审

---

## 1. 项目目标

构建一个 **企业单据处理智能体（Chatbot）**，覆盖"识别 → 人工校验 → 入库 → 统计问答 → 可视化"完整链路：

- 用户上传**支票/销售单据图片**，系统自动结构化识别
- 识别结果**人工可改**（模型准确率 ~80%，Agent 需要 100% 准确数据）
- 多张图数据进库，供**高效检索与统计**（避免全量喂给大模型烧 token、效果差）
- 统计结果可**绘制图表**
- 后续能力**可插拔扩展**

### 1.1 目标用户链路

```
上传图片 → OCR识别 → 人工核对/修改 → 确认入库 → 提问统计 → 图表展示
```

---

## 2. 总体架构

**核心思想：以 Agent 为中心（而非多个独立服务）。** 用 LangChain 搭建 DeepSeek LLM 智能体，
为其"加装"一组 Tool，每个 Tool 补齐 DeepSeek / 基础模型的一项短板。

```
                          ┌──────────────────────────────┐
                          │      Web 单页面界面 (FastAPI)    │
                          │  上传 / 表格修改 / 问答 / 图表     │
                          └──────────────┬───────────────┘
                                         │
                          ┌──────────────▼───────────────┐
                          │   LangChain Agent (DeepSeek)  │
                          │   function-calling 自动决策    │
                          └───┬─────────┬─────────┬───────┘
                              │         │         │
        ┌─────────────────────▼──┐  ┌───▼─────┐  ┌▼──────────────┐
        │ Tool1 OCR (InternVL)   │  │Tool2     │  │Tool3 RAG /    │
        │ 9052 / internvl3       │  │Correct   │  │Tool4 Plot     │
        └───────────┬────────────┘  └───┬─────┘  └───────────────┘
                    │                    │
              ┌─────▼─────────────────────▼─────┐
              │   SQLite: data/agent.db          │
              │   documents(图) + ocr_rows(行)    │
              │   状态: pending / confirmed       │
              └───────────────────────────────────┘
```

### 2.1 为什么是 4 个 Tool（每个补一个短板）

| Tool | 短板 | 解决方案 | 关键技术 |
|---|---|---|---|
| **OCR** | DeepSeek 是"盲"的，无法看图 | InternVL 微调模型识别图片 | InternVL3 @ 9052（OpenAI 兼容接口）|
| **Correct** | OCR 准确率仅 ~80%，Agent 需要准确数据 | 界面上人工核对/修改识别结果 | SQLite 行级状态机 + Web 表格编辑 |
| **RAG** | 多图全量喂 DeepSeek 费 token 且统计差 | 数据入库，按需检索/聚合 | SQLite 过滤查询 + 统计聚合 |
| **Plot** | 统计数字不直观 | 绘制统计图 | matplotlib → PNG |

后续需要新能力（发票类目、合同、导出等）时，在 Agent 中**注册新 Tool 即可扩展**。

---

## 3. 模块设计

### 3.1 目录结构

```
enterprise_ocr_service/
├── config.py               # .env 加载(OCR 端点 / DeepSeek key / 模型参数)
├── agent/
│   ├── llm.py              # DeepSeek LLM 封装(langchain-deepseek ChatDeepSeek)
│   └── agent.py            # AgentExecutor + 8 个工具注册 + 对话编排
├── tools/                  # 每个工具独立模块(可插拔)
│   ├── ocr_client.py       # InternVL 客户端: 9052 调用 + CSV 解析(纯 httpx)
│   ├── ocr_tool.py         # Tool1: 识别图片 → 入库待确认
│   ├── correct_tool.py     # Tool2: 列单据/看行/改行/确认
│   ├── rag_tool.py         # Tool3: 检索(rag_query) + 聚合(rag_summarize)
│   └── plot_tool.py        # Tool4: 柱状/饼图 → charts/*.png
├── db/
│   ├── database.py         # SQLite 连接/初始化(标准库 sqlite3, WAL)
│   ├── schema.py           # 建表 DDL
│   └── crud.py             # 数据操作(用户友好键 desc/date/from/sum)
├── web/
│   └── main.py             # FastAPI 单页(上传/修改/问答/图表)
├── run_web.py              # 启动入口 (127.0.0.1:8100)
├── data/                   # agent.db + 上传图片(本地, gitignore)
├── charts/                 # Plot tool 输出 PNG
└── tests/test_agent_v2.py  # db + tools 单元测试
```

### 3.2 数据模型（SQLite）

**documents**（每张上传图片）：

| 列 | 说明 |
|---|---|
| id | 主键 |
| file_name | 源文件名（唯一，作为图级标识）|
| sha256 | 可选，用于去重 |
| uploaded_at | 上传时间 |
| status | `recognition_pending` / `rows_inserted` |

**ocr_rows**（每张图识别出的行数据）：

| 列 | 说明 |
|---|---|
| id | 主键 |
| doc_id | → documents.id（外键，级联删除）|
| seq | 行号 |
| desc / date / from / item / amount / price / tax / sum | 8 列识别字段 |
| status | **pending（待确认）/ confirmed（已确认）** |
| modified_by / modified_at | 人工修改留痕 |

> 关键设计：**只有 confirmed 的行才进入 RAG / 统计**。Agent 回答时若数据未确认，
> 会如实说明是"待确认草稿"。人工确认前，识别错误不会污染统计结果。

### 3.3 Tool1 — OCR（InternVL）

- **模型端点**：`http://127.0.0.1:9052/v1/chat/completions`，`model=internvl3`
  （`/v1/models` 只显示 `gpt-3.5-turbo`，实际必须用 `internvl3`）
- **Prompt**（S1 真机实测固化）：使用**中英语义引导**而非纯英文 key ——
  纯英文 `from` 会被模型错认成日期（实测输出幻觉日期）；
  中英混写 `from(发货公司/源公司)` 后识别正确。
- **输出**：8 列 `desc,date,from,item,amount,price,tax,sum`（与训练标注一致）
- 识别结果直接写入 SQLite，状态 = `pending`；同图重复识别会先删旧行再插入（覆盖语义）

### 3.4 Tool2 — Correct（人工修改）

提供 4 个子工具，Web 表格编辑与 Agent 共用同一数据层：

- `correct_list_docs` 列出所有单据及确认进度
- `correct_show_rows(file_name)` 查看某图识别行（含行 id 与状态）
- `correct_update_row(row_id, fields)` 修改单行字段（白名单校验）
- `correct_confirm(file_name, reviewer)` 确认整图全部行（进入统计口径）

### 3.5 Tool3 — RAG（检索与统计）

- `rag_query(query, top_k)`：对已确认行做关键词检索（商品/公司/日期/文件名），返回明细
- `rag_summarize(group_by, filter)`：按 item/desc/date 聚合（数量、金额小计），
  供统计问答与画图复用
- 因为数据已入库并确认，Agent 只把**命中结果**喂给 DeepSeek —— 省 token 且结果可靠

### 3.6 Tool4 — Plot（可视化）

- `plot_chart(group_by, chart_type, filter)`：matplotlib 生成 PNG 到 `charts/`
- 支持 bar（柱状）/ pie（饼图）；中文字体自动探测（SimHei / Microsoft YaHei）
- Web 端通过 `/charts/{file}` 展示

### 3.7 Agent 中枢

- **LLM**：DeepSeek `deepseek-chat`（langchain-deepseek 官方接入）
- **编排**：`AgentExecutor + create_tool_calling_agent`（tool-calling 自动决策）
- **系统提示**：告知 Agent 何时用哪个 Tool、确认状态语义、禁止编造数据
- **可扩展**：新增能力 = 新增一个 `@tool` 函数并加入列表即可

---

## 4. 关键技术结论（实测沉淀）

1. **InternVL 输出契约 = 训练 8 列**；prompt 需中英语义引导，纯英文 key 会把 `from` 认成日期
2. **识别数据必须人工确认后才进统计**：模型有幻觉/重复行（如一张图 13 行明细实为 3 种商品），草稿不能直接用于统计
3. **单槽 llama.cpp 一次只处理一个请求**：一张图约 20~60s，长 prompt 会卡服务（曾复现读超时）
4. **langchain 1.x 注意**：`AgentExecutor` 在 `langchain_classic.agents`（`create_tool_calling_agent` 同处）；
   纯 `langchain.agents` 走新 `create_agent` API
5. **matplotlib 中文**需显式指定中文字体，否则图表乱码
6. 本项目 PyPI 直连不通，统一用清华源 `-i https://pypi.tuna.tsinghua.edu.cn/simple`

---

## 5. 运行与联调

### 5.1 依赖安装

```powershell
pip install -i https://pypi.tuna.tsinghua.edu.cn/simple langchain langchain-deepseek ^
    langchain-classic fastapi uvicorn python-multipart httpx matplotlib
```

### 5.2 环境变量（.env）

```
OCR_BASE_URL=http://127.0.0.1:9052/v1
OCR_MODEL=internvl3
DEEPSEEK_API_KEY=sk-xxx          # platform.deepseek.com
```

### 5.3 启动

```powershell
# 1) 先确保 InternVL 9052 在线(隧道/服务)
# 2) 启动 Web(单页集成了全部功能)
python run_web.py                # http://127.0.0.1:8100
```

### 5.4 API 一览（FastAPI，/docs 有 Swagger）

| 方法 & 路径 | 用途 |
|---|---|
| POST `/api/ocr` | multipart 上传图片 → 识别 → 入库待确认 |
| GET `/api/docs` | 单据列表（行数/确认状态）|
| GET `/api/docs/{name}` | 某图识别行 |
| POST `/api/rows/{id}` | 人工修改某行 |
| POST `/api/confirm` | 确认某图全部行 |
| POST `/api/agent` | 智能体问答（会按需调 Tool）|
| POST `/api/chart` | 画统计图 |
| GET `/charts/{file}` | 图表文件 |
| GET `/api/stats` | 总览 |

---

## 6. 测试与质量

- 单元测试（`tests/test_agent_v2.py`）：db CRUD、Correct/RAG Tool 逻辑
  （不依赖真实模型/网络，可离线跑）
- `ruff` 静态检查：通过

```powershell
pytest tests -q
ruff check db agent tools web config.py run_web.py
```

---

## 7. 当前进度与待办

| 项 | 状态 |
|---|---|
| SQLite 数据层（doc + rows / 状态机）| ✅ |
| Tool1 OCR（客户端 + 入库）| ✅ 代码就绪，**待 9052 真实联调** |
| Tool2 Correct | ✅ |
| Tool3 RAG | ✅ |
| Tool4 Plot | ✅ |
| Agent 中枢（8 工具）| ✅ |
| Web 单页面 | ✅ |
| 端到端真实联调（上传→改→确认→问答→图）| ⏳ 待 9052 在线 |
| 发票等第二类目扩展 | ⬜ 规划中（数据/模型未提供）|

---

## 8. 版本演进（git）

```
master       旧版: 双 FastAPI 服务(8000 OCR + 8100 rag/CSV)  ← 已归档, 保留
feat/agent-v2 新版: Agent 架构(本文档描述) ← 当前开发分支
```

如需查看旧版实现细节，切到 `master` 分支阅读。
