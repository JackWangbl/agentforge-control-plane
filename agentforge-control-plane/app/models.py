from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class TenantOwnedMixin:
    tenant_id: Mapped[int] = mapped_column(Integer, default=1, index=True)
    owner_id: Mapped[Optional[int]] = mapped_column(Integer, default=None, index=True)


class Tenant(Base, TimestampMixin):
    __tablename__ = "tenants"
    id: Mapped[int] = mapped_column(primary_key=True)
    slug: Mapped[str] = mapped_column(String(60), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(80))
    description: Mapped[str] = mapped_column(String(200), default="")
    status: Mapped[str] = mapped_column(String(24), default="active")


class User(Base, TimestampMixin):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(Integer, index=True)
    username: Mapped[str] = mapped_column(String(60), unique=True, index=True)
    display_name: Mapped[str] = mapped_column(String(80), default="")
    password_hash: Mapped[str] = mapped_column(String(200), default="")
    role_id: Mapped[Optional[int]] = mapped_column(Integer, default=None, index=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)


class AuthToken(Base):
    __tablename__ = "auth_tokens"
    id: Mapped[int] = mapped_column(primary_key=True)
    token: Mapped[str] = mapped_column(String(80), unique=True, index=True)
    user_id: Mapped[int] = mapped_column(Integer, index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class Agent(Base, TimestampMixin, TenantOwnedMixin):
    __tablename__ = "agents"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(80), unique=True, index=True)
    description: Mapped[str] = mapped_column(String(255), default="")
    model_name: Mapped[str] = mapped_column(String(120))
    status: Mapped[str] = mapped_column(String(24), default="published")
    version: Mapped[str] = mapped_column(String(20), default="v1.0.0")
    system_prompt: Mapped[str] = mapped_column(Text, default="")
    skill_ids: Mapped[list[int]] = mapped_column(JSON, default=list)
    mcp_ids: Mapped[list[int]] = mapped_column(JSON, default=list)
    sandbox_id: Mapped[Optional[int]] = mapped_column(Integer, index=True, default=None)
    workspace: Mapped[str] = mapped_column(String(255), default="")
    success_rate: Mapped[float] = mapped_column(Float, default=0)


class Conversation(Base, TimestampMixin, TenantOwnedMixin):
    __tablename__ = "conversations"
    id: Mapped[int] = mapped_column(primary_key=True)
    session_id: Mapped[str] = mapped_column(String(80), unique=True, index=True)
    user_id: Mapped[str] = mapped_column(String(80), index=True)
    agent_id: Mapped[Optional[int]] = mapped_column(Integer, index=True, default=None)
    agent_name: Mapped[str] = mapped_column(String(80), index=True)
    title: Mapped[str] = mapped_column(String(200))
    status: Mapped[str] = mapped_column(String(24), default="completed")
    message_count: Mapped[int] = mapped_column(Integer, default=0)
    total_tokens: Mapped[int] = mapped_column(Integer, default=0)
    latency_ms: Mapped[int] = mapped_column(Integer, default=0)
    channel: Mapped[str] = mapped_column(String(32), default="API")


class McpServer(Base, TimestampMixin, TenantOwnedMixin):
    __tablename__ = "mcp_servers"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100), unique=True)
    transport: Mapped[str] = mapped_column(String(30))
    endpoint: Mapped[str] = mapped_column(String(500))
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    tools_count: Mapped[int] = mapped_column(Integer, default=0)
    config: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)


class Skill(Base, TimestampMixin, TenantOwnedMixin):
    __tablename__ = "skills"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100), unique=True)
    description: Mapped[str] = mapped_column(String(300), default="")
    source: Mapped[str] = mapped_column(String(500), default="manual")
    version: Mapped[str] = mapped_column(String(20), default="1.0.0")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    instruction: Mapped[str] = mapped_column(Text, default="")


class ModelConfig(Base, TimestampMixin, TenantOwnedMixin):
    __tablename__ = "model_configs"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100), unique=True)
    provider: Mapped[str] = mapped_column(String(60))
    model_id: Mapped[str] = mapped_column(String(160))
    base_url: Mapped[str] = mapped_column(String(500), default="")
    api_key_ref: Mapped[str] = mapped_column(String(160), default="")
    api_key: Mapped[str] = mapped_column(Text, default="")
    temperature: Mapped[float] = mapped_column(Float, default=0.2)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)


class Workflow(Base, TimestampMixin, TenantOwnedMixin):
    __tablename__ = "workflows"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100), unique=True)
    description: Mapped[str] = mapped_column(String(300), default="")
    status: Mapped[str] = mapped_column(String(24), default="draft")
    graph: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)


class SandboxPolicy(Base, TimestampMixin, TenantOwnedMixin):
    __tablename__ = "sandbox_policies"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100), unique=True)
    runtime: Mapped[str] = mapped_column(String(60), default="python:3.11")
    cpu_limit: Mapped[str] = mapped_column(String(20), default="1 vCPU")
    memory_limit: Mapped[str] = mapped_column(String(20), default="1 GiB")
    timeout_seconds: Mapped[int] = mapped_column(Integer, default=60)
    network_mode: Mapped[str] = mapped_column(String(30), default="deny")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)


