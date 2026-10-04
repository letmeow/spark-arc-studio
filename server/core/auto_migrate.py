import os
import logging
import sqlite3
import sqlalchemy as sa

from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory

from core.migration_specs import (
    BASE_DIR as MIGRATION_BASE_DIR,
    get_database_url,
    get_db_path,
    get_version_dir,
    load_metadata,
)

# 统一日志出口；避免重复配置 root logger
logger = logging.getLogger("alembic_runner")
if not logging.getLogger().handlers:
    logging.basicConfig(level=logging.INFO)

# 服务根目录（server/）
BASE_DIR = str(MIGRATION_BASE_DIR)


def _get_db_path(db_name: str) -> str:
    return str(get_db_path(db_name))


# ============================================================
# 工具函数
# ============================================================

def _get_current_db_revision(db_path: str) -> str:
    """快速读取数据库当前版本号，用于短路跳过无变更的升级。"""
    if not os.path.exists(db_path):
        return None
    try:
        conn = sqlite3.connect(db_path)
        cur = conn.cursor()
        cur.execute("SELECT version_num FROM alembic_version")
        row = cur.fetchone()
        conn.close()
        if row:
            return row[0]
    except Exception:
        pass
    return None


def _has_user_tables(db_path: str) -> bool:
    """判断数据库是否已存在业务表，用于区分"空库"与"旧库未纳管"场景。"""
    if not os.path.exists(db_path):
        return False
    try:
        conn = sqlite3.connect(db_path)
        cur = conn.cursor()
        cur.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        )
        rows = [name for (name,) in cur.fetchall()]
        conn.close()
        return any(
            name not in ("alembic_version",) and not name.startswith("_alembic_tmp_")
            for name in rows
        )
    except Exception:
        return False


def _get_head_revision(server_dir: str, db_name: str) -> str:
    """读取迁移脚本的 head 版本号（不触发 env.py）。"""
    alembic_cfg_path = os.path.join(server_dir, "alembic.ini")
    alembic_cfg = Config(alembic_cfg_path)

    # 直接指定 version_locations 以读取对应分支的脚本
    version_dir = get_version_dir(db_name, base_dir=server_dir)
    if not os.path.exists(version_dir):
        return None

    # 构建最小 Config 以读取脚本信息
    alembic_cfg.set_main_option("script_location", os.path.join(server_dir, "alembic"))
    alembic_cfg.set_main_option("version_locations", str(version_dir))

    try:
        script = ScriptDirectory.from_config(alembic_cfg)
        return script.get_current_head()
    except Exception as e:
        logger.warning(f"Error reading script head: {e}")
        return None


def _build_alembic_config(base_dir: str, db_name: str) -> Config:
    """构造带分支配置的 Alembic Config（确保 version_locations 正确）。"""
    alembic_cfg_path = os.path.join(base_dir, "alembic.ini")
    alembic_cfg = Config(alembic_cfg_path)
    alembic_cfg.cmd_opts = type("CmdOpts", (), {"x": [f"db={db_name}"]})()
    alembic_cfg.set_main_option("script_location", os.path.join(base_dir, "alembic"))
    alembic_cfg.set_main_option("version_locations", str(get_version_dir(db_name, base_dir=base_dir)))
    alembic_cfg.set_main_option("path_separator", "os")
    return alembic_cfg


def _stamp_head(db_name: str, db_path: str, base_dir: str) -> None:
    """清空旧版本号并 stamp 到当前 head，不执行结构迁移。"""
    with sqlite3.connect(db_path) as raw_conn:
        try:
            raw_conn.execute("DELETE FROM alembic_version")
            raw_conn.commit()
        except sqlite3.OperationalError:
            # 表不存在时由 Alembic stamp 自动创建并写入版本号。
            pass

    original_cwd = os.getcwd()
    try:
        os.chdir(base_dir)
        cfg = _build_alembic_config(base_dir, db_name)
        command.stamp(cfg, "head")
    finally:
        os.chdir(original_cwd)


def _get_target_metadata(db_name: str):
    """加载指定数据库对应的 SQLAlchemy Metadata。"""
    try:
        return load_metadata(db_name)
    except ImportError as e:
        logger.warning(f"⚠️ Unable to load [{db_name}] model metadata: {e}")
        return None


