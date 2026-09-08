# 企业文档处理智能体（InternVL3 OCR + DeepSeek 多Tool 框架）

课程项目：基于 InternVL3 微调模型的企业文档处理多智能体框架。
**两层架构**：OCR 数据服务（本 README 下半部分）+ 智能体 Chatbot（`rag/` 子项目）。

```
图片 → InternVL3 OCR(8000) → all_sales.csv(待确认) → 人工确认(状态列)
     → rag_rebuild 索引 → 用户提问 → 意图识别 → rag_query/ocr Tool → DeepSeek 回答
```

- 智能体网页：`python rag_app.py` → http://127.0.0.1:8100 （流式展示工具调用过程）
- 需要 `.env` 配 `DEEPSEEK_API_KEY`
- 详细设计/四人分工/汇报素材：`docs/project-plan.md`；rag 说明：`rag/README.md`

---

# 销售单据识别 → 汇总 CSV 服务（OCR 数据层 v0.4）

上传销售单据图片 → InternVL（OpenAI 兼容端点，本地 9052 转发）识别 → 8 列清洗 →
**所有图片数据汇总写入单个 `output/all_sales.csv`**（同名覆盖更新）→ 人工可直改 CSV，
用「状态列 + confirm 动作」保护人工修改不被重识别覆盖。不依赖数据库。

> v1（数据库版：文档表 + 审核状态机 + /rag 出口）已 git 存档：tag `v1-archive` / 分支 `archive-v1`。
> 人工修改环节将在全流程整合阶段重新加入（计划中）。

## 识别列契约（8 列，与训练标注一致）

| 列 | 含义 | 清洗规则 |
|---|---|---|
| desc | 顾客公司 | 文本原样 |
| date | 发注日 | 归一为 `YYYY年MM月DD日`（如 2025年08月07日） |
| from | 源公司 | 文本原样 |
| item | 项目 | 文本原样 |
| amount | 数量 | 取整（如 4） |
| price | 单价 | 去 `¥`/千分位逗号，两位小数（如 14781.00） |
| tax | 税率 | 原样保留（如 0.10） |
| sum | 金额 | 去 `¥`/千分位逗号，两位小数（如 14781.00） |

## 目录结构

```
app/
  main.py               # FastAPI：识别 API + 健康检查（无数据库）
  core/config.py        # 配置（OCR 端点 / output 目录）
  api/routes/ocr.py     # POST /api/v1/ocr/recognize
  services/
    ocr_client.py       # OpenAI 兼容视觉模型客户端（9052, 带重试）
    prompt_builder.py   # 销售 8 列指令（cols8/train/short 模式）
    result_parser.py    # CSV 多行容错解析
    cleaner.py          # 8 列清洗（上表规则）
    csv_store.py        # 汇总单文件 all_sales.csv（同名覆盖 + 锁）
    recognize.py        # 编排：字节 → OCR → 解析 → 清洗 → 落盘
```

## 快速开始

```powershell
# 1. 创建虚拟环境并安装
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e ".[dev]"

# 2.（可选）按需配置 .env（默认值即可本地跑通）
Copy-Item .env.example .env

# 3. 启动（默认 127.0.0.1:8000）
uvicorn app.main:app --host 127.0.0.1 --port 8000
```

打开 http://127.0.0.1:8000/docs：

- `POST /api/v1/ocr/recognize`：`file` 选销售图，`doc_type=sales` → 同步识别 → 返回 8 列 rows + 落盘路径
- `GET /healthz`：探活

响应示例（rows 为清洗后 8 列）：

```json
{
  "ok": true, "doc_type": "sales", "row_count": 2,
  "csv_file": "all_sales.csv",
  "csv_path": "C:\\...\\output\\all_sales.csv",
  "rows": [
    {"desc": "SETソフトウェア株式会社", "date": "2024年05月22日",
     "from": "菱洋電子貿易(上海)有限公司", "item": "掃除機",
     "amount": "1", "price": "19121.00", "tax": "0.10", "sum": "19121.00"}
  ],
  "columns": ["desc","date","from","item","amount","price","tax","sum"],
  "warnings": []
}
```

## 输出文件（单一总 CSV，同名覆盖）

所有图片识别结果**汇总到一个文件**，便于后续整表喂给 RAG：

- `output/all_sales.csv` — UTF-8 BOM，Excel 直接打开不乱码
- 列：`源图片文件, 识别时间, 顾客公司, 发注日, 源公司, 项目, 数量, 单价, 税率, 金额`
- 策略：以「源图片文件」为键 —— 同一张图重新识别后**覆盖旧行**（只保留最新一批），
  其它图的行不受影响；不同图依次追加，行数持续累积
- 无每图 .meta.json（溯源信息在行的「源图片文件 + 识别时间」两列中）

## 9052 InternVL 契约（实测固化的关键结论）

- 端点：`POST http://127.0.0.1:9052/v1/chat/completions`，`model=internvl3`（`/v1/models` 只显示 gpt-3.5-turbo，实际必须用 internvl3）
- system：`You are a helpful assistant.`；user 文本为识别指令，图片以 base64 data URL 作为第二个 content part
- 训练标注（train.jsonl）为 **8 列**：desc,date,from,item,amount,price,tax,sum —— prompt 模式见 `prompt_builder.py`
- ⚠️ 训练同款完整字段定义 prompt（`train` 模式）在 9052 上会**读超时并可能卡死服务**（S1 复测）；折中用 `cols8` 模式（短指令 + 一句 8 列顺序引导）
- 单槽串行：一张图约 20~60s；**一次只发一个请求**，不要并发
- 9052 隧道需自行保持在线；卡死后重启服务即可恢复

## 测试

```powershell
pytest -q        # 27 passed（cleaner/csv_store/result_parser/recognize API/ocr_client）
ruff check app tests scripts
```

## 人工修改环节（CSV 直改 + 状态保护）

产品最终形态要求"识别结果无论如何都给人工修改机会"。采用最轻方案 —— **直接编辑
`output/all_sales.csv`**，用状态列防覆盖：

- `all_sales.csv` 列：`源图片文件, 识别时间, 状态, 修改人, 修改时间, 顾客公司, 发注日, 源公司, 项目, 数量, 单价, 税率, 金额`
- 状态取值：`待确认`(模型初稿) / `已确认`(人工核对过)
- **修改流程**：Excel 打开 `all_sales.csv` → 改错的行 → 保存（选 CSV UTF-8 格式）→
  调 `POST /api/v1/ocr/confirm`（`source_file`=图名, `reviewer`=谁改的）把该图标为已确认
- **重识别保护**：已确认的图再上传 → 默认**跳过**（不冲掉人工修改），响应 `skipped=true`；
  确需重跑加 `force=true` 强制覆盖（状态会回到"待确认"，需重新核对）
- 进度查询：`GET /api/v1/ocr/sources` 列出每张图 待确认/已确认 数量

> ⚠️ Excel 保存注意：文件 → 另存为 → 选「CSV UTF-8 (逗号分隔)」，不要存成 ANSI 否则中文乱码；
> 手工改数字时 Excel 可能去掉尾零（14781.00→14781），属于 Excel 显示特性，保存后建议抽查。
> 后续 RAG 整合阶段可只消费「已确认」的行（终稿），把「待确认」视为草稿。
