"""图片接入层：multipart / local_path / url / api 统一归一化为图片字节。

安全约束（企业级）：
- local_path 仅允许 INGEST_ALLOWED_ROOTS 内真实路径；
- url/api 仅允许 INGEST_URL_ALLOWLIST 内主机（"*" 放行；空列表 = 全部拒绝）；
- 一律校验魔数（防止改名换后缀）、大小上限、扩展名白名单。
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
from urllib.parse import urlparse

import httpx

from app.core.config import get_settings

_MAGIC: dict[str, tuple[bytes, int]] = {
    "jpg": (b"\xff\xd8\xff", 3),
    "jpeg": (b"\xff\xd8\xff", 3),
    "png": (b"\x89PNG\r\n\x1a\n", 8),
    "webp": (b"WEBP", 4),
    "bmp": (b"BM", 2),
}


class IngestError(Exception):
    """接入失败（带用户可读信息）。"""


def sniff_ext(data: bytes) -> str | None:
    """按魔数推断真实格式。"""
    # webp: RIFF....WEBP (前12字节)
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp"
    for ext, (magic, n) in _MAGIC.items():
        if ext == "webp":
            continue
        if data.startswith(magic[:n]):
            return ext
    return None


def _check_bytes(data: bytes, declared_ext: str | None) -> str:
    settings = get_settings()
    if not data:
        raise IngestError("空文件")
    if len(data) > settings.ingest_max_bytes:
        raise IngestError(
            f"文件过大: {len(data)} 字节 > 上限 {settings.ingest_max_bytes} 字节"
        )
    real = sniff_ext(data)
    if real is None:
        raise IngestError("无法识别图片格式（魔数校验失败），仅支持 jpg/png/webp/bmp")
    allowed = settings.allowed_exts_set
    if allowed and real not in allowed:
        raise IngestError(f"图片格式 {real} 不在白名单 {sorted(allowed)}")
    # 声明扩展名与魔数不一致时仅告警不阻断（以魔数为准，入库扩展名用魔数结果）
    return real


def sha256_of(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


async def fetch_bytes(source: str, ref: str) -> tuple[bytes, str, str]:
    """统一取字节：返回 (data, real_ext, display_name)。"""
    if source == "local_path":
        return await _from_local_path(ref)
    if source == "url":
        return await _from_url(ref, use_api_auth=False)
    if source == "api":
        return await _from_url(ref, use_api_auth=True)
    raise IngestError(f"未知来源类型: {source}")


async def _from_local_path(ref: str) -> tuple[bytes, str, str]:
    settings = get_settings()
    roots = settings.allowed_roots
    if not roots:
        raise IngestError("local_path 来源未启用：请配置 INGEST_ALLOWED_ROOTS")
    raw = os.path.abspath(os.path.normpath(ref))
    norm = os.path.normcase(raw)
    if not any(norm == r or norm.startswith(r + os.sep) for r in roots):
        raise IngestError(f"路径不在允许根目录内: {ref}")
    path = Path(raw)
    if not path.is_file():
        raise IngestError(f"文件不存在: {ref}")
    data = path.read_bytes()
    ext = path.suffix.lower().lstrip(".")
    real = _check_bytes(data, ext or None)
    return data, real, path.name


async def _from_url(ref: str, *, use_api_auth: bool) -> tuple[bytes, str, str]:
    settings = get_settings()
    if use_api_auth:
        base = settings.ingest_api_base_url.rstrip("/")
        if not base:
            raise IngestError("api 来源未启用：请配置 INGEST_API_BASE_URL")
        url = ref if ref.startswith(("http://", "https://")) else f"{base}/{ref.lstrip('/')}"
    else:
        url = ref
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise IngestError(f"非法 URL: {ref}")
    allowlist = settings.url_allowlist
    if not allowlist:
        raise IngestError("url/api 来源未启用：请配置 INGEST_URL_ALLOWLIST")
    if "*" not in allowlist and parsed.hostname.lower() not in allowlist:
        raise IngestError(f"主机不在白名单内: {parsed.hostname}")

    headers = {}
    if use_api_auth and settings.ingest_api_token:
        headers["Authorization"] = f"Bearer {settings.ingest_api_token}"

    timeout = httpx.Timeout(15.0, connect=5.0)
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=False) as client:
        try:
            resp = await client.get(url, headers=headers)
        except httpx.HTTPError as exc:
            raise IngestError(f"下载失败: {exc}") from exc
        if resp.status_code != 200:
            raise IngestError(f"下载失败: HTTP {resp.status_code}")
        content_length = resp.headers.get("content-length")
        if content_length and int(content_length) > settings.ingest_max_bytes:
            raise IngestError(f"远端文件超过大小上限: {content_length} 字节")
        data = resp.content
    if len(data) > settings.ingest_max_bytes:
        raise IngestError(f"远端文件超过大小上限: {len(data)} 字节")
    declared = Path(urlparse(url).path).suffix.lower().lstrip(".")
    real = _check_bytes(data, declared or None)
    return data, real, Path(urlparse(url).path).name or "remote_image"


def check_upload_bytes(data: bytes, file_name: str) -> str:
    """multipart 上传字节校验，返回真实扩展名。"""
    declared = Path(file_name).suffix.lower().lstrip(".")
    return _check_bytes(data, declared or None)
