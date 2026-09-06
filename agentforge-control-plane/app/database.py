from collections.abc import Generator
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy import MetaData, create_engine, inspect, text
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker
from sqlalchemy.pool import StaticPool

ROOT = Path(__file__).resolve().parents[1]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(ROOT / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )
    database_url: str = "sqlite:///./agentforge.db"
    langfuse_public_key: str = ""
    langfuse_secret_key: str = ""
    langfuse_host: str = "https://cloud.langfuse.com"
    langfuse_base_url: str = ""
    langfuse_project_id: str = ""
    agentscope_studio_url: str = ""


settings = Settings()
DATABASE_URL = settings.database_url.strip()
SQLITE_FILE = ROOT / "agentforge.db"


def _engine_kwargs(url: str) -> dict:
    if url.startswith("sqlite"):
        kwargs: dict = {"connect_args": {"check_same_thread": False}}
        if ":memory:" in url:
            kwargs["poolclass"] = StaticPool
        return kwargs
    return {"pool_pre_ping": True, "pool_recycle": 3600}


engine = create_engine(DATABASE_URL, **_engine_kwargs(DATABASE_URL))
SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)


class Base(DeclarativeBase):
    pass


def is_sqlite() -> bool:
    return engine.dialect.name == "sqlite"


def is_mysql() -> bool:
    return engine.dialect.name == "mysql"


def ensure_schema() -> None:
    inspector = inspect(engine)
    if "model_configs" not in inspector.get_table_names():
        return
    columns = {col["name"] for col in inspector.get_columns("model_configs")}
    if "api_key" not in columns:
        with engine.begin() as conn:
            if is_mysql():
                conn.execute(text("ALTER TABLE model_configs ADD COLUMN api_key TEXT NULL"))
            else:
                conn.execute(text("ALTER TABLE model_configs ADD COLUMN api_key TEXT DEFAULT ''"))
    if "agents" in inspector.get_table_names():
        agent_cols = {col["name"] for col in inspector.get_columns("agents")}
        with engine.begin() as conn:
            if "skill_ids" not in agent_cols:
                if is_mysql():
                    conn.execute(text("ALTER TABLE agents ADD COLUMN skill_ids JSON NULL"))
                else:
                    conn.execute(text("ALTER TABLE agents ADD COLUMN skill_ids TEXT DEFAULT '[]'"))
            if "mcp_ids" not in agent_cols:
                if is_mysql():
                    conn.execute(text("ALTER TABLE agents ADD COLUMN mcp_ids JSON NULL"))
                else:
                    conn.execute(text("ALTER TABLE agents ADD COLUMN mcp_ids TEXT DEFAULT '[]'"))
            if "workspace" not in agent_cols:
                if is_mysql():
                    conn.execute(text("ALTER TABLE agents ADD COLUMN workspace VARCHAR(255) NULL"))
                else:
                    conn.execute(text("ALTER TABLE agents ADD COLUMN workspace TEXT DEFAULT ''"))
            if "sandbox_id" not in agent_cols:
                conn.execute(text("ALTER TABLE agents ADD COLUMN sandbox_id INTEGER NULL"))
    if "conversations" in inspector.get_table_names():
        conversation_cols = {col["name"] for col in inspector.get_columns("conversations")}
        if "agent_id" not in conversation_cols:
            with engine.begin() as conn:
                conn.execute(text("ALTER TABLE conversations ADD COLUMN agent_id INTEGER NULL"))
    if "chat_messages" in inspector.get_table_names():
        message_cols = {col["name"] for col in inspector.get_columns("chat_messages")}
        if "agent_id" not in message_cols:
            with engine.begin() as conn:
                conn.execute(text("ALTER TABLE chat_messages ADD COLUMN agent_id INTEGER NULL"))
    if "traces" in inspector.get_table_names():
        trace_cols = {col["name"] for col in inspector.get_columns("traces")}
        with engine.begin() as conn:
            if "langfuse_url" not in trace_cols:
                if is_mysql():
                    conn.execute(text("ALTER TABLE traces ADD COLUMN langfuse_url VARCHAR(500) NULL"))
                else:
                    conn.execute(text("ALTER TABLE traces ADD COLUMN langfuse_url TEXT DEFAULT ''"))
            if "agent_id" not in trace_cols:
                conn.execute(text("ALTER TABLE traces ADD COLUMN agent_id INTEGER NULL"))
    if "experiments" in inspector.get_table_names():
        experiment_cols = {col["name"] for col in inspector.get_columns("experiments")}
        with engine.begin() as conn:
            if "last_compare" not in experiment_cols:
                if is_mysql():
                    conn.execute(text("ALTER TABLE experiments ADD COLUMN last_compare JSON NULL"))
                else:
                    conn.execute(text("ALTER TABLE experiments ADD COLUMN last_compare TEXT DEFAULT NULL"))
            if "assignment_strategy" not in experiment_cols:
                if is_mysql():
                    conn.execute(text("ALTER TABLE experiments ADD COLUMN assignment_strategy VARCHAR(32) NULL"))
                else:
                    conn.execute(text("ALTER TABLE experiments ADD COLUMN assignment_strategy TEXT DEFAULT 'session_hash'"))
                conn.execute(text("UPDATE experiments SET assignment_strategy = 'user_hash' WHERE assignment_unit = 'user' AND (assignment_strategy IS NULL OR assignment_strategy = '')"))
                conn.execute(text("UPDATE experiments SET assignment_strategy = 'session_hash' WHERE assignment_strategy IS NULL OR assignment_strategy = ''"))
    if "evaluation_runs" in inspector.get_table_names():
        eval_cols = {col["name"] for col in inspector.get_columns("evaluation_runs")}
        additions = {
            "dataset_id": ("INTEGER NULL", "INTEGER"),
            "agent_id": ("INTEGER NULL", "INTEGER"),
            "judge_model_id": ("INTEGER NULL", "INTEGER"),
            "mode": ("VARCHAR(24) NULL", "TEXT DEFAULT 'offline'"),
            "scorer": ("VARCHAR(24) NULL", "TEXT DEFAULT 'contains'"),
            "case_ids": ("JSON NULL", "TEXT DEFAULT '[]'"),
            "total": ("INTEGER NULL", "INTEGER DEFAULT 0"),
            "passed": ("INTEGER NULL", "INTEGER DEFAULT 0"),
            "failed": ("INTEGER NULL", "INTEGER DEFAULT 0"),
            "skipped": ("INTEGER NULL", "INTEGER DEFAULT 0"),
            "avg_latency_ms": ("INTEGER NULL", "INTEGER DEFAULT 0"),
            "total_tokens": ("INTEGER NULL", "INTEGER DEFAULT 0"),
            "error_message": ("TEXT NULL", "TEXT DEFAULT ''"),
            "started_at": ("DATETIME NULL", "DATETIME"),
            "finished_at": ("DATETIME NULL", "DATETIME"),
        }
        with engine.begin() as conn:
            for name, (mysql_type, sqlite_type) in additions.items():
                if name in eval_cols:
                    continue
                col_type = mysql_type if is_mysql() else sqlite_type
                conn.execute(text(f"ALTER TABLE evaluation_runs ADD COLUMN {name} {col_type}"))
    with engine.begin() as conn:
        conn.execute(text(
            "UPDATE model_configs SET api_key = api_key_ref, api_key_ref = '' "
            "WHERE api_key_ref LIKE 'sk-%' AND (api_key IS NULL OR api_key = '')"
        ))
        conn.execute(text(
            "UPDATE model_configs SET api_key_ref = '' WHERE api_key_ref LIKE 'sk-%'"
        ))
        conn.execute(text(
            "UPDATE model_configs SET model_id = 'deepseek-v4-flash' "
            "WHERE base_url LIKE '%api.deepseek.com%' "
            "AND model_id IN ('deepseek-v4', 'deepseek-v3', 'deepseek-chat')"
        ))
    _ensure_tenant_columns()


