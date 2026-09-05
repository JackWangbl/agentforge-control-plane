from contextlib import asynccontextmanager
from datetime import datetime
import json
import os
from pathlib import Path
from typing import Any, Optional
from uuid import uuid4

from fastapi import Body, Depends, FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import ValidationError
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, object_session

from app.access.auth import CurrentUser, get_current_user, require_permission
from app.access.kinds import ResourceKind
from app.access.scope import attach_access, kind_for, stamp_owner
from app.access.service import access_service
from app.database import Base, SessionLocal, copy_sqlite_if_mysql_empty, engine, ensure_schema, get_db
from app.models import Agent, ChatMessage, Conversation, Dataset, EvaluationRun, McpServer, ModelConfig, Role, SandboxPolicy, Skill, Trace, Workflow
from app.routers.auth import router as auth_router
from app.routers.evaluations import router as evaluation_router
from app.services.eval_runner import dump_run, start_eval_worker
from app.schemas import (
    AgentCreate,
    AgentUpdate,
    McpCreate,
    McpUpdate,
    ModelCreate,
    ModelUpdate,
    RoleCreate,
    RoleUpdate,
    SandboxCreate,
    SandboxUpdate,
    SkillCreate,
    SkillUpdate,
    WorkflowCreate,
    WorkflowUpdate,
    PlaygroundRun,
    ResourceStatusUpdate,
)
from app.services.agent_workspace import (
    ensure_workspace,
    ensure_workspaces,
    load_session,
    new_trace_id,
    persist_run,
    remove_workspace,
    workspace_status,
)
from app.services.langfuse_tracer import observability_status, public_trace_url
from app.services.studio_tracer import export_playground_to_studio
from app.seed import ensure_iam, purge_demo_observability_data, seed_database
from app.services.agentscope_adapter import complete_chat, initialize_agentscope
from app.services.tool_runtime import (
    agent_allows_tool,
    build_system_prompt,
    execute_tool,
    is_builtin_mcp,
    list_mcp_tools,
    normalize_id_list,
    openai_tools_for_mcps,
    parse_tool_arguments,
    persist_skill_markdown,
    purge_junk_and_seed_tools,
    selected_mcps,
    selected_skills,
    skill_instruction,
)


ROOT = Path(__file__).resolve().parent.parent
RESOURCE_MODELS = {
    "agents": Agent,
    "mcp": McpServer,
    "skills": Skill,
    "models": ModelConfig,
    "workflows": Workflow,
    "sandboxes": SandboxPolicy,
    "roles": Role,
    "traces": Trace,
}
RESOURCE_UPDATES = {
    "agents": (Agent, AgentUpdate),
    "mcp": (McpServer, McpUpdate),
    "skills": (Skill, SkillUpdate),
    "models": (ModelConfig, ModelUpdate),
    "workflows": (Workflow, WorkflowUpdate),
    "sandboxes": (SandboxPolicy, SandboxUpdate),
    "roles": (Role, RoleUpdate),
}
STATUS_RESOURCES = {
    "mcp": McpServer,
    "skills": Skill,
    "models": ModelConfig,
    "sandboxes": SandboxPolicy,
}


@asynccontextmanager
async def lifespan(_: FastAPI):
    Base.metadata.create_all(engine)
    ensure_schema()
    copy_sqlite_if_mysql_empty()
    with SessionLocal() as db:
        seed_database(db)
        ensure_iam(db)
        purge_demo_observability_data(db)
        purge_junk_and_seed_tools(db)
        ensure_iam(db)
        ensure_workspaces(list(db.scalars(select(Agent)).all()))
        db.commit()
    initialize_agentscope()
    start_eval_worker()
    yield


app = FastAPI(title="AgentForge Control Plane", version="0.1.0", lifespan=lifespan)
app.include_router(auth_router)
app.include_router(evaluation_router)
app.mount("/static", StaticFiles(directory=ROOT / "static"), name="static")