def _is_internal_table(name: str) -> bool:
    if not name:
        return False
    return name == "alembic_version" or name.startswith("sqlite_") or name.startswith("_alembic_tmp_")


def _is_missing_object_error(err_msg: str) -> bool:
    """判断错误是否由引用了不存在的数据库对象（列/表/索引）导致。

    典型场景：迁移脚本尝试 drop/alter 一个在当前 DB 中不存在的列或表。
    这通常是因为本地 DB 曾有幽灵结构（来自未提交的迁移），
    Alembic autogenerate 基于幽灵结构生成了 drop 指令，
    但云端 DB 从链构建，从未有过这些对象。
    """
    # SQLite: "no such column" / "no such table"
    # Alembic batch_alter: KeyError（列名不在反射结果中）
    # SQLAlchemy: "NoSuchTableError"
    missing_indicators = [
        "no such column",
        "no such table",
        "nosuchtableerror",
        # Alembic 的 drop_column 在找不到列时抛出 KeyError
        # 错误信息形如: KeyError: 'column_name'
    ]
    err_lower = err_msg.lower()
    for indicator in missing_indicators:
        if indicator in err_lower:
            return True
    # KeyError 的特征：错误消息被引号包裹且不含常见 SQL 关键字
    # 形如: 'sys_credit_output_price_per_million'
    if err_msg.startswith("'") and err_msg.endswith("'") and " " not in err_msg.strip("'"):
        return True
    return False


def _is_duplicate_object_error(err_msg: str) -> bool:
    """判断错误是否由迁移重复创建已存在的数据库对象导致。"""
    duplicate_indicators = [
        # SQLite: "duplicate column name: xxx"
        "duplicate column name",
        # SQLAlchemy / 其它方言常见表达
        "duplicatecolumn",
        "column already exists",
        "table already exists",
        "index already exists",
        "already exists",
    ]
    err_lower = err_msg.lower()
    return any(indicator in err_lower for indicator in duplicate_indicators)


def _describe_schema_drift(db_name: str, db_path: str) -> dict[str, list[str]]:
    """检查数据库结构与当前模型的表/列差异。

    这里故意只做保守的表/列存在性检查。类型、约束、索引等复杂变化
    必须经 Alembic migration 表达，不能靠启动期自愈猜测。
    """
    empty = {
        "missing_tables": [],
        "missing_columns": [],
        "extra_tables": [],
        "extra_columns": [],
    }
    if not db_path or not os.path.exists(db_path):
        return empty

    target_metadata = _get_target_metadata(db_name)
    if target_metadata is None:
        return empty

    from sqlalchemy import create_engine, inspect as sa_inspect
    from sqlalchemy.pool import NullPool

    normalized_path = db_path.replace("\\", "/")
    db_url = f"sqlite:///{normalized_path}"
    engine = create_engine(db_url, poolclass=NullPool)
    try:
        inspector = sa_inspect(engine)
        existing_tables = set(inspector.get_table_names())

        # Model 中定义的业务表名集合
        model_table_names = {
            name for name in target_metadata.tables
            if not _is_internal_table(name)
        }

        drift = {key: [] for key in empty}

        # 1) 缺表：Model 有但 DB 没有
        for table_name in model_table_names:
            if table_name not in existing_tables:
                drift["missing_tables"].append(table_name)

        # 2) 缺列：Model 列在 DB 中不存在
        for table_name, table in target_metadata.tables.items():
            if _is_internal_table(table_name):
                continue
            if table_name not in existing_tables:
                continue
            existing_col_names = {c["name"] for c in inspector.get_columns(table_name)}
            for col in table.columns:
                if col.name not in existing_col_names:
                    drift["missing_columns"].append(f"{table_name}.{col.name}")

        # 3) 多余表：DB 有业务表但 Model 没定义
        for table_name in existing_tables:
            if _is_internal_table(table_name):
                continue
            if table_name not in model_table_names:
                drift["extra_tables"].append(table_name)

        # 4) 多余列：DB 列在 Model 中不存在
        for table_name, table in target_metadata.tables.items():
            if _is_internal_table(table_name):
                continue
            if table_name not in existing_tables:
                continue
            existing_col_names = {c["name"] for c in inspector.get_columns(table_name)}
            model_col_names = {col.name for col in table.columns}
            ghost_cols = existing_col_names - model_col_names
            if ghost_cols:
                drift["extra_columns"].extend(f"{table_name}.{col}" for col in ghost_cols)

        return {key: sorted(values) for key, values in drift.items()}
    finally:
        engine.dispose()


