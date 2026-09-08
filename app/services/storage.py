"""图片存储：本地目录实现（save/load/delete + 路径安全）。

对外接口稳定，后续替换 S3/OSS 时只改本模块。
storage_key 约定: yyyy/mm/<uuid>.<ext>
"""

from __future__ import annotations

import os
import uuid
from datetime import datetime
from pathlib import Path

from app.core.config import get_settings


def _root() -> Path:
    root = Path(get_settings().storage_dir).resolve()
    root.mkdir(parents=True, exist_ok=True)
    return root


def save_image(data: bytes, ext: str) -> str:
    """保存图片字节，返回 storage_key。"""
    ext = ext.lower().lstrip(".")
    root = _root()
    now = datetime.now()
    sub = now.strftime("%Y/%m")
    (root / sub).mkdir(parents=True, exist_ok=True)
    key = f"{sub}/{uuid.uuid4().hex}.{ext}"
    (root / key).write_bytes(data)
    return key


def resolve_path(storage_key: str) -> Path:
    """storage_key -> 绝对路径；拒绝路径穿越。"""
    root = _root()
    candidate = (root / storage_key).resolve()
    if not candidate.is_relative_to(root):
        raise ValueError(f"非法 storage_key: {storage_key}")
    return candidate


def load_image(storage_key: str) -> bytes:
    path = resolve_path(storage_key)
    if not path.is_file():
        raise FileNotFoundError(f"图片不存在: {storage_key}")
    return path.read_bytes()


def delete_image(storage_key: str) -> None:
    path = resolve_path(storage_key)
    if path.is_file():
        path.unlink(missing_ok=True)


def file_exists(storage_key: str) -> bool:
    try:
        return resolve_path(storage_key).is_file()
    except ValueError:
        return False


def make_public_id() -> str:
    return uuid.uuid4().hex


def ext_for_name(file_name: str) -> str:
    return Path(file_name).suffix.lower().lstrip(".") or "bin"


def safe_filename(file_name: str) -> str:
    return os.path.basename(file_name)
