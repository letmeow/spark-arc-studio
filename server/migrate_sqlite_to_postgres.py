"""将停写后的 SQLite 主库副本导入空 PostgreSQL，并逐表验证全部字段。"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
from pathlib import Path

from sqlalchemy import create_engine, func, inspect, select, text
from sqlalchemy.engine import make_url

from core.migration_specs import BASE_DIR, load_metadata
from core.database_config import configured_database_url


ENV_KEYS = {"users": "SPARKARC_USERS_DATABASE_URL", "llm": "AGENT_MATCHBOX_DATABASE_URL"}


def table_digest(connection, table):
    """按主键稳定排序，比较经模型解码后的完整业务数据。"""
    digest = hashlib.sha256()
    count = 0
    result = connection.execute(select(table).order_by(*table.primary_key.columns))
    for row in result:
        payload = json.dumps(dict(row._mapping), ensure_ascii=False, sort_keys=True,
                             default=str, separators=(",", ":"))
        digest.update(payload.encode("utf-8") + b"\n")
        count += 1
    return count, digest.hexdigest()


def migrate(db_name: str, source_path: Path, *, verify_only: bool = False):
    """复用现有模型、迁移版本和类型转换；禁止覆盖目标中的业务记录。"""
    target_url = configured_database_url(db_name)
    if not target_url or make_url(target_url).get_backend_name() != "postgresql":
        raise ValueError(f"请填写 PostgreSQL 分项配置、SPARKARC_POSTGRES_URL 或 {ENV_KEYS[db_name]}")
    source_path = source_path.resolve(strict=True)
    # 仅导入模型，避免启动组件管理器或改写来源数据库。
    os.environ["AGENT_MATCHBOX_DISABLED"] = "1"
    metadata = load_metadata(db_name)
    source = create_engine(f"sqlite:///file:{source_path.as_posix()}?mode=ro&uri=true")
    target = create_engine(target_url)
    try:
        from core.auto_migrate import _get_head_revision, run_db_upgrade

        with source.connect() as src:
            if src.execute(text("PRAGMA integrity_check")).scalar() != "ok":
                raise ValueError("来源 SQLite 完整性检查失败")
            revision = src.execute(text("SELECT version_num FROM alembic_version")).scalar()
            if revision != _get_head_revision(str(BASE_DIR), db_name):
                raise ValueError("来源版本与当前代码不一致，请先用对应版本升级 SQLite 副本")
            tables = set(inspect(src).get_table_names()) - {"alembic_version"}
            if tables != set(metadata.tables):
                raise ValueError(f"来源表结构不一致: {tables ^ set(metadata.tables)}")
            for table in metadata.sorted_tables:
                columns = {item["name"] for item in inspect(src).get_columns(table.name)}
                if columns != set(table.columns.keys()):
                    raise ValueError(f"来源列结构不一致: {table.name}")

            if not verify_only:
                run_db_upgrade(db_name, str(BASE_DIR))
            with target.begin() as dst:
                if not verify_only:
                    for table in metadata.sorted_tables:
                        if dst.scalar(select(func.count()).select_from(table)):
                            raise ValueError(f"目标非空，拒绝覆盖: {table.name}")
                    for table in metadata.sorted_tables:
                        rows = src.execute(select(table).order_by(*table.primary_key.columns))
                        for batch in rows.partitions(500):
                            dst.execute(table.insert(), [dict(row._mapping) for row in batch])
                report = {}
                for table in metadata.sorted_tables:
                    source_digest = table_digest(src, table)
                    if source_digest != table_digest(dst, table):
                        raise ValueError(f"完整内容校验失败: {table.name}")
                    report[table.name] = {"rows": source_digest[0], "sha256": source_digest[1]}
                    if not verify_only:
                        for column in table.primary_key.columns:
                            sequence = dst.scalar(text("SELECT pg_get_serial_sequence(:t, :c)"),
                                                  {"t": table.name, "c": column.name})
                            if sequence:
                                maximum = dst.scalar(select(func.max(column)))
                                dst.execute(text("SELECT setval(CAST(:s AS regclass), :v, :used)"),
                                            {"s": sequence, "v": max(maximum or 0, 1),
                                             "used": maximum is not None and maximum >= 1})
                return report
    finally:
        source.dispose()
        target.dispose()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("db", choices=ENV_KEYS)
    parser.add_argument("source", type=Path, help="停写后备份的 SQLite 文件")
    parser.add_argument("--verify-only", action="store_true", help="只校验，不写目标数据库")
    args = parser.parse_args()
    # 数据异常可能包含密钥字段，终端只显示错误类别，不打印驱动异常参数。
    try:
        report = migrate(args.db, args.source, verify_only=args.verify_only)
    except Exception as exc:
        detail = str(exc) if isinstance(exc, ValueError) else "请检查版本、目标空库、约束及连接配置"
        if isinstance(getattr(exc, "orig", None), sqlite3.Error):
            detail = str(exc.orig)
        elif getattr(getattr(exc, "orig", None), "sqlstate", None):
            detail += f" (SQLSTATE={exc.orig.sqlstate})"
        print(f"迁移失败 ({type(exc).__name__})：{detail}")
        raise SystemExit(1) from None
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