def dump(row: Any) -> dict[str, Any]:
    data = {c.name: getattr(row, c.name) for c in row.__table__.columns}
    if isinstance(row, ModelConfig):
        secret = data.get("api_key") or ""
        data["has_api_key"] = bool(secret)
        data["has_credential"] = model_has_credential(row)
        data["api_key"] = mask_secret(secret)
        ref = data.get("api_key_ref") or ""
        if ref.startswith("sk-"):
            data["api_key_ref"] = ""
    if isinstance(row, McpServer):
        tools = list_mcp_tools(row)
        data["tools"] = tools
        data["tools_count"] = len(tools) if tools else row.tools_count
        data["runnable"] = is_builtin_mcp(row)
    if isinstance(row, Skill):
        instruction = skill_instruction(row)
        data["instruction"] = instruction
        data["has_instruction"] = bool(instruction)
    if isinstance(row, Agent):
        data["skill_ids"] = normalize_id_list(data.get("skill_ids"))
        data["mcp_ids"] = normalize_id_list(data.get("mcp_ids"))
        data["system_prompt"] = row.system_prompt or ""
        data["bound_skills"] = []
        data["bound_mcps"] = []
        db = object_session(row)
        if db is not None:
            skills = selected_skills(row, db)
            mcps = selected_mcps(row, db)
            data["bound_skills"] = [{"id": item.id, "name": item.name} for item in skills]
            data["bound_mcps"] = [
                {"id": item.id, "name": item.name, "tools": list_mcp_tools(item)}
                for item in mcps
            ]
        data["workspace"] = row.workspace or ""
    if isinstance(row, Trace):
        data["spans"] = row.spans or []
        data["langfuse_url"] = public_trace_url(row.langfuse_url or "", row.trace_id or "")
    if isinstance(row, EvaluationRun):
        return dump_run(row)
    if isinstance(row, Dataset):
        data["description"] = row.description or ""
        data["source_name"] = row.source_name or ""
    return attach_access(data, row)


def mask_secret(value: str) -> str:
    if not value:
        return ""
    if len(value) <= 8:
        return "••••••••"
    return f"{value[:3]}••••{value[-4:]}"


def resolve_model_credential(model: ModelConfig) -> str:
    if model.api_key:
        return model.api_key
    if model.api_key_ref:
        return os.getenv(model.api_key_ref, "")
    return ""


def model_has_credential(model: ModelConfig) -> bool:
    if resolve_model_credential(model):
        return True
    return bool(model.base_url and not model.api_key and not model.api_key_ref)


def model_endpoint(model: ModelConfig) -> str:
    if model.base_url:
        return model.base_url.rstrip("/")
    provider = (model.provider or "").lower()
    if "dashscope" in provider:
        return "https://dashscope.aliyuncs.com/compatible-mode/v1"
    return "https://api.openai.com/v1"


def preview_chat_reply(agent: Agent, message: str, extra: str = "") -> str:
    duty = agent.description or "通用问答与任务处理"
    prompt = agent.system_prompt.strip() if agent.system_prompt else ""
    hint = f"\n\n我的设定：{prompt}" if prompt else ""
    addon = f"\n\n{extra}" if extra else ""
    return (
        f"我是「{agent.name}」，已经收到你的消息。\n\n"
        f"{message}\n\n"
        f"我的职责是{duty}。你可以继续往下说，我会按同一段对话来回复。{hint}{addon}"
    )


def debug_span(name: str, title: str, kind: str, status: str = "ok", duration_ms: int = 0, detail: str = "") -> dict[str, Any]:
    return {
        "name": name,
        "title": title,
        "kind": kind,
        "status": status,
        "duration_ms": duration_ms,
        "detail": (detail or "")[:240],
    }