def _has_schema_drift(db_name: str, db_path: str) -> bool:
    drift = _describe_schema_drift(db_name, db_path)
    return any(drift.values())


def _has_missing_model_objects(drift: dict[str, list[str]]) -> bool:
    return bool(drift.get("missing_tables") or drift.get("missing_columns"))


def _format_schema_drift(drift: dict[str, list[str]], *, limit: int = 12) -> str:
    labels = {
        "missing_tables": "缺表",
        "missing_columns": "缺列",
        "extra_tables": "多余表",
        "extra_columns": "多余列",
    }
    parts = []
    for key, label in labels.items():
        values = drift.get(key) or []
        if not values:
            continue
        shown = values[:limit]
        suffix = "" if len(values) <= limit else f" 等 {len(values)} 项"
        parts.append(f"{label}: {', '.join(shown)}{suffix}")
    return "；".join(parts) if parts else "无表/列级差异"


# ============================================================
#遗留版本自愈
# ============================================================

def _heal_orphan_revision(db_name: str, db_path: str, base_dir: str) -> None:
    """
   遗留版本自愈：当 DB 中记录的 revision 在迁移文件链中不存在时触发
    （最常见原因：管理员重置了迁移，开发者的 DB 停在了旧版本号）。

    自愈策略（默认只做非破坏性增量，保证应用能继续启动）：
      1. 扫描 Model 元数据，补全所有缺失的表（整表创建）。
      2. 扫描已存在表的列，补全所有缺失的列（batch_alter）。
      3. 默认保留 DB 中存在但 Model 未定义的幽灵列/表。
      4. 仅在 SPARKARC_AUTO_MIGRATE_ALLOW_DROPS=1 时清理幽灵列/表。
      5. 清除孤儿版本号，stamp 到当前迁移 head。

    前提假设：只要站长没有手动修改数据库结构或迁移文件，
    此函数即可保证应用正常启动，无需任何人工干预。
    """
    from sqlalchemy import create_engine, inspect as sa_inspect
    from sqlalchemy.pool import NullPool
    from alembic.runtime.migration import MigrationContext
    from alembic.operations import Operations

    logger.warning(f"⚕️  [{db_name}] Orphaned revision detected, starting structural self-heal...")

    target_metadata = _get_target_metadata(db_name)
    if target_metadata is None:
        raise RuntimeError(
            f"[{db_name}] Unable to load model metadata, cannot auto-fix orphaned revision. "
            "Please check if model imports are working correctly."
        )

    # 规范化为 SQLite URL（处理 Windows 反斜杠）
    normalized_path = db_path.replace("\\", "/")
    db_url = f"sqlite:///{normalized_path}"
    engine = create_engine(db_url, poolclass=NullPool)

    added_tables = []
    added_columns = []
    dropped_columns = []
    dropped_tables = []
    kept_columns = []
    kept_tables = []
    allow_drops = os.environ.get("SPARKARC_AUTO_MIGRATE_ALLOW_DROPS") == "1"
    # 内部表，跳过不处理
    SKIP_NAMES = {"alembic_version"}

    try:
        # ── 第一轮：补全缺失的整张表 ───────────────────────────
        inspector = sa_inspect(engine)
        existing_tables = set(inspector.get_table_names())

        for table_name, table in target_metadata.tables.items():
            if table_name in SKIP_NAMES or table_name.startswith("_alembic_tmp_"):
                continue
            if table_name not in existing_tables:
                table.create(engine)
                added_tables.append(table_name)
                logger.info(f"   ➕ Creating missing table: {table_name}")

        # ── 第二轮：补全缺失的列（重新 inspect，反映第一轮刚建的表）──
        inspector = sa_inspect(engine)
        existing_tables = set(inspector.get_table_names())

        with engine.connect() as conn:
            migration_ctx = MigrationContext.configure(
                conn, opts={"render_as_batch": True}
            )
            op_obj = Operations(migration_ctx)

            for table_name, table in target_metadata.tables.items():
                if table_name in SKIP_NAMES or table_name.startswith("_alembic_tmp_"):
                    continue
                if table_name not in existing_tables:
                    continue  # 第一轮刚建的表已含全部列

                existing_col_names = {c["name"] for c in inspector.get_columns(table_name)}
                missing_cols = [col for col in table.columns if col.name not in existing_col_names]

                if not missing_cols:
                    continue

                with op_obj.batch_alter_table(table_name) as batch_op:
                    for col in missing_cols:
                        # SQLite 不允许向含有数据的表添加 NOT NULL 且无默认值的列，
                        # 此时以 nullable 方式安全添加（现有行该列为 NULL，业务层可接受）
                        nullable = col.nullable
                        server_default = col.server_default

                        if not nullable and server_default is None and col.default is None:
                            logger.warning(
                                f"   \u26a0\ufe0f {table_name}.{col.name}: NOT NULL with no default value, "
                                f"adding as nullable during self-heal (existing rows will be NULL)"
                            )
                            nullable = True

                        batch_op.add_column(
                            sa.Column(col.name, col.type, nullable=nullable, server_default=server_default)
                        )
                        added_columns.append(f"{table_name}.{col.name}")
                        logger.info(f"   ➕ Filling missing column: {table_name}.{col.name}")

            conn.commit()

        # ── 第三轮：清理幽灵列（DB 有但 Model 没定义的列）───────────
        # 重新 inspect 以反映前两轮的变更
        inspector = sa_inspect(engine)

        with engine.connect() as conn:
            migration_ctx = MigrationContext.configure(
                conn, opts={"render_as_batch": True}
            )
            op_obj = Operations(migration_ctx)

            for table_name, table in target_metadata.tables.items():
                if table_name in SKIP_NAMES or table_name.startswith("_alembic_tmp_"):
                    continue
                if table_name not in existing_tables:
                    continue

                existing_col_names = {c["name"] for c in inspector.get_columns(table_name)}
                model_col_names = {col.name for col in table.columns}
                ghost_cols = existing_col_names - model_col_names

                if not ghost_cols:
                    continue

                if not allow_drops:
                    kept_columns.extend(f"{table_name}.{col_name}" for col_name in sorted(ghost_cols))
                    logger.warning(
                        f"   ⚠️ Retaining ghost columns: "
                        f"{', '.join(f'{table_name}.{col_name}' for col_name in sorted(ghost_cols))} "
                        f"(set SPARKARC_AUTO_MIGRATE_ALLOW_DROPS=1 to remove them)"
                    )
                    continue

                with op_obj.batch_alter_table(table_name) as batch_op:
                    for col_name in ghost_cols:
                        batch_op.drop_column(col_name)
                        dropped_columns.append(f"{table_name}.{col_name}")
                        logger.info(f"   🗑️ Dropping ghost column: {table_name}.{col_name}")

            conn.commit()

        # ── 第四轮：清理幽灵表（DB 有业务表但 Model 没定义）────────
        inspector = sa_inspect(engine)
        existing_tables_now = set(inspector.get_table_names())
        model_table_names = {
            name for name in target_metadata.tables
            if not _is_internal_table(name)
        }
        ghost_tables = existing_tables_now - model_table_names - {"alembic_version", "sqlite_sequence"}
        ghost_tables = {t for t in ghost_tables if not t.startswith("_alembic_tmp_")}

        if ghost_tables and not allow_drops:
            kept_tables.extend(sorted(ghost_tables))
            logger.warning(
                f"   ⚠️ Retaining ghost tables: {', '.join(sorted(ghost_tables))} "
                f"(set SPARKARC_AUTO_MIGRATE_ALLOW_DROPS=1 to remove them)"
            )
        else:
            for table_name in ghost_tables:
                with engine.connect() as conn:
                    conn.execute(sa.text(f'DROP TABLE IF EXISTS "{table_name}"'))
                    conn.commit()
                dropped_tables.append(table_name)
                logger.info(f"   🗑️ Dropping ghost table: {table_name}")

        # ── 清除孤儿版本号，stamp 到当前 head ──────────────────
        _stamp_head(db_name, db_path, base_dir)

        # ── 汇报自愈结果 ────────────────────────────────────────
        if added_tables or added_columns or dropped_columns or dropped_tables or kept_columns or kept_tables:
            logger.info(f"✅ [{db_name}] Structural self-heal complete.")
            if added_tables:
                logger.info(f"   Tables created: {', '.join(added_tables)}")
            if added_columns:
                logger.info(f"   Columns filled: {', '.join(added_columns)}")
            if dropped_columns:
                logger.info(f"   Ghost columns dropped: {', '.join(dropped_columns)}")
            if dropped_tables:
                logger.info(f"   Ghost tables dropped: {', '.join(dropped_tables)}")
            if kept_columns:
                logger.info(f"   Ghost columns retained: {', '.join(kept_columns)}")
            if kept_tables:
                logger.info(f"   Ghost tables retained: {', '.join(kept_tables)}")
        else:
            logger.info(f"✅ [{db_name}] DB structure fully consistent with model, version number updated.")

    finally:
        engine.dispose()


