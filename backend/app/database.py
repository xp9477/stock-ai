from sqlalchemy import create_engine, event
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from .config import settings


class Base(DeclarativeBase):
    pass


engine = create_engine(
    f"sqlite:///{settings.db_path}",
    connect_args={"check_same_thread": False},
)


def enable_sqlite_foreign_keys(dbapi_connection, _connection_record) -> None:
    """Make declared evidence/trading foreign keys real on every SQLite connection."""
    cursor = dbapi_connection.cursor()
    try:
        cursor.execute("PRAGMA foreign_keys=ON")
    finally:
        cursor.close()


event.listen(engine, "connect", enable_sqlite_foreign_keys)
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
        ("runs", "result_json", "TEXT DEFAULT ''"),
        ("models", "is_official_strategy", "BOOLEAN DEFAULT 0"),
        ("canary_states", "status", "VARCHAR(16) DEFAULT 'active'"),
        ("agent_outputs", "model_id_snapshot", "VARCHAR(100) DEFAULT ''"),
        ("agent_outputs", "prompt_hash", "VARCHAR(64) DEFAULT ''"),
        ("agent_outputs", "input_hash", "VARCHAR(64) DEFAULT ''"),
        ("agent_outputs", "output_hash", "VARCHAR(64) DEFAULT ''"),
        ("agent_outputs", "config_snapshot_json", "TEXT DEFAULT '{}'"),
        ("shadow_benchmark_snapshots", "artifact_id",
         "INTEGER REFERENCES run_market_artifacts(id)"),
        ("execution_intents", "authorized_target_position_pct", "FLOAT DEFAULT 0"),
        ("execution_intents", "authorized_notional", "FLOAT DEFAULT 0"),
        ("execution_intents", "authorized_qty", "INTEGER DEFAULT 0"),
        ("execution_intents", "estimated_fee", "FLOAT DEFAULT 0"),
        ("execution_intents", "risk_snapshot_json", "TEXT DEFAULT '{}'"),
    ]
    with engine.connect() as conn:
        for table, column, ddl in migrations:
            cols = [r[1] for r in conn.execute(text(f"PRAGMA table_info({table})"))]
            if cols and column not in cols:
                conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}"))
        canary_cols = [
            r[1] for r in conn.execute(text("PRAGMA table_info(canary_states)"))
        ]
        if "status" in canary_cols and "stopped" in canary_cols:
            conn.execute(text(
                "UPDATE canary_states SET status='stopped' WHERE stopped=1"
            ))
        model_cols = [
            r[1] for r in conn.execute(text("PRAGMA table_info(models)"))
        ]
        if "is_official_strategy" in model_cols:
            conn.execute(text(
                "CREATE UNIQUE INDEX IF NOT EXISTS ux_models_one_official_strategy "
                "ON models(is_official_strategy) WHERE is_official_strategy = 1"
            ))
        from .models import install_sqlite_audit_triggers
        install_sqlite_audit_triggers(conn)
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
