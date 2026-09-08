# 企业 OCR → 人工审核 → 入库 → RAG 管线服务

内部图片（在线上传 / 本地路径 / 现有文件模块 URL·API）→ 微调视觉模型 OCR（OpenAI 兼容，本地 9052 隧道）→
结构化结果存库进入**待审队列** → 人工经 API 查看并修正 → 审核通过入库（**版本链 + 审计留痕 + 字段级 diff**）→
已审核数据经 **rag/data 增量接口 / v_rag_documents 视图**交付 RAG 同事。

- 审核/上传页面由前端同事基于本服务 OpenAPI（`/docs`）另行开发，本仓库只提供后端 API。
- 技术栈：Python 3.11+ / FastAPI / SQLAlchemy 2 async / PostgreSQL 16（开发可 sqlite 免迁移）。

## 目录结构

```
app/
├─ main.py                 # 应用工厂 + 生命周期(批量 worker)
├─ core/config.py          # 全部配置(env/.env, 见 .env.example)
├─ core/db.py              # async engine/session (PG: asyncpg; sqlite: aiosqlite)
├─ models.py               # 6 张表 ORM + 状态常量
├─ schemas.py              # API 契约(Pydantic)
├─ api/
│  ├─ deps.py              # 身份(X-User-Id/X-User-Name; AUTH_MODE=none 联调用)
│  ├─ serializers.py       # ORM→响应 + 原图 HMAC 签名 URL
│  └─ routes/              # ocr / ingest / documents / review / templates / rag / health
├─ services/
│  ├─ ingest.py            # 三来源接入: local_path/url/api + 魔数/大小/白名单校验
│  ├─ storage.py           # 原图存储(本地目录实现, 预留 S3/OSS 替换点)
│  ├─ pipeline.py          # 单文档: 接入→去重→存图→OCR→初值版本  (同步/批量共用)
│  ├─ ocr_client.py        # OpenAI 兼容 /chat/completions + 重试
│  ├─ prompt_builder.py    # 按模板生成识别指令(JSON Schema 形态)
│  ├─ result_parser.py     # 容错解析(text+fields+raw 存档)
│  ├─ validation.py        # 字段校验/清洗 (OCR 侧 warning; 审核侧阻断)
│  ├─ review_service.py    # approve/reject/reopen(乐观锁+diff+审计, 事务)
│  ├─ rag_service.py       # rag/data 游标分页 + tombstones
│  └─ batch_worker.py      # ingest_queue 消费者(同进程单协程)
└─ seed/seed_templates.py  # 默认模板种子(invoice/contract 示例)
docs/rag-handoff.md        # RAG 同事对接说明(必读)
scripts/mock_ocr_server.py # 9052 未就绪时的 mock OCR 服务
tests/                     # 53 个测试(含完整审核/批量/RAG 流程), respx mock OCR
```

## 快速开始（开发，sqlite + mock OCR，无需 Docker）

```powershell
# 1) 安装
python -m venv .venv
.\.venv\Scripts\python -m pip install -e ".[dev]"

# 2) 起一个 mock OCR（模拟 9052 的 OpenAI 兼容端点）
$env:OCR_BASE_URL="http://127.0.0.1:19052/v1"; $env:OCR_MODEL="mock-ocr-vl"
.\.venv\Scripts\python -m scripts.mock_ocr_server --port 19052   # 另开终端

# 3) 起服务（sqlite 自动建表并种入默认模板）
$env:DATABASE_URL="sqlite+aiosqlite:///./data/dev.db"; $env:STORAGE_DIR="./data/images"; $env:AUTH_MODE="none"
.\.venv\Scripts\python -m uvicorn app.main:app --reload --port 8000
# API 文档: http://127.0.0.1:8000/docs
```

## 联调（真实 9052 模型 + PostgreSQL）

1. 启动 9052 隧道/模型服务，`GET http://127.0.0.1:9052/v1/models` 确认可达并记下模型名。
2. `docker compose up -d` 起 PostgreSQL 16（或连已有库）。
3. 复制 `.env.example` 为 `.env`，配置 `OCR_MODEL`、`OCR_BASE_URL`、`DATABASE_URL`、
   `INGEST_ALLOWED_ROOTS`（本地收件路径，逗号分隔）、`INGEST_URL_ALLOWLIST`（远端主机白名单，`*` 全放行）。
4. 迁移 + 种子：
   ```powershell
   .\.venv\Scripts\python -m alembic upgrade head
   .\.venv\Scripts\python -m app.seed.seed_templates
   ```
5. 启动 `uvicorn app.main:app`。生产部署建议 nginx/gateway 转发并注入 `X-User-Id/X-User-Name`（AUTH_MODE=header）。

### 真实 InternVL 契约（已 S0 实测冻结）

> 模型名 `internvl3`（`/v1/models` 只显示 gpt-3.5-turbo，实际以 `internvl3` 调用返回 `"model":"internvl3"`）。
> 单槽 llama.cpp：**并发请求会排队/超时**，业务侧应串行（或 worker 串行消费队列）。

