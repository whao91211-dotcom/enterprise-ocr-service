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

    # ---- 识别结果落盘 ----
    output_dir: str = "./output"  # 每张图一个 .csv + .meta.json，平铺于此
    allowed_exts: Annotated[list[str], NoDecode] = ["jpg", "jpeg", "png", "webp", "bmp"]
    max_image_bytes: int = 20 * 1024 * 1024

    @field_validator("allowed_exts", mode="before")
    @classmethod
    def _csv_list(cls, v: object) -> list[str]:
        if isinstance(v, list):
            return [str(x).strip() for x in v if str(x).strip()]
        return _split_csv(v)

    @property
    def allowed_exts_set(self) -> set[str]:
        return {e.lower().lstrip(".") for e in self.allowed_exts}


@lru_cache
def get_settings() -> Settings:
    return Settings()