def _ensure_tenant_columns() -> None:
    inspector = inspect(engine)
    tables = {
        "agents", "mcp_servers", "skills", "model_configs", "workflows",
        "sandbox_policies", "roles", "datasets", "dataset_cases",
        "evaluation_runs", "evaluation_results", "experiments",
        "experiment_variants", "experiment_assignments", "experiment_events",
        "conversations",
        "chat_messages", "traces",
    }
    for table in tables:
        if table not in inspector.get_table_names():
            continue
        columns = {col["name"] for col in inspector.get_columns(table)}
        with engine.begin() as conn:
            if "tenant_id" not in columns:
                if is_mysql():
                    conn.execute(text(f"ALTER TABLE {table} ADD COLUMN tenant_id INTEGER NULL"))
                else:
                    conn.execute(text(f"ALTER TABLE {table} ADD COLUMN tenant_id INTEGER DEFAULT 1"))
            if "owner_id" not in columns:
                if is_mysql():
                    conn.execute(text(f"ALTER TABLE {table} ADD COLUMN owner_id INTEGER NULL"))
                else:
                    conn.execute(text(f"ALTER TABLE {table} ADD COLUMN owner_id INTEGER"))
            conn.execute(text(f"UPDATE {table} SET tenant_id = 1 WHERE tenant_id IS NULL"))


def copy_sqlite_if_mysql_empty() -> None:
    if not is_mysql() or not SQLITE_FILE.exists():
        return
    from sqlalchemy import select as sql_select
    from app.models import Agent

    with SessionLocal() as db:
        if db.scalar(sql_select(Agent.id).limit(1)):
            return

    sqlite_engine = create_engine(
        f"sqlite:///{SQLITE_FILE}",
        connect_args={"check_same_thread": False},
    )
    source_meta = MetaData()
    source_meta.reflect(bind=sqlite_engine)
    dest_meta = MetaData()
    dest_meta.reflect(bind=engine)
    with sqlite_engine.connect() as src, engine.begin() as dst:
        if is_mysql():
            dst.execute(text("SET FOREIGN_KEY_CHECKS=0"))
        for table in source_meta.sorted_tables:
            dest = dest_meta.tables.get(table.name)
            if dest is None:
                continue
            rows = [dict(row) for row in src.execute(table.select()).mappings()]
            if not rows:
                continue
            dst.execute(dest.insert(), rows)
        if is_mysql():
            dst.execute(text("SET FOREIGN_KEY_CHECKS=1"))
    sqlite_engine.dispose()


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
