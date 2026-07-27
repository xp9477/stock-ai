from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from .config import settings


class Base(DeclarativeBase):
    pass


engine = create_engine(
    f"sqlite:///{settings.db_path}",
    connect_args={"check_same_thread": False},
)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def _migrate():
    """轻量迁移:对已有库补充新增列(SQLite ALTER TABLE ADD COLUMN)。"""
    from sqlalchemy import text

    migrations = [
        ("watchlist", "source", "VARCHAR(10) DEFAULT 'manual'"),
        ("watchlist", "miss_count", "INTEGER DEFAULT 0"),
        ("watchlist", "select_reason", "TEXT DEFAULT ''"),
    ]
    with engine.connect() as conn:
        for table, column, ddl in migrations:
            cols = [r[1] for r in conn.execute(text(f"PRAGMA table_info({table})"))]
            if cols and column not in cols:
                conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}"))
        conn.commit()


def init_db():
    from . import models  # noqa: F401

    Base.metadata.create_all(engine)
    _migrate()

    from .seed import seed_models

    db = SessionLocal()
    try:
        seed_models(db)
    finally:
        db.close()