- 请求：`POST {9052}/v1/chat/completions`，`model=internvl3`，
  `temperature=0.7, top_p=0.8, max_tokens=1024`，`system="You are a helpful assistant."`，
  user.content 数组含 `{type:text, text:"# 任务描述\n你需要对图像中的销售消息进行结构化信息提取，输出一个标准CSV格式的数据。"}` + `{type:image_url, image_url:{url:"data:image/png;base64,..."}}`。
- **关键坑**：追加「字段定义/输出说明/表头」长 prompt 会触发 500/超时（已实测）。必须用上面的短任务描述。
- 返回：多行 CSV（无表头），列固定 `desc,item,amount,price,tax,sum`（顾客公司/货物/数量/单价/税率/金额）。
  实测 15 行、约 22s、`finish_reason=stop`。max_tokens=512 会被截断(finish=length)，用 1024 完整。
- 对应模板 `sales`（row_mode=True）：`corrections.fields.rows` 为行数组，逐行校验、逐单元格 diff。

对应 env：`OCR_MODEL=internvl3` `OCR_TEMPERATURE=0.7` `OCR_TOP_P=0.8` `OCR_MAX_TOKENS=1024`
`OCR_JSON_MODE=false` `OCR_TIMEOUT_SECONDS=300`（模型慢，超时给足）。

## 核心 API（前缀 /api/v1，全量见 /docs）

| 方法与路径 | 用途 |
|---|---|
| POST /ocr/upload | multipart 在线上传单张 + doc_type，同步 OCR |
| POST /ocr/submit | JSON 提交本地路径/URL/API 单张，同步 OCR |
| POST /ocr/import | 批量清单入队（返回 batch_id；worker 后台处理）|
| GET /ocr/import/{batch_id} | 批次逐项进度 |
| POST /queue/{item_id}/retry | 队列失败项重试 |
| GET /review/queue | 待审队列（默认 pending_review，可按 doc_type 过滤）|
| GET /documents/{id} | 详情：OCR 结果 + 最新版本 + 模板字段 + 原图地址 |
| GET /documents/{id}/history · /audit | 版本链 / 审计日志 |
| POST /documents/{id}/review | `{action: approve\|reject, expected_version, corrections?, comment?}` |
| POST /documents/{id}/reopen | approved/rejected → 返修 |
| POST /documents/{id}/ocr/retry | ocr_failed 重试 |
| GET /documents/{id}/image?expires=&sig= | 原图（HMAC 签名短时效）|
| GET/POST/PUT /templates | 单据模板（字段定义管理）|
| GET /rag/data | 已审核数据增量（cursor 幂等）|
| GET /rag/tombstones | 曾发布后变动的文档 id（RAG 清理）|
| GET /healthz | 存活 + DB |

## 审核流程要点（给 UI 同事）

1. `GET /review/queue` 取待审列表（附 OCR 初值摘要）。
2. `GET /documents/{id}` 详情里带 `template_field_defs`（可渲染编辑表单）与 `image_url`（原图对照）。
3. 提交 `POST /documents/{id}/review`：
   - approve：`expected_version` 必须等于详情里的 `current_version`（并发冲突返回 409）；
     `corrections.fields` 为**完整字段终值**（可不传=沿用 OCR 结果），服务端按模板校验，错误返回 422 及字段级明细；
   - reject：必须提供 `comment` 打回原因。
4. 审核通过后 `GET /documents/{id}/history` 可见 `kind=review` 的新版本与 `field_changes`（字段级 old→new）。
5. 上线后发现错误：`POST /documents/{id}/reopen` 返修，RAG 侧会收到 tombstone。

## 测试

```powershell
.\.venv\Scripts\python -m pytest tests -q        # 53 passed（sqlite + respx mock OCR，无需外网/DB）
.\.venv\Scripts\python -m ruff check app tests scripts
```

## 状态与边界语义

- 文档状态机：`ocr_pending → ocr_failed(可重试) / pending_review → approved | rejected`，均可 reopen 返修。
- sha256 全局去重：同图重复提交幂等返回既有文档，不重复 OCR。
- OCR 原始返回 `raw_response` 全量存档于 ocr_runs（可复盘/反哺微调）。
- rejected / 未审核数据永不出现在 rag 出口；曾 approved 后变动由 tombstones 通知。
- 接入安全：本地路径限 `INGEST_ALLOWED_ROOTS`、远端主机限 `INGEST_URL_ALLOWLIST`、魔数校验防伪扩展名。
- 图片为单张单据；多页/PDF 拆页为后续扩展点。批量 worker 为同进程单协程，量大可换 Celery/Redis（接口不变）。

## 待办（外部依赖）

- [ ] S0 契约冻结：9052 隧道启动后探测 `/v1/models` 确认模型名与真实返回格式，调整 result_parser 并真实联调。
- [ ] 现有文件模块取图 API 细节（INGEST_API_BASE_URL/凭证）确认后启用 `api` 来源。
- [ ] 业务确认单据字段清单（现为 invoice/contract 示例模板，可用 POST /templates 管理）。
