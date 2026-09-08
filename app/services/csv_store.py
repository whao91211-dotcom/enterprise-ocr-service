"""识别结果落盘：所有图片数据汇总到 output/ 下的【单一总 CSV】，同名覆盖更新。

文件: {output_dir}/all_sales.csv（UTF-8 with BOM，Excel 直接打开不乱码）
列:   源图片文件, 识别时间, 状态, 修改人, 修改时间, 顾客公司, 发注日, 源公司, 项目, 数量, 单价, 税率, 金额

状态语义(按图整体维护, 冗余到每行便于 Excel 查看):
  - 待确认: 刚由模型识别写入, 未经人工核对
  - 已确认: 人工已核对/修改（通过 confirm 动作或直接编辑 Excel 状态列）
重识别保护: 某图已确认时, 除非 force=True, 否则不再覆盖, 避免冲掉人工修改。

策略: 每次识别以「源图片文件名」为键 —— 先移除该图的旧行，再追加本批新行。
线程安全: 全局锁串行化读写（本地单进程足够）。
"""

from __future__ import annotations

import csv
import threading
from datetime import datetime
from pathlib import Path

from app.core.config import get_settings
from app.services.cleaner import SALES_KEYS

ALL_SALES_CSV = "all_sales.csv"

STATUS_PENDING = "待确认"
STATUS_CONFIRMED = "已确认"

# 文件列: 溯源/状态 4 列 + 8 列数据。key -> 中文表头
HEADERS: dict[str, str] = {
    "source_file": "源图片文件",
    "recognized_at": "识别时间",
    "status": "状态",
    "modified_by": "修改人",
    "modified_at": "修改时间",
    "desc": "顾客公司",
    "date": "发注日",
    "from": "源公司",
    "item": "项目",
    "amount": "数量",
    "price": "单价",
    "tax": "税率",
    "sum": "金额",
}
PREFIX_KEYS = ["source_file", "recognized_at", "status", "modified_by", "modified_at"]
ALL_KEYS = PREFIX_KEYS + SALES_KEYS

_write_lock = threading.Lock()


def _output_root() -> Path:
    root = Path(get_settings().output_dir).resolve()
    root.mkdir(parents=True, exist_ok=True)
    return root


def total_csv_path() -> Path:
    """总 CSV 文件路径（尚未确保存在）。"""
    return _output_root() / ALL_SALES_CSV


def _read_existing(path: Path) -> list[dict[str, str]]:
    """读取现有总 CSV（含表头则跳过），返回 dict 行列表（key 为 ALL_KEYS 英文）。"""
    if not path.exists():
        return []
    rows: list[dict[str, str]] = []
    with path.open(encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        if not reader.fieldnames:
            return []
        label_to_key = {v: k for k, v in HEADERS.items()}
        for line in reader:
            mapped: dict[str, str] = {}
            for label, value in line.items():
                key = label_to_key.get(label, label)
                mapped[key] = value or ""
            rows.append({k: mapped.get(k, "") for k in ALL_KEYS})
    return rows


def _write_all(path: Path, rows: list[dict[str, str]]) -> None:
    """整表写回（表头用中文标签，DictWriter.writeheader 会写英文 key 故手动写首行）。"""
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=ALL_KEYS, extrasaction="ignore")
        writer.writerow({k: HEADERS[k] for k in ALL_KEYS})
        writer.writerows(rows)


def save_result_rows(
    *,
    rows: list[dict],
    file_name: str | None,
    doc_type: str,
    meta: dict | None = None,
) -> Path:
    """把某张图的清洗结果 upsert 进总 CSV（新行状态=待确认）。返回总文件路径。

    rows: list[dict]，键为 SALES_KEYS 中的 key（允许缺失/多余，写盘按固定列序）。
    以 file_name 为覆盖键：先移除同名旧行，再追加新行；其它图的行保留。
    """
    source = file_name or "unknown"
    now = datetime.now().isoformat(timespec="seconds")
    data_rows: list[dict[str, str]] = []
    for row in rows:
        data_rows.append(
            {
                "source_file": source,
                "recognized_at": now,
                "status": STATUS_PENDING,
                "modified_by": "",
                "modified_at": "",
                **{k: str(row.get(k, "")) for k in SALES_KEYS},
            }
        )

    path = total_csv_path()
    with _write_lock:
        existing = _read_existing(path)
        kept = [r for r in existing if r.get("source_file") != source]
        merged = kept + data_rows
        _write_all(path, merged)
    return path


def list_sources() -> list[dict[str, str]]:
    """汇总每张图的状态（按图去重，保留每图首行信息）。返回按源文件名排序。"""
    path = total_csv_path()
    with _write_lock:
        rows = _read_existing(path)
    per: dict[str, dict[str, str]] = {}
    for r in rows:
        src = r.get("source_file") or ""
        if not src:
            continue
        if src not in per:
            per[src] = {
                "source_file": src,
                "row_count": "0",
                "status": r.get("status") or STATUS_PENDING,
                "recognized_at": r.get("recognized_at") or "",
                "modified_by": r.get("modified_by") or "",
                "modified_at": r.get("modified_at") or "",
            }
        per[src]["row_count"] = str(int(per[src]["row_count"]) + 1)
        # 图内任一行已确认 → 整体按已确认处理（人工可能只改了部分行）
        if (r.get("status") or STATUS_PENDING) == STATUS_CONFIRMED:
            per[src]["status"] = STATUS_CONFIRMED
    return sorted(per.values(), key=lambda x: x["source_file"])


def is_source_confirmed(source: str | None) -> bool:
    """该图是否已人工确认（重识别保护用）。图中任意行已确认即视为整体已确认。"""
    if not source:
        return False
    path = total_csv_path()
    with _write_lock:
        rows = _read_existing(path)
    return any(
        r.get("source_file") == source and (r.get("status") or STATUS_PENDING) == STATUS_CONFIRMED
        for r in rows
    )


def confirm_source(source: str, reviewer: str) -> int:
    """把某图全部行标记为已确认 + 记录修改人/时间。返回更新的行数(0=图不存在)。"""
    now = datetime.now().isoformat(timespec="seconds")
    path = total_csv_path()
    with _write_lock:
        existing = _read_existing(path)
        updated = 0
        for r in existing:
            if r.get("source_file") == source:
                r["status"] = STATUS_CONFIRMED
                r["modified_by"] = reviewer or ""
                r["modified_at"] = now
                updated += 1
        if updated:
            _write_all(path, existing)
    return updated