def build_debug_spans(agent: Agent, model: ModelConfig, mode: str, tool_spans: list[dict[str, Any]], latency_ms: int, db: Session) -> list[dict[str, Any]]:
    skills = selected_skills(agent, db)
    mcps = selected_mcps(agent, db)
    tools = [tool for row in mcps for tool in list_mcp_tools(row)]
    spans = [
        debug_span("user.message", "接收用户消息", "input", duration_ms=2),
        debug_span("agent.resolve", f"解析 Agent · {agent.name}", "agent", duration_ms=8, detail=agent.system_prompt or agent.description or ""),
    ]
    if skills:
        spans.append(debug_span("skill.inject", f"注入 {len(skills)} 个技能", "skill", duration_ms=4, detail="、".join(item.name for item in skills)))
    else:
        spans.append(debug_span("skill.inject", "未关联技能", "skill", status="skip", detail="可在 Agent 编辑页勾选技能"))
    if tools:
        spans.append(debug_span("mcp.bind", f"关联 {len(tools)} 个工具", "mcp", duration_ms=4, detail="、".join(tool["name"] for tool in tools)))
    else:
        spans.append(debug_span("mcp.bind", "未关联工具", "mcp", status="skip", detail="可在 Agent 编辑页勾选 MCP"))
    model_detail = "预览模式：当前模型没有可用密钥" if mode == "preview" else model.model_id
    spans.append(debug_span(
        "model.chat",
        f"调用模型 · {model.name}",
        "llm",
        status="error" if mode == "error" else "ok",
        duration_ms=max(20, latency_ms - 40),
        detail=model_detail,
    ))
    spans.extend(tool_spans)
    spans.append(debug_span("reply.emit", "生成回复", "output", status="error" if mode == "error" else "ok", duration_ms=6))
    return spans


def generate_chat_reply(agent: Agent, model: ModelConfig, history: list[dict[str, Any]], db: Session) -> tuple[str, str, list[dict[str, Any]], dict[str, Any]]:
    credential = resolve_model_credential(model)
    last_user = next((item["content"] for item in reversed(history) if item.get("role") == "user"), "")
    system_prompt = build_system_prompt(agent, db)
    bound_mcps = selected_mcps(agent, db)
    tools = openai_tools_for_mcps(bound_mcps)
    if not credential:
        extra = "当前模型没有密钥，这是预览回复。已绑定的 Skill 和 MCP 工具会在配置密钥后由模型调用。"
        tool_spans: list[dict[str, Any]] = []
        if ("几点" in last_user or "时间" in last_user or "日期" in last_user) and agent_allows_tool(agent, db, "get_current_time"):
            extra = execute_tool("get_current_time", {})
            tool_spans.append(debug_span("mcp.get_current_time", "调用工具 get_current_time", "tool", duration_ms=6, detail=extra))
        return preview_chat_reply(agent, last_user, extra), "preview", tool_spans, {}
    working = [dict(item) for item in history]
    traces: list[dict[str, Any]] = []
    usage: dict[str, Any] = {}
    try:
        rounds = 8 if any((item.get("function") or {}).get("name", "").startswith("browser_") for item in tools) else 4
        for _ in range(rounds):
            result = complete_chat(
                model_id=model.model_id,
                base_url=model_endpoint(model),
                api_key=credential,
                temperature=model.temperature,
                system_prompt=system_prompt,
                messages=working,
                tools=tools or None,
            )
            if result.get("usage"):
                usage = result["usage"]
            calls = result.get("tool_calls") or []
            if not calls:
                reply = result.get("content") or preview_chat_reply(agent, last_user)
                return reply, "ready", traces, usage
            working.append({"role": "assistant", "content": result.get("content") or "", "tool_calls": calls})
            for call in calls:
                fn = call.get("function") or {}
                name = fn.get("name") or "unknown"
                args = parse_tool_arguments(fn.get("arguments"))
                allowed = agent_allows_tool(agent, db, name)
                output = execute_tool(name, args, db) if allowed else json.dumps({"error": f"Agent 未绑定工具 {name}"}, ensure_ascii=False)
                traces.append(debug_span(f"mcp.{name}", f"调用工具 {name}", "tool", status="ok" if allowed else "error", duration_ms=8, detail=output))
                working.append({"role": "tool", "tool_call_id": call.get("id") or name, "content": output})
        return working[-1].get("content") or preview_chat_reply(agent, last_user), "ready", traces, usage
    except Exception as exc:
        detail = str(exc)
        if credential:
            detail = detail.replace(credential, "****")
        return f"模型调用失败：{detail}\n\n{preview_chat_reply(agent, last_user)}", "error", traces, usage


