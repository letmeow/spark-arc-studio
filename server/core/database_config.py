"""SparkArc 数据库部署配置：共用连接、固定库名和缺库初始化。"""

from __future__ import annotations

import os
import re
from typing import Mapping

from sqlalchemy import create_engine, text
from sqlalchemy.engine import URL, make_url
from sqlalchemy.exc import DBAPIError
from sqlalchemy.pool import NullPool


DATABASE_ENV_KEYS = {"users": "SPARKARC_USERS_DATABASE_URL", "llm": "AGENT_MATCHBOX_DATABASE_URL"}


def shared_postgres_url(env: Mapping[str, str] | None = None) -> str | None:
    """从高级 URL 或分项配置生成共用连接；密码按原文交给结构化 URL 接口。"""
    values = os.environ if env is None else env
    shared = str(values.get("SPARKARC_POSTGRES_URL") or "").strip()
    if shared:
        try:
            url = make_url(shared)
            if url.get_backend_name() != "postgresql":
                raise ValueError
        except Exception:
            raise ValueError("SPARKARC_POSTGRES_URL 必须是有效的 PostgreSQL 连接串") from None
        if url.drivername == "postgresql":
            url = url.set(drivername="postgresql+psycopg")
        return url.render_as_string(hide_password=False)
    host = str(values.get("SPARKARC_POSTGRES_HOST") or "").strip()
    if not host:
        return None
    try:
        port = int(values.get("SPARKARC_POSTGRES_PORT") or "5432")
        if not 1 <= port <= 65535:
            raise ValueError
    except (TypeError, ValueError):
        raise ValueError("SPARKARC_POSTGRES_PORT 必须是 1 到 65535 的端口号") from None
    username = str(values.get("SPARKARC_POSTGRES_USER") or "sparkarc").strip()
    password = values.get("SPARKARC_POSTGRES_PASSWORD")
    if not username or password is None or password == "":
        raise ValueError("请填写 PostgreSQL 账号和 SPARKARC_POSTGRES_PASSWORD 密码")
    return URL.create("postgresql+psycopg", username=username, password=password,
                      host=host, port=port, database="postgres").render_as_string(hide_password=False)


def configured_database_url(db_name: str, env: Mapping[str, str] | None = None) -> str | None:
    """单库显式配置优先，否则从共用 PostgreSQL URL 派生稳定的数据库名。"""
    values = os.environ if env is None else env
    explicit = str(values.get(DATABASE_ENV_KEYS[db_name]) or "").strip()
    if explicit:
        return explicit
    shared = shared_postgres_url(values)
    if not shared:
        return None
    url = make_url(shared)
    prefix = str(values.get("SPARKARC_DATABASE_PREFIX") or "sparkarc").strip()
    if not re.fullmatch(r"[a-z][a-z0-9_]{0,56}", prefix):
        raise ValueError("SPARKARC_DATABASE_PREFIX 必须以小写字母开头，仅含小写字母、数字、下划线，最长 57 字符")
    if url.drivername == "postgresql":
        url = url.set(drivername="postgresql+psycopg")
    return url.set(database=f"{prefix}_{db_name}").render_as_string(hide_password=False)


def ensure_postgres_database(database_url: str, maintenance_url: str) -> None:
    """仅在目标不存在时尝试建库；连接错误和权限不足均阻断启动。"""
    target = create_engine(database_url, poolclass=NullPool)
    try:
        try:
            with target.connect():
                return
        except DBAPIError:
            # 驱动的连接阶段不一定提供 SQLSTATE，需从维护库确认目标是否存在。
            pass
    finally:
        target.dispose()
    # CREATE DATABASE 不允许在事务内执行，且标识符不能作为普通绑定参数。
    admin_url = make_url(maintenance_url)
    if admin_url.drivername == "postgresql":
        admin_url = admin_url.set(drivername="postgresql+psycopg")
    admin = create_engine(admin_url.set(database=admin_url.database or "postgres"),
                          isolation_level="AUTOCOMMIT", poolclass=NullPool)
    try:
        database_name = make_url(database_url).database
        identifier = admin.dialect.identifier_preparer.quote_identifier(database_name)
        with admin.connect() as connection:
            if connection.scalar(text("SELECT 1 FROM pg_database WHERE datname = :name"),
                                 {"name": database_name}):
                raise RuntimeError("PostgreSQL 目标已存在但连接失败，请检查认证和访问权限；不会切换数据库")
            try:
                connection.exec_driver_sql(f"CREATE DATABASE {identifier}")
            except DBAPIError as exc:
                # 多个应用进程可能同时初始化；仅允许忽略另一个进程已经建库。
                if getattr(exc.orig, "sqlstate", None) != "42P04":
                    raise
    except DBAPIError:
        raise RuntimeError("PostgreSQL 连接或初始化失败，请检查认证与维护库访问权限；缺库时需 CREATEDB 权限或预先建库") from None
    finally:
        admin.dispose()