class Role(Base, TimestampMixin, TenantOwnedMixin):
    __tablename__ = "roles"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(60), unique=True)
    description: Mapped[str] = mapped_column(String(200), default="")
    permissions: Mapped[list[str]] = mapped_column(JSON, default=list)
    user_count: Mapped[int] = mapped_column(Integer, default=0)


class Dataset(Base, TimestampMixin, TenantOwnedMixin):
    __tablename__ = "datasets"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120), index=True)
    description: Mapped[str] = mapped_column(String(300), default="")
    source_name: Mapped[str] = mapped_column(String(255), default="")
    case_count: Mapped[int] = mapped_column(Integer, default=0)


class DatasetCase(Base, TimestampMixin, TenantOwnedMixin):
    __tablename__ = "dataset_cases"
    id: Mapped[int] = mapped_column(primary_key=True)
    dataset_id: Mapped[int] = mapped_column(Integer, index=True)
    case_key: Mapped[str] = mapped_column(String(80), default="", index=True)
    input: Mapped[str] = mapped_column(Text, default="")
    expected: Mapped[str] = mapped_column(Text, default="")
    tags: Mapped[list[str]] = mapped_column(JSON, default=list)
    extra: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)


class EvaluationRun(Base, TimestampMixin, TenantOwnedMixin):
    __tablename__ = "evaluation_runs"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    dataset: Mapped[str] = mapped_column(String(120), default="")
    agent_name: Mapped[str] = mapped_column(String(80), default="")
    dataset_id: Mapped[Optional[int]] = mapped_column(Integer, index=True, default=None)
    agent_id: Mapped[Optional[int]] = mapped_column(Integer, index=True, default=None)
    judge_model_id: Mapped[Optional[int]] = mapped_column(Integer, default=None)
    mode: Mapped[str] = mapped_column(String(24), default="offline")
    scorer: Mapped[str] = mapped_column(String(24), default="contains")
    status: Mapped[str] = mapped_column(String(24), default="queued")
    score: Mapped[float] = mapped_column(Float, default=0)
    cases: Mapped[int] = mapped_column(Integer, default=0)
    case_ids: Mapped[list[int]] = mapped_column(JSON, default=list)
    total: Mapped[int] = mapped_column(Integer, default=0)
    passed: Mapped[int] = mapped_column(Integer, default=0)
    failed: Mapped[int] = mapped_column(Integer, default=0)
    skipped: Mapped[int] = mapped_column(Integer, default=0)
    avg_latency_ms: Mapped[int] = mapped_column(Integer, default=0)
    total_tokens: Mapped[int] = mapped_column(Integer, default=0)
    error_message: Mapped[str] = mapped_column(Text, default="")
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime, default=None)
    finished_at: Mapped[Optional[datetime]] = mapped_column(DateTime, default=None)


class EvaluationResult(Base, TimestampMixin, TenantOwnedMixin):
    __tablename__ = "evaluation_results"
    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int] = mapped_column(Integer, index=True)
    case_id: Mapped[Optional[int]] = mapped_column(Integer, index=True, default=None)
    case_key: Mapped[str] = mapped_column(String(80), default="")
    status: Mapped[str] = mapped_column(String(24), default="failed")
    score: Mapped[float] = mapped_column(Float, default=0)
    input: Mapped[str] = mapped_column(Text, default="")
    expected: Mapped[str] = mapped_column(Text, default="")
    actual: Mapped[str] = mapped_column(Text, default="")
    reason: Mapped[str] = mapped_column(Text, default="")
    latency_ms: Mapped[int] = mapped_column(Integer, default=0)
    tokens: Mapped[int] = mapped_column(Integer, default=0)
    trace_id: Mapped[str] = mapped_column(String(80), default="")
    session_id: Mapped[str] = mapped_column(String(80), default="")
    error: Mapped[str] = mapped_column(Text, default="")


class Trace(Base, TenantOwnedMixin):
    __tablename__ = "traces"
    id: Mapped[int] = mapped_column(primary_key=True)
    trace_id: Mapped[str] = mapped_column(String(80), unique=True, index=True)
    session_id: Mapped[str] = mapped_column(String(80), index=True)
    agent_id: Mapped[Optional[int]] = mapped_column(Integer, index=True, default=None)
    agent_name: Mapped[str] = mapped_column(String(80), index=True)
    operation: Mapped[str] = mapped_column(String(120))
    status: Mapped[str] = mapped_column(String(24), default="ok")
    duration_ms: Mapped[int] = mapped_column(Integer)
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    spans: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    langfuse_url: Mapped[str] = mapped_column(String(500), default="")
    started_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class ChatMessage(Base, TimestampMixin, TenantOwnedMixin):
    __tablename__ = "chat_messages"
    id: Mapped[int] = mapped_column(primary_key=True)
    session_id: Mapped[str] = mapped_column(String(80), index=True)
    agent_id: Mapped[Optional[int]] = mapped_column(Integer, index=True, default=None)
    role: Mapped[str] = mapped_column(String(20))
    content: Mapped[str] = mapped_column(Text, default="")
    agent_name: Mapped[str] = mapped_column(String(80), default="")