@app.get("/", include_in_schema=False)
def index() -> FileResponse:
    return FileResponse(ROOT / "static" / "index.html")


@app.get("/api/health")
def health() -> dict[str, Any]:
    return {"status": "ok", "service": "agentforge-control-plane", **observability_status()}


@app.get("/api/observability")
def observability(user: CurrentUser = Depends(get_current_user)) -> dict[str, Any]:
    return observability_status()


@app.get("/api/dashboard")
def dashboard(user: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db)) -> dict[str, Any]:
    tenant = access_service.tenant_clause(Conversation, user)
    total = db.scalar(select(func.count()).select_from(Conversation).where(tenant)) or 0
    completed = db.scalar(select(func.count()).select_from(Conversation).where(tenant, Conversation.status == "completed")) or 0
    avg_latency = db.scalar(select(func.avg(Conversation.latency_ms)).where(tenant)) or 0
    tokens = db.scalar(select(func.sum(Conversation.total_tokens)).where(tenant)) or 0
    agents = access_service.list_rows(user, ResourceKind.AGENT, db) if user.has("agent:read") else []
    sessions = []
    if user.has("session:read"):
        sessions = list(db.scalars(select(Conversation).where(tenant).order_by(Conversation.updated_at.desc(), Conversation.id.desc()).limit(5)).all())
    return {
        "metrics": {"requests": total * 2568, "success_rate": round(completed / total * 100, 1) if total else 0, "avg_latency_ms": round(avg_latency or 0), "tokens": (tokens or 0) * 128},
        "agents": [dump(x) for x in sorted(agents, key=lambda item: item.success_rate or 0, reverse=True)],
        "recent_sessions": [dump(x) for x in sessions],
        "activity": [82, 110, 96, 138, 126, 164, 152, 189, 171, 204, 196, 236, 218, 248, 227, 263, 251, 284, 269, 302, 292, 326, 311, 348],
    }


@app.get("/api/sessions")
def sessions(agent_name: Optional[str] = None, user_id: Optional[str] = None, session_id: Optional[str] = None, status: Optional[str] = None, q: Optional[str] = None, limit: int = Query(50, le=200), user: CurrentUser = Depends(require_permission("session:read")), db: Session = Depends(get_db)) -> list[dict[str, Any]]:
    stmt = select(Conversation).where(access_service.tenant_clause(Conversation, user)).order_by(Conversation.updated_at.desc(), Conversation.id.desc()).limit(limit)
    if agent_name: stmt = stmt.where(Conversation.agent_name == agent_name)
    if user_id: stmt = stmt.where(Conversation.user_id.contains(user_id))
    if session_id: stmt = stmt.where(Conversation.session_id.contains(session_id))
    if status: stmt = stmt.where(Conversation.status == status)
    if q:
        message_match = select(ChatMessage.id).where(
            ChatMessage.session_id == Conversation.session_id,
            ChatMessage.content.contains(q),
        ).exists()
        stmt = stmt.where(or_(
            Conversation.title.contains(q),
            Conversation.user_id.contains(q),
            Conversation.session_id.contains(q),
            message_match,
        ))
    return [dump(x) for x in db.scalars(stmt).all()]