# ============================================================
# 核心升级入口
# ============================================================

def run_db_upgrade(db_name: str, base_dir: str) -> None:
    """对指定数据库执行 upgrade head（进程内调用）。"""
    database_url = get_database_url(db_name)
    if not database_url.startswith("sqlite:"):
        from core.database_config import ensure_postgres_database, shared_postgres_url

        maintenance_url = shared_postgres_url()
        if maintenance_url:
            ensure_postgres_database(database_url, maintenance_url)
        head_rev = _get_head_revision(base_dir, db_name)
        if not head_rev:
            logger.warning(f"⚠️ [{db_name}] No migration scripts detected (head is empty). Skipping auto-upgrade.")
            return
        logger.info(f"🔄 Upgrading [{db_name}] PostgreSQL database to head.")
        original_cwd = os.getcwd()
        try:
            os.chdir(base_dir)
            alembic_cfg = _build_alembic_config(base_dir, db_name)
            command.upgrade(alembic_cfg, "head")
            logger.info(f"✅ [{db_name}] PostgreSQL upgrade completed.")
        finally:
            try:
                os.chdir(original_cwd)
            except Exception:
                pass
        return

    db_path = _get_db_path(db_name)

    # 1) 快速检查：已是 head 直接跳过
    current_rev = _get_current_db_revision(db_path)
    head_rev = _get_head_revision(base_dir, db_name)

    if not head_rev:
        logger.warning(f"⚠️ [{db_name}] No migration scripts detected (head is empty). Skipping auto-upgrade.")
        return

    if current_rev and current_rev == head_rev:
        drift = _describe_schema_drift(db_name, db_path)
        if _has_missing_model_objects(drift):
            if os.environ.get("SPARKARC_AUTO_MIGRATE_REPAIR_HEAD_DRIFT") == "1":
                logger.warning(
                    f"⚠️ [{db_name}] Version is already head ({current_rev}), but missing objects detected; "
                    f"running emergency self-heal as requested by env var: {_format_schema_drift(drift)}"
                )
                _heal_orphan_revision(db_name, db_path, base_dir)
                return
            raise RuntimeError(
                f"[{db_name}] alembic_version 已是 head ({current_rev})，"
                f"但数据库结构缺少当前模型需要的对象: {_format_schema_drift(drift)}。"
                "这通常表示模型变更没有提交迁移，或开发机真实 DB 曾被自愈/手工修改污染。"
                "请先运行 `python gen_migration.py` 生成并提交迁移；"
                "若这是部署端救急且确认可按模型补表/补列，"
                "可临时设置 SPARKARC_AUTO_MIGRATE_REPAIR_HEAD_DRIFT=1。"
            )

        if drift.get("extra_tables") or drift.get("extra_columns"):
            logger.warning(
                f"⚠️ [{db_name}] Version is already head ({current_rev}), but DB contains extra structures not in model; "
                f"retaining and continuing startup: {_format_schema_drift(drift)}"
            )
            return

        logger.info(f"✨ [{db_name}] Database is up to date ({current_rev}). Skipping auto-upgrade.")
        return

    # 旧库未纳管：有业务表但没有版本号
    if current_rev is None and _has_user_tables(db_path):
        logger.warning(
            f"⚠️ [{db_name}] Unmanaged legacy database detected (has user tables but no version), "
            f"running structural self-heal to align to head."
        )
        _heal_orphan_revision(db_name, db_path, base_dir)
        return

    logger.info(f"🔄 Upgrading [{db_name}] database: {current_rev} -> {head_rev}")

    # 2) 执行升级
    original_cwd = os.getcwd()
    try:
        os.chdir(base_dir)
        alembic_cfg = _build_alembic_config(base_dir, db_name)
        command.upgrade(alembic_cfg, "head")
        logger.info(f"✅ [{db_name}] Upgrade completed.")
    except Exception as e:
        err_msg = str(e)
        if "Can't locate revision identified by" in err_msg:
            #遗留版本：迁移链被重置导致 DB 中记录的 revision 在迁移文件里找不到。
            # 触发结构自愈，无需人工干预。
            logger.warning(f"⚠️  [{db_name}] Migration chain broken (migration may have been reset): {err_msg}")
            # 先还原 CWD，再进入自愈（自愈函数内部自管 CWD）
            os.chdir(original_cwd)
            _heal_orphan_revision(db_name, db_path, base_dir)
        elif _is_missing_object_error(err_msg):
            # 迁移引用了不存在的列/表（如幽灵列残留导致 drop_column 失败），
            # 降级到结构自愈，重建 DB 结构与 Model 的一致性。
            logger.warning(
                f"⚠️  [{db_name}] Migration references a non-existent database object, "
                f"triggering structural self-heal: {err_msg}"
            )
            os.chdir(original_cwd)
            _heal_orphan_revision(db_name, db_path, base_dir)
        elif _is_duplicate_object_error(err_msg):
            # 迁移试图创建已存在的列/表。若当前 DB 已满足模型表/列结构，
            # 说明只是版本号落后，直接 stamp 到 head；否则仍交给结构自愈补齐缺口。
            os.chdir(original_cwd)
            drift = _describe_schema_drift(db_name, db_path)
            if _has_missing_model_objects(drift):
                logger.warning(
                    f"⚠️  [{db_name}] Migration tried to create an existing database object, "
                    f"but model-required objects are still missing; triggering structural self-heal: "
                    f"{_format_schema_drift(drift)}; original error: {err_msg}"
                )
                _heal_orphan_revision(db_name, db_path, base_dir)
            else:
                if drift.get("extra_tables") or drift.get("extra_columns"):
                    logger.warning(
                        f"⚠️  [{db_name}] Migration tried to create an existing database object; "
                        f"DB has no missing model objects but contains extra structures, retaining them and "
                        f"stamping version to head: {_format_schema_drift(drift)}; original error: {err_msg}"
                    )
                else:
                    logger.warning(
                        f"⚠️  [{db_name}] Migration tried to create an existing database object, "
                        f"but DB structure already matches the current model; stamping version to head: {err_msg}"
                    )
                _stamp_head(db_name, db_path, base_dir)
        else:
            logger.error(f"❌ [{db_name}] Upgrade failed: {e}")
            raise e
    finally:
        # 确保 CWD 始终复原
        try:
            os.chdir(original_cwd)
        except Exception:
            pass


def run_auto_migrations():
    """
    启动时自动运行 Alembic 迁移。
    目标：把迁移放在最早阶段完成，避免后续模块初始化时持锁或访问旧结构。
    """
    base_dir = BASE_DIR
    alembic_ini_path = os.path.join(base_dir, "alembic.ini")

    if not os.path.exists(alembic_ini_path):
        logger.warning(f"⚠️ Alembic config file not found: {alembic_ini_path}, skipping auto-migration.")
        return

    logger.info("🛠️  Checking database migration status...")

    try:
        # 1) 迁移 Users 数据库
        run_db_upgrade("users", base_dir)

        # 2) 迁移 LLM 数据库
        run_db_upgrade("llm", base_dir)

    except Exception as e:
        # 启动期迁移失败直接阻断启动，避免运行在不一致的结构上
        logger.error(f"❌ Auto-migration process encountered an error: {e}")
        raise e

