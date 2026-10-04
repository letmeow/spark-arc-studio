"""Shared database migration specifications.

Keep every Alembic entrypoint on the same database paths, metadata objects,
and version directories. Divergence here is exactly how a local runtime DB and
an Alembic migration target quietly become two different files.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


BASE_DIR = Path(__file__).resolve().parents[1]


@dataclass(frozen=True)
class MigrationDbSpec:
    name: str
    env_key: str
    default_relative_path: str
    version_subdir: str


DB_SPECS: dict[str, MigrationDbSpec] = {
    "users": MigrationDbSpec(
        name="users",
        env_key="SPARKARC_ALEMBIC_USERS_DB",
        default_relative_path="data/users.db",
        version_subdir="users",
    ),
    "llm": MigrationDbSpec(
        name="llm",
        env_key="SPARKARC_ALEMBIC_LLM_DB",
        default_relative_path="llm/agen_matchbox/llm_config.db",
        version_subdir="llm",
    ),
}


def iter_db_names() -> Iterable[str]:
    return DB_SPECS.keys()


def get_db_spec(db_name: str) -> MigrationDbSpec:
    try:
        return DB_SPECS[db_name]
    except KeyError as exc:
        valid = ", ".join(DB_SPECS)
        raise ValueError(f"Unknown migration database '{db_name}'. Valid values: {valid}") from exc


def _resolve_path(raw_path: str | os.PathLike[str]) -> Path:
    path = Path(raw_path).expanduser()
    if not path.is_absolute():
        path = BASE_DIR / path
    return path.resolve()


def get_db_path(db_name: str) -> Path:
    """Return the actual runtime DB file for a migration branch.

    Explicit SPARKARC_ALEMBIC_* overrides win. For the LLM DB, otherwise honor
    AGENT_MATCHBOX_HOME via the matchbox path helper so startup migration and
    runtime manager operate on the same file.
    """

    spec = get_db_spec(db_name)
    override = (os.environ.get(spec.env_key) or "").strip()
    if override:
        return _resolve_path(override)

    if db_name == "llm":
        try:
            from llm.agen_matchbox.paths import get_db_file_path

            return get_db_file_path("llm_config.db").resolve()
        except Exception:
            pass

    return _resolve_path(spec.default_relative_path)


def get_database_url(db_name: str) -> str:
    """返回迁移目标数据库 URL。

    默认仍使用 SQLite 文件；配置 PostgreSQL URL 后，Alembic 与运行时共用同一目标。
    """

    spec = get_db_spec(db_name)
    alembic_override = (os.environ.get(spec.env_key) or "").strip()
    if alembic_override:
        if "://" in alembic_override or alembic_override.startswith("sqlite:"):
            return alembic_override
        return sqlite_url(_resolve_path(alembic_override))

    from core.database_config import configured_database_url

    configured = configured_database_url(db_name)
    if configured:
        return configured
    return sqlite_url(get_db_path(db_name))


def get_version_dir(db_name: str, *, base_dir: Path | str | None = None) -> Path:
    spec = get_db_spec(db_name)
    root = Path(base_dir).resolve() if base_dir is not None else BASE_DIR
    return root / "alembic" / "versions" / spec.version_subdir


def sqlite_url(path: Path | str) -> str:
    return f"sqlite:///{Path(path).expanduser().resolve().as_posix()}"


def load_metadata(db_name: str):
    """Load SQLAlchemy Metadata for one migration branch."""

    if db_name == "users":
        from core.models import UserInfo

        return UserInfo.metadata

    if db_name == "llm":
        from llm.agen_matchbox.models import Base as LLMBase
        from core.search_provider_models import SearchProviderUserConfig  # noqa: F401

        return LLMBase.metadata

    get_db_spec(db_name)
    return None