@app.get("/api/sessions/{session_id}")
def session_detail(session_id: str, user: CurrentUser = Depends(require_permission("session:read")), db: Session = Depends(get_db)) -> dict[str, Any]:
    row = db.scalar(select(Conversation).where(Conversation.session_id == session_id, access_service.tenant_clause(Conversation, user)))
    if row is None:
        raise HTTPException(404, "Session not found")
    data = dump(row)
    data["messages"] = [
        dump(item)
        for item in db.scalars(
            select(ChatMessage)
            .where(ChatMessage.session_id == session_id)
            .order_by(ChatMessage.created_at, ChatMessage.id)
        ).all()
    ]
    data["traces"] = [
        dump(item)
        for item in db.scalars(
            select(Trace)
            .where(Trace.session_id == session_id)
            .order_by(Trace.started_at.desc())
        ).all()
    ]
    return data


@app.get("/api/traces/{item_id}")
def get_trace_detail(item_id: str, user: CurrentUser = Depends(require_permission("trace:read")), db: Session = Depends(get_db)) -> dict[str, Any]:
    row = None
    if item_id.isdigit():
        row = db.get(Trace, int(item_id))
    if row is None:
        row = db.scalar(select(Trace).where(Trace.trace_id == item_id))
    if row is None or getattr(row, "tenant_id", user.tenant_id) not in (None, user.tenant_id):
        raise HTTPException(404, "Trace not found")
    data = dump(row)
    messages = []
    if row.session_id:
        messages = [
            {"id": item.id, "role": item.role, "content": item.content, "agent_name": item.agent_name, "created_at": item.created_at}
            for item in db.scalars(select(ChatMessage).where(ChatMessage.session_id == row.session_id).order_by(ChatMessage.id)).all()
        ]
    data["messages"] = messages
    return data


@app.get("/api/{resource}")
def list_resource(resource: str, user: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db)) -> list[dict[str, Any]]:
    if resource not in RESOURCE_MODELS:
        raise HTTPException(404, "Unknown resource")
    return [dump(x) for x in access_service.list_rows(user, kind_for(resource), db)]


@app.post("/api/mcp", status_code=201)
def create_mcp(payload: McpCreate, user: CurrentUser = Depends(require_permission("mcp:write")), db: Session = Depends(get_db)) -> dict[str, Any]:
    data = payload.model_dump()
    row = stamp_owner(McpServer(**data, tools_count=len((data.get("config") or {}).get("tools") or [])), user)
    if is_builtin_mcp(row):
        row.tools_count = len(list_mcp_tools(row))
        row.config = {**(row.config or {}), "kind": "builtin", "tools": list_mcp_tools(row)}
    db.add(row); db.commit(); db.refresh(row); return dump(row)


@app.post("/api/skills", status_code=201)
def create_skill(payload: SkillCreate, user: CurrentUser = Depends(require_permission("skill:write")), db: Session = Depends(get_db)) -> dict[str, Any]:
    row = stamp_owner(Skill(**payload.model_dump()), user)
    persist_skill_markdown(row)
    db.add(row); db.commit(); db.refresh(row); return dump(row)


@app.post("/api/models", status_code=201)
def create_model(payload: ModelCreate, user: CurrentUser = Depends(require_permission("model:write")), db: Session = Depends(get_db)) -> dict[str, Any]:
    row = stamp_owner(ModelConfig(**payload.model_dump()), user); db.add(row); db.commit(); db.refresh(row); return dump(row)


@app.post("/api/workflows", status_code=201)
def create_workflow(payload: WorkflowCreate, user: CurrentUser = Depends(require_permission("workflow:write")), db: Session = Depends(get_db)) -> dict[str, Any]:
    row = stamp_owner(Workflow(**payload.model_dump()), user); db.add(row); db.commit(); db.refresh(row); return dump(row)


@app.post("/api/agents", status_code=201)
def create_agent(payload: AgentCreate, user: CurrentUser = Depends(require_permission("agent:write")), db: Session = Depends(get_db)) -> dict[str, Any]:
    row = stamp_owner(Agent(**payload.model_dump()), user)
    db.add(row)
    db.commit()
    db.refresh(row)
    ensure_workspace(row)
    db.commit()
    db.refresh(row)
    return dump(row)


