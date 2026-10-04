"""数据库引擎创建工具。

默认保持 SQLite 零部署；配置 PostgreSQL URL 后切换到服务器数据库。
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine, make_url


def _coerce_sqlite_url(path: str | os.PathLike[str]) -> str:
    return f"sqlite:///{Path(path).expanduser().resolve().as_posix()}"


def normalize_database_url(
    *,
    env_key: str,
    default_sqlite_path: str | os.PathLike[str],
) -> str:
    """读取数据库 URL；未配置时返回默认 SQLite 文件 URL。"""

    raw = (os.environ.get(env_key) or "").strip()
    if raw:
        return raw
    if env_key == "SPARKARC_USERS_DATABASE_URL":
        from core.database_config import configured_database_url

        configured = configured_database_url("users")
        if configured:
            return configured
    return _coerce_sqlite_url(default_sqlite_path)


def _is_sqlite_url(url: str) -> bool:
    try:
        return make_url(url).get_backend_name() == "sqlite"
    except Exception:
        return url.startswith("sqlite:")


def _install_sqlite_pragmas(engine: Engine) -> None:
    """为 SQLite 连接注入生产默认 PRAGMA。"""

    @event.listens_for(engine, "connect")
    def _set_sqlite_pragma(dbapi_connection, _connection_record) -> None:  # pragma: no cover - 由 DB 驱动触发
        cursor = dbapi_connection.cursor()
        try:
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA synchronous=NORMAL")
            cursor.execute("PRAGMA busy_timeout=10000")
            cursor.execute("PRAGMA cache_size=-64000")
            cursor.execute("PRAGMA foreign_keys=ON")
        finally:
            cursor.close()


def create_configured_engine(
    database_url: str,
    *,
    echo: bool = False,
    future: bool = True,
    sqlite_pool_size: int | None = 20,
    sqlite_max_overflow: int | None = 30,
    postgres_pool_size: int | None = 20,
    postgres_max_overflow: int | None = 40,
    pool_timeout: int = 60,
    **kwargs: Any,
) -> Engine:
    """创建按 dialect 调参的 SQLAlchemy Engine。"""

    engine_kwargs: dict[str, Any] = {
        "echo": echo,
        "future": future,
        **kwargs,
    }

    if _is_sqlite_url(database_url):
        if sqlite_pool_size is not None:
            engine_kwargs.setdefault("pool_size", sqlite_pool_size)
        if sqlite_max_overflow is not None:
            engine_kwargs.setdefault("max_overflow", sqlite_max_overflow)
        engine_kwargs.setdefault("pool_timeout", pool_timeout)
        engine = create_engine(database_url, **engine_kwargs)
        _install_sqlite_pragmas(engine)
        return engine

    engine_kwargs.setdefault("pool_size", postgres_pool_size)
    engine_kwargs.setdefault("max_overflow", postgres_max_overflow)
    engine_kwargs.setdefault("pool_timeout", pool_timeout)
    engine_kwargs.setdefault("pool_pre_ping", True)
    engine_kwargs.setdefault("pool_recycle", 1800)
    return create_engine(database_url, **engine_kwargs)


def create_engine_from_env(
    *,
    env_key: str,
    default_sqlite_path: str | os.PathLike[str],
    **kwargs: Any,
) -> Engine:
    """从环境变量或默认 SQLite 路径创建 Engine。"""

    return create_configured_engine(
        normalize_database_url(env_key=env_key, default_sqlite_path=default_sqlite_path),
        **kwargs,
    )
