"""独立数据库基础设施测试（从主项目迁移）。"""

from sqlalchemy import Column, Integer, MetaData, Table, select

from ..database import create_configured_engine


def test_memory_sqlite_uses_shared_connection_pool() -> None:
    engine = create_configured_engine("sqlite:///:memory:")
    metadata = MetaData()
    sample = Table("sample", metadata, Column("id", Integer, primary_key=True))

    try:
        metadata.create_all(engine)
        with engine.begin() as connection:
            connection.execute(sample.insert().values(id=1))
        with engine.connect() as connection:
            assert connection.execute(select(sample.c.id)).scalar_one() == 1
    finally:
        engine.dispose()