@app.get("/api/agents/{item_id}/workspace")
def get_agent_workspace(item_id: int, user: CurrentUser = Depends(require_permission("agent:read")), db: Session = Depends(get_db)) -> dict[str, Any]:
    agent = access_service.get_row(user, ResourceKind.AGENT, item_id, db)
    status = workspace_status(agent)
    db.commit()
    return status


@app.get("/api/agents/{item_id}/workspace/sessions/{session_id}")
def get_agent_workspace_session(item_id: int, session_id: str, user: CurrentUser = Depends(require_permission("agent:read")), db: Session = Depends(get_db)) -> dict[str, Any]:
    agent = access_service.get_row(user, ResourceKind.AGENT, item_id, db)
    ensure_workspace(agent)
    data = load_session(agent, session_id)
    if not data:
        raise HTTPException(404, "Session not found in agent workspace")
    return data


@app.post("/api/sandboxes", status_code=201)
def create_sandbox(payload: SandboxCreate, user: CurrentUser = Depends(require_permission("sandbox:write")), db: Session = Depends(get_db)) -> dict[str, Any]:
    row = stamp_owner(SandboxPolicy(**payload.model_dump()), user); db.add(row); db.commit(); db.refresh(row); return dump(row)


@app.post("/api/roles", status_code=201)
def create_role(payload: RoleCreate, user: CurrentUser = Depends(require_permission("role:write")), db: Session = Depends(get_db)) -> dict[str, Any]:
    row = stamp_owner(Role(**payload.model_dump(), user_count=0), user); db.add(row); db.commit(); db.refresh(row); return dump(row)


@app.delete("/api/{resource}/{item_id}")
def delete_resource(resource: str, item_id: int, user: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db)) -> dict[str, Any]:
    spec = RESOURCE_UPDATES.get(resource)
    if not spec:
        raise HTTPException(404, "Unknown resource")
    row = access_service.resolve_for_edit(user, kind_for(resource), item_id, db)
    if resource == "agents":
        remove_workspace(row)
    db.delete(row)
    db.commit()
    return {"id": item_id, "deleted": True}


@app.put("/api/{resource}/{item_id}")
def update_resource(resource: str, item_id: int, payload: dict[str, Any] = Body(...), user: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db)) -> dict[str, Any]:
    spec = RESOURCE_UPDATES.get(resource)
    if not spec:
        raise HTTPException(404, "Unknown resource")
    model, schema = spec
    row = access_service.resolve_for_edit(user, kind_for(resource), item_id, db)
    try:
        data = schema.model_validate(payload).model_dump(exclude_unset=True)
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=exc.errors()) from exc
    if resource == "models" and data.get("api_key") == "":
        data.pop("api_key")
    for key, value in data.items():
        setattr(row, key, value)
    if resource == "skills":
        persist_skill_markdown(row)
    if resource == "agents":
        ensure_workspace(row)
    db.commit()
    db.refresh(row)
    return dump(row)


@app.patch("/api/{resource}/{item_id}/status")
def update_resource_status(resource: str, item_id: int, payload: ResourceStatusUpdate, user: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db)) -> dict[str, Any]:
    model = STATUS_RESOURCES.get(resource)
    if not model:
        raise HTTPException(404, "Resource does not support status changes")
    row = access_service.resolve_for_edit(user, kind_for(resource), item_id, db)
    row.enabled = payload.enabled
    db.commit()
    db.refresh(row)
    return dump(row)


