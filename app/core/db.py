"""异步数据库：engine / session 工厂 / FastAPI 依赖。

使用说明：
- 生产/联调：DATABASE_URL 指向 PostgreSQL（asyncpg 驱动），迁移用 Alembic。
- 本地开发/测试：可临时用 sqlite+aiosqlite:///./data/dev.db（模型自动建表），
  或按 README 用 docker-compose 起 PostgreSQL 后执行 alembic upgrade head。
"""

from collections.abc import AsyncIterator

from sqlalchemy import MetaData
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy.types import JSON as _SAJSON

_engine: AsyncEngine | None = None
_session_factory: async_sessionmaker[AsyncSession] | None = None


def jsonb_type():
    return JSONB().with_variant(_SAJSON(), "sqlite")


class Base(DeclarativeBase):
    metadata = MetaData(
        naming_convention={
            "ix": "ix_%(column_0_label)s",
            "uq": "uq_%(table_name)s_%(column_0_name)s",
            "ck": "ck_%(table_name)s_%(constraint_name)s",
            "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
            "pk": "pk_%(table_name)s",
        }
    )


def init_db(database_url: str | None = None) -> AsyncEngine:
    """初始化全局 engine 与 session 工厂（幂等：重复调用以首次为准）。"""
    from sqlalchemy.pool import NullPool, StaticPool

    global _engine, _session_factory
    if _engine is not None:
        return _engine
    from app.core.config import get_settings

    url = database_url or get_settings().database_url
    kwargs: dict = {"pool_pre_ping": True}
    if url.startswith("sqlite"):
        kwargs["connect_args"] = {"check_same_thread": False}
        # 确保 sqlite 文件父目录存在
        db_path = url.split(":///", 1)[-1]
        if db_path and db_path != ":memory:":
            import os

            os.makedirs(os.path.dirname(os.path.abspath(db_path)), exist_ok=True)
        if ":memory:" in url:
            kwargs["poolclass"] = StaticPool
        else:
            # 每会话独立连接：跨事件循环/多协程安全（测试用）
            kwargs["poolclass"] = NullPool
    _engine = create_async_engine(url, **kwargs)
    _session_factory = async_sessionmaker(_engine, expire_on_commit=False)
    return _engine


def get_session_factory() -> async_sessionmaker[AsyncSession]:
    if _session_factory is None:
        init_db()
    assert _session_factory is not None
    return _session_factory


async def get_session() -> AsyncIterator[AsyncSession]:
    """FastAPI 依赖：每请求一个 session，自动提交/回滚语义由路由内显式 commit 控制。"""
    async with get_session_factory()() as session:
        yield session


async def dispose_db() -> None:
    global _engine, _session_factory
    if _engine is not None:
        await _engine.dispose()
        _engine = None
        _session_factory = None
