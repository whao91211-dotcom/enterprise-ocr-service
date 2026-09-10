# DocMind Agent · 企业文档处理智能体

基于 LangChain Agent（DeepSeek LLM）的企业文档处理智能体，**以 Agent 为中心**装配 6 组可插拔 Tool。
识别 InternVL 微调模型的销售/支票单据 → 人工确认 → SQLite 入库 → SQL 直查统计 → 画图 / 生成报告。

> 当前分支：`feat/agent-v2`（默认分支）。旧版（双 FastAPI 服务）保留在 `master`。

## 架构

```
用户(深色 Web 聊天界面, 上传图/文字)
   │
   ▼
LangChain Agent (DeepSeek function-calling, 自主决定调哪个 Tool)
   │
   ├─ Tool1 OCR       ocr_recognize          InternVL(9052) 识别 → SQLite(待确认)
   ├─ Tool2 Correct   correct_list/show/update/confirm   人工核对/修改(模型~80%)
   ├─ Tool3 检索      rag_query              已确认数据明细(SQL WHERE, 关键词/年份)
   ├─ Tool4 统计      rag_summarize          聚合(GROUP BY + SUM, 数量/金额)
   ├─ Tool5 画图      plot_chart             统计图(matplotlib → charts/*.png)
   └─ Tool6 报表      generate_report        Word 统计报告(docx → reports/)
   │
   ▼
SQLite (data/agent.db): documents(图) + ocr_rows(识别行, status=pending/confirmed)
```

**为什么 6 个 Tool**（每个补一个短板）：
1. DeepSeek 是"盲"的 → OCR Tool（InternVL 微调识别图片）
2. OCR 只有 ~80% 准确率 → Correct Tool（人工修改，确认后才进统计）
3. 多图全喂 DeepSeek 费 token → **检索/统计直接 SQL 直查**（不再全表读入内存），只把聚合结果喂 LLM
4. 统计要直观 → Plot Tool（画柱状/饼图）
5. 统计要交付 → Report Tool（生成 Word 报告）

## 快速开始

```powershell
# 1. 依赖(清华源; requirements.txt 已含全部)
pip install -i https://pypi.tuna.tsinghua.edu.cn/simple -r requirements.txt

# 2. .env(已配好): OCR_BASE_URL=http://127.0.0.1:9052/v1  OCR_MODEL=internvl3
#    DEEPSEEK_API_KEY=sk-xxx

# 3. 启动 InternVL 9052(隧道), 然后:
python run_web.py        # http://127.0.0.1:8100
```

浏览器打开后可直接对话，例如：
- 上传一张销售单据图片 → Agent 自动识别入库（待确认）
- 说"确认" → 人工确认（进入统计口径）
- 问"2022年销售额如何" → Agent 调 `rag_summarize(year=2022)` 精确返回
- 说"画个柱状图" → Agent 调 `plot_chart`
- 说"生成一份2022年统计报告" → Agent 生成 Word 报告到 `reports/`

## 目录

```
config.py              .env 配置(OCR 9052 / DeepSeek)
db/                    SQLite 数据层
  schema.py            documents(图) + ocr_rows(识别行) 两级表
  crud.py              CRUD + SQL 直查(search_confirmed / summarize_confirmed)
tools/                 可插拔工具(每个独立)
  ocr_client.py        InternVL 客户端(cols8 中英语义 prompt)
  ocr_tool.py          Tool1 识别
  correct_tool.py      Tool2 人工核对(4 子工具)
  rag_tool.py          Tool3/4 检索统计(真 SQL)
  plot_tool.py         Tool5 画图
  report_tool.py       Tool6 Word 报表
agent/
  llm.py               ChatDeepSeek
  agent.py             手动 tool-calling 循环 + 6 组工具 + 流式事件
web/
  agent_chat.py        SSE 流式端点 /api/agent/chat(支持图片)
  agentchat.html       深色工业风聊天界面
  main.py              入口 + 其它 /api/*(correct/docs/chart)
run_web.py / start_web.py  启动
requirements.txt       依赖清单
tests/                 db+tools 单元测试
reports/               报表输出(gitignore)
charts/                图表输出(gitignore)
```

## 检索为何用 SQL 直查

销售数据是**结构化表格**（非文档语义），无需向量检索。直接 `SELECT ... WHERE item LIKE ? / substr(date,1,4)=? GROUP BY ... SUM(...)`，
结果 100% 精确、只把聚合后的几行喂 LLM —— **省 token、速度快**。（早期版本曾全表读入内存再 Python 过滤，已废弃。）

## 测试

```powershell
pytest tests -q
ruff check db agent tools web config.py
```

## 环境依赖

- **InternVL 9052**：识别单据（OpenAI 兼容端点，单槽串行，重启可恢复）
- **DeepSeek API**：Agent 决策与回答（`.env` 配 key）
- Python 3.11+；依赖见 `requirements.txt`（安装用清华源 `-i https://pypi.tuna.tsinghua.edu.cn/simple`）