@app.post("/api/mcp/{item_id}/test")
def test_mcp_connection(item_id: int, user: CurrentUser = Depends(require_permission("mcp:read")), db: Session = Depends(get_db)) -> dict[str, Any]:
    row = access_service.get_row(user, ResourceKind.MCP, item_id, db)
    if not row.enabled:
        raise HTTPException(409, "MCP server is disabled")
    tools = list_mcp_tools(row)
    if not is_builtin_mcp(row):
        return {
            "id": row.id,
            "ready": False,
            "status": "not_runnable",
            "tools": tools,
            "message": f"{row.name} 只保存了地址，当前控制面只能实际调用内置 MCP「本地工具」。",
        }
    sample = execute_tool("get_current_time", {})
    row.tools_count = len(tools)
    row.config = {**(row.config or {}), "kind": "builtin", "tools": tools}
    db.commit()
    return {
        "id": row.id,
        "ready": True,
        "status": "ready",
        "tools": tools,
        "sample": sample,
        "message": f"{row.name} 已连通，可用 {len(tools)} 个工具。{sample}",
    }


@app.post("/api/skills/{item_id}/test")
def test_skill(item_id: int, user: CurrentUser = Depends(require_permission("skill:read")), db: Session = Depends(get_db)) -> dict[str, Any]:
    row = access_service.get_row(user, ResourceKind.SKILL, item_id, db)
    if not row.enabled:
        raise HTTPException(409, "Skill is disabled")
    instruction = skill_instruction(row)
    if not instruction:
        return {"id": row.id, "ready": False, "status": "empty", "message": f"{row.name} 没有可执行指令，请补充 Skill 正文。"}
    return {
        "id": row.id,
        "ready": True,
        "status": "ready",
        "instruction": instruction,
        "message": f"{row.name} 已就绪，调试台会把它写入 Agent 指令。",
    }


@app.post("/api/models/{model_id}/test")
def test_model_connection(model_id: int, user: CurrentUser = Depends(require_permission("model:read")), db: Session = Depends(get_db)) -> dict[str, Any]:
    model = access_service.get_row(user, ResourceKind.CREDENTIAL, model_id, db)
    if not model.enabled:
        raise HTTPException(409, "Model config is disabled")
    credential_ready = model_has_credential(model)
    ready = credential_ready
    return {
        "id": model.id,
        "ready": ready,
        "status": "ready" if ready else "missing_credential",
        "message": (
            f"{model.name} 配置检查通过，可以进入调试台。"
            if ready
            else f"{model.name} 尚未配置 API 密钥，请在模型配置中直接填写密钥。"
        ),
    }


