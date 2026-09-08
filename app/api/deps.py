"""请求身份依赖：AUTH_MODE=header 时读取网关注入的 X-User-Id/X-User-Name；none 时本地联调。"""

from __future__ import annotations

from dataclasses import dataclass

from fastapi import Request

from app.core.config import get_settings


@dataclass
class CurrentUser:
    id: str
    name: str


def get_current_user(request: Request) -> CurrentUser:
    settings = get_settings()
    if settings.auth_mode == "none":
        return CurrentUser(id="local", name="local")
    user_id = request.headers.get("X-User-Id") or "anonymous"
    user_name = request.headers.get("X-User-Name") or user_id
    return CurrentUser(id=user_id, name=user_name)
