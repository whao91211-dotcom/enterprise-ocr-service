"""识别结果落盘：所有图片数据汇总到 output/ 下的【单一总 CSV】，同名覆盖更新。

文件: {output_dir}/all_sales.csv（UTF-8 with BOM，Excel 直接打开不乱码）
列:   源图片文件, 识别时间, 顾客公司, 发注日, 源公司, 项目, 数量, 单价, 税率, 金额
策略: 每次识别以「源图片文件名」为键 —— 先移除该图的旧行，再追加本批新行
      （即同一张图重识别后只保留最新一批，其它图的行不动）。
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

# 文件列: 前缀两列 + 8 列数据。key -> 中文表头
HEADERS: dict[str, str] = {
    "source_file": "源图片文件",
    "recognized_at": "识别时间",
    "desc": "顾客公司",
    "date": "发注日",
    "from": "源公司",
    "item": "项目",
    "amount": "数量",
    "price": "单价",
    "tax": "税率",
    "sum": "金额",
}
PREFIX_KEYS = ["source_file", "recognized_at"]
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


def save_result_rows(
    *,
    rows: list[dict],
    file_name: str | None,
    doc_type: str,
    meta: dict | None = None,
) -> Path:
    """把某张图的清洗结果 upsert 进总 CSV。返回总文件路径。

    rows: list[dict]，键为 SALES_KEYS 中的 key（允许缺失/多余，写盘按固定列序）。
    以 file_name 为覆盖键：先移除同名旧行，再追加新行；其它图的行保留。
    """
    source = file_name or "unknown"
    now = datetime.now().isoformat(timespec="seconds")
    data_rows: list[dict[str, str]] = []
    for row in rows:
        data_rows.append(
            {"source_file": source, "recognized_at": now, **{k: str(row.get(k, "")) for k in SALES_KEYS}}
        )

    path = total_csv_path()
    with _write_lock:
        existing = _read_existing(path)
        kept = [r for r in existing if r.get("source_file") != source]
        merged = kept + data_rows
        with path.open("w", encoding="utf-8-sig", newline="") as f:
            # 表头用中文标签（DictWriter.writeheader 会写英文 key，故手动写首行）
            writer = csv.DictWriter(f, fieldnames=ALL_KEYS, extrasaction="ignore")
            writer.writerow({k: HEADERS[k] for k in ALL_KEYS})
            writer.writerows(merged)
    return path