@app.post("/api/playground/run")
def run_playground(payload: PlaygroundRun, user: CurrentUser = Depends(require_permission("session:write", "agent:write")), db: Session = Depends(get_db)) -> dict[str, Any]:
    agent = access_service.get_row(user, ResourceKind.AGENT, payload.agent_id, db)
    model = access_service.get_row(user, ResourceKind.CREDENTIAL, payload.model_config_id, db)
    if not model.enabled:
        raise HTTPException(409, "Selected model config is disabled")
    ensure_workspace(agent)
    session_id = payload.session_id or f"debug_{uuid4().hex[:10]}"
    conversation = db.scalar(select(Conversation).where(Conversation.session_id == session_id))
    if conversation and getattr(conversation, "tenant_id", user.tenant_id) not in (None, user.tenant_id):
        raise HTTPException(409, "Session belongs to another tenant")
    if conversation and conversation.agent_id and conversation.agent_id != agent.id:
        raise HTTPException(409, "Session belongs to another agent workspace")
    stored = load_session(agent, session_id)
    if stored and stored.get("agent_id") and stored.get("agent_id") != agent.id:
        raise HTTPException(409, "Session belongs to another agent workspace")
    history = [
        {"role": item.get("role"), "content": item.get("content") or ""}
        for item in (stored or {}).get("messages") or []
        if item.get("role") in {"user", "assistant"}
    ]
    if not history:
        history = [
            {"role": row.role, "content": row.content}
            for row in db.scalars(select(ChatMessage).where(ChatMessage.session_id == session_id).order_by(ChatMessage.id)).all()
        ]
    history.append({"role": "user", "content": payload.message})
    started = datetime.utcnow()
    reply, mode, tool_spans, usage = generate_chat_reply(agent, model, history, db)
    latency_ms = max(1, int((datetime.utcnow() - started).total_seconds() * 1000))
    spans = build_debug_spans(agent, model, mode, tool_spans, latency_ms, db)
    db.add(stamp_owner(ChatMessage(session_id=session_id, agent_id=agent.id, role="user", content=payload.message, agent_name=agent.name), user))
    db.add(stamp_owner(ChatMessage(session_id=session_id, agent_id=agent.id, role="assistant", content=reply, agent_name=agent.name), user))
    tokens = max(12, len(payload.message) + len(reply))
    if conversation:
        conversation.message_count += 2
        conversation.total_tokens += tokens
        conversation.latency_ms = latency_ms
        conversation.status = "completed"
        conversation.agent_id = agent.id
        conversation.agent_name = agent.name
        conversation.title = payload.message[:80]
        conversation.updated_at = datetime.utcnow()
        conversation.tenant_id = conversation.tenant_id or user.tenant_id
        conversation.owner_id = conversation.owner_id or user.id
        conversation.user_id = user.username
    else:
        conversation = stamp_owner(Conversation(
            session_id=session_id,
            user_id=user.username,
            agent_id=agent.id,
            agent_name=agent.name,
            title=payload.message[:80],
            status="completed",
            message_count=2,
            total_tokens=tokens,
            latency_ms=latency_ms,
            channel="Playground",
        ), user)
        db.add(conversation)
    trace_id = new_trace_id()
    db.add(stamp_owner(Trace(
        trace_id=trace_id,
        session_id=session_id,
        agent_id=agent.id,
        agent_name=agent.name,
        operation="POST /api/playground/run",
        status="ok" if mode != "error" else "error",
        duration_ms=latency_ms,
        input_tokens=int(usage.get("prompt_tokens") or len(payload.message)),
        output_tokens=int(usage.get("completion_tokens") or len(reply)),
        spans=spans,
        langfuse_url="",
        started_at=started,
    ), user))
    persisted = persist_run(
        agent=agent,
        session_id=session_id,
        title=payload.message,
        message=payload.message,
        reply=reply,
        mode=mode,
        model_name=model.name,
        trace_id=trace_id,
        spans=spans,
        usage=usage or {},
        latency_ms=latency_ms,
    )
    export_playground_to_studio(
        agent_name=agent.name,
        session_id=session_id,
        trace_id=trace_id,
        message=payload.message,
        reply=reply,
        mode=mode,
        model_name=model.name,
        model_id=model.model_id,
        spans=spans,
        usage=usage or {},
        latency_ms=latency_ms,
        title=payload.message,
    )
    db.commit()
    messages = persisted.get("messages") or [
        {"role": row.role, "content": row.content, "agent_name": row.agent_name}
        for row in db.scalars(select(ChatMessage).where(ChatMessage.session_id == session_id).order_by(ChatMessage.id)).all()
    ]
    return {
        "mode": mode,
        "reply": reply,
        "response": reply,
        "output": reply,
        "trace_id": trace_id,
        "session_id": session_id,
        "model": model.name,
        "agent": agent.name,
        "agent_id": agent.id,
        "workspace": agent.workspace,
        "messages": messages,
        "spans": spans,
        "latency_ms": latency_ms,
        "usage": usage,
    }


@app.get("/api/playground/sessions/{session_id}")
def playground_session(session_id: str, user: CurrentUser = Depends(require_permission("session:read", "agent:write")), db: Session = Depends(get_db)) -> dict[str, Any]:
    messages = [
        {"id": row.id, "role": row.role, "content": row.content, "agent_name": row.agent_name, "created_at": row.created_at}
        for row in db.scalars(select(ChatMessage).where(ChatMessage.session_id == session_id, access_service.tenant_clause(ChatMessage, user)).order_by(ChatMessage.id)).all()
    ]
    return {"session_id": session_id, "messages": messages}


