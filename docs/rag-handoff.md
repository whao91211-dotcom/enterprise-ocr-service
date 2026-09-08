# RAG 同事对接说明（rag-handoff）

本文档面向**负责 RAG 的同事**：如何从本服务取得「已人工审核、可信任」的结构化文档数据。
本服务只产出干净数据 + 稳定的增量接口 + 撤回通知；**向量化 / chunking / 向量库由 RAG 侧负责**。

## 1. 数据语义（务必先读）

| 概念 | 说明 |
|---|---|
| document_id | 一个图片/一份单据的唯一 ID（UUID）。多次发布同一 id 时请**按 id 覆盖（upsert）**，不要追加成多条 |
| version_no | 内容版本号：1 = OCR 初值；≥2 = 人工审核通过的修正值。**只消费最新一版** |
| status | 只有 `approved` 的文档会出现在 rag/data；`rejected` 永不出现 |
| text | 整篇原文（审核人可能改过） |
| fields | 结构化字段（key 见单据模板 field_defs），审核人修正后的终值；金额为 number、日期为 `YYYY-MM-DD` |
| updated_at | 文档状态/内容最后变化时间（含 reopen/reject），做增量游标用 |
| reviewed_at | 最近一次审核通过时间 |

状态机：`pending_review → approved / rejected`，`approved/rejected → pending_review`(返修)。
**一个曾发布过的文档被 reopen/reject 后，RAG 侧必须删除或降权旧索引** —— 用 rag/tombstones 感知。

## 2. 拉取方式（二选一，推荐 API）

### 方式 A：HTTP API（推荐，跨网络/语言无关）
```bash
# 首次全量
GET /api/v1/rag/data?page_size=500
# 之后增量（用上一页返回的 next_cursor 推进）
GET /api/v1/rag/data?page_size=500&cursor=<next_cursor>
# 可选过滤
GET /api/v1/rag/data?doc_type=invoice&updated_since=2025-01-01T00:00:00
```
- 返回：`{items:[{document_id, doc_type, version_no, text, fields, updated_at, reviewed_at}], next_cursor}`
- `next_cursor` 为空 = 已到末页；每拉完一页即 upsert，重复拉取同一页幂等无害。
- 撤回通知：
```bash
GET /api/v1/rag/tombstones?page_size=500   # 可加 updated_since / cursor
```
  返回 `[{document_id, status, latest_approved_at, updated_at}]`，仅包含**曾 approved 后又变状态**的文档。
  消费建议：把 document_id 加入「待删除/降权」集合，等一轮 rag/data 增量拉完后再统一清理，避免误删刚重新发布的新版本。

### 方式 B：直连数据库（同网段/同机房时）
Alembic 迁移已建只读视图 `v_rag_documents`，语义与 rag/data 完全一致（最新 approved 版本）：
```sql
SELECT document_id, doc_type, version_no, text, fields, updated_at, reviewed_at
FROM v_rag_documents
WHERE updated_at > :last_sync
ORDER BY updated_at, document_id;
```
建议给 RAG 侧建只读账号并仅授权该视图 + documents 表 status 列查询。

## 3. 建议的文档拼装（喂给 chunk/向量前的文本组织）

`fields` 与 `text` 分两类信息，建议拼成可读文本再切块：

```python
def render(doc) -> str:
    lines = []
    for k, v in (doc["fields"] or {}).items():
        if v is not None:
            lines.append(f"{k}: {v}")
    lines.append("---原文---")
    lines.append(doc["text"] or "")
    return "\n".join(lines)
```
按单据类型维护字段中文 label 映射（字段定义可通过 `GET /api/v1/templates/{code}` 拿到 `label`），
拼装时优先用 label 而非 key。

## 4. 同步任务建议（幂等优先）

1. 每轮：拉取 tombstones（先记 ids）→ 按 cursor 拉完所有 rag/data 页 → upsert → 清理 tombstone ids。
2. 频率建议 1–5 分钟一轮；并发拉取加 `doc_type` 分片即可。
3. 若某轮中断：下一轮从持久化的 `next_cursor` 续拉即可（游标基于 updated_at+id，状态型增量天然幂等）。

## 5. 字段与模板
- `GET /api/v1/templates` 可拉全部模板的 field_defs（单据字段的 key/label/type/必填），方便动态渲染/构建字段 schema。
- OCR 初值仅供参考与质量评估，RAG 只应消费审核后的 version_no≥2 数据。
