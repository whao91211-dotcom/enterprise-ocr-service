"""配置模块：全部配置经环境变量 / .env 注入（pydantic-settings）。"""

from functools import lru_cache
from typing import Annotated

from pydantic import field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


def _split_csv(value: str | None) -> list[str]:
    """兼容 "a,b,c" 与 JSON 数组两种写法，返回去空字符串列表。"""
    if value is None:
        return []
    if isinstance(value, str):
        value = value.strip()
        if not value:
            return []
        if value.startswith("["):  # pydantic-settings 可能按 JSON 解析 list 字段
            import json

            try:
                parsed = json.loads(value)
                return [str(x).strip() for x in parsed if str(x).strip()]
            except json.JSONDecodeError:
                pass
        return [x.strip() for x in value.split(",") if x.strip()]
    return [str(x).strip() for x in value if str(x).strip()]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore", case_sensitive=False
    )

    # ---- 应用 ----
    app_name: str = "enterprise-ocr-service"
    debug: bool = False
    log_level: str = "INFO"
    api_prefix: str = "/api/v1"

    # ---- OCR 模型服务（OpenAI 兼容端点，本地 9052 隧道转发）----
    ocr_base_url: str = "http://127.0.0.1:9052/v1"
    ocr_model: str = "internvl3"  # S0 已确认：网关用 internvl3（/v1/models 仅显示 gpt-3.5-turbo）
    ocr_api_key: str = ""
    ocr_timeout_seconds: float = 240.0
    ocr_max_tokens: int = 1024  # InternVL 实测定 1024 输出完整(finish=stop)，512 会被截断
    ocr_temperature: float = 0.7
    ocr_top_p: float = 0.8
    ocr_json_mode: bool = False  # 真实契约：返回 CSV 多行，非 JSON
    ocr_retries: int = 2  # 网络/5xx/超时额外重试次数（总尝试 = 该值 + 1）

    # ---- 数据库 ----
    # 默认 sqlite 免配置开箱即用（自动建表+种子）；生产用 .env 覆盖为 PostgreSQL
    database_url: str = "sqlite+aiosqlite:///./data/dev.db"

    # ---- 图片存储 ----
    storage_dir: str = "./data/images"

    # ---- 图片接入限制 ----
    ingest_max_bytes: int = 20 * 1024 * 1024
    # 以下列表字段环境变量按 CSV 解析（NoDecode 关闭 pydantic-settings 的 JSON 预解析）
    ingest_allowed_roots: Annotated[list[str], NoDecode] = []  # local_path 允许根（空=禁用）
    ingest_url_allowlist: Annotated[list[str], NoDecode] = []  # url/api 允许主机（"*" 全放行）
    ingest_api_base_url: str = ""  # 现有文件模块取图 API base
    ingest_api_token: str = ""
    ingest_allowed_exts: Annotated[list[str], NoDecode] = ["jpg", "jpeg", "png", "webp", "bmp"]

    # ---- 批量队列 ----
    # 默认关闭（单张联调避免 worker 写锁冲突）；批量导入时设 >0 开启
    batch_poll_interval_seconds: float = 0.0
    batch_page_size: int = 5  # 每轮 worker 取出的队列项数

    # ---- 身份与审核 ----
    auth_mode: str = "none"  # 本地默认免鉴权；生产 header
    image_url_ttl_seconds: int = 300
    signing_secret: str = ""

    @field_validator(
        "ingest_allowed_roots", "ingest_url_allowlist", "ingest_allowed_exts", mode="before"
    )
    @classmethod
    def _csv_list(cls, v: object) -> list[str]:
        if isinstance(v, list):
            return [str(x).strip() for x in v if str(x).strip()]
        return _split_csv(v)

    @property
    def allowed_exts_set(self) -> set[str]:
        return {e.lower().lstrip(".") for e in self.ingest_allowed_exts}

    @property
    def url_allowlist(self) -> set[str]:
        return {h.lower() for h in self.ingest_url_allowlist}

    @property
    def allowed_roots(self) -> list[str]:
        import os

        return [os.path.normcase(os.path.abspath(r)) for r in self.ingest_allowed_roots]

    @property
    def signing_key(self) -> str | None:
        return self.signing_secret or None


@lru_cache
def get_settings() -> Settings:
    return Settings()
