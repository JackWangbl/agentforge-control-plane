from sqlalchemy import delete, func, select, update
from sqlalchemy.orm import Session

from app.access.auth import hash_password
from app.models import (
    Agent,
    ChatMessage,
    Conversation,
    Dataset,
    DatasetCase,
    EvaluationResult,
    EvaluationRun,
    McpServer,
    ModelConfig,
    Role,
    SandboxPolicy,
    Skill,
    Tenant,
    Trace,
    User,
    Workflow,
)

DEMO_EVAL_NAMES = ("客服回归测试 #42", "知识检索准确率")


DEMO_SESSION_IDS = (
    "sess_8f21a9",
    "sess_7bc443",
    "sess_91d2be",
    "sess_3ad872",
    "sess_e51c20",
)
DEMO_TRACE_IDS = tuple(f"tr_{index:02d}c82f9" for index in range(1, 6))


def seed_database(db: Session) -> None:
    if db.scalar(select(Agent.id).limit(1)):
        return
    agents = [
        Agent(name="客服助手", description="售前咨询与工单分流", model_name="Qwen-Max", success_rate=98.6),
        Agent(name="数据分析师", description="自然语言数据分析", model_name="GPT-4.1", success_rate=96.2),
        Agent(name="知识库专家", description="企业知识检索与问答", model_name="DeepSeek-V3", success_rate=97.4),
        Agent(name="订单处理器", description="订单查询与售后处理", model_name="Qwen-Plus", success_rate=94.8),
    ]
    db.add_all(agents)
    db.add_all([
        McpServer(name="本地工具", transport="stdio", endpoint="builtin:local-tools", tools_count=4, config={"kind": "builtin"}),
        Skill(name="客服回复规范", description="按企业客服口径回复用户", source="skills/customer-reply/SKILL.md", version="1.0.0"),
        Skill(name="会议纪要", description="把讨论整理成结论、待办和风险", source="skills/meeting-notes/SKILL.md", version="1.0.0"),
        ModelConfig(name="Qwen 生产集群", provider="DashScope", model_id="qwen-max", api_key_ref="DASHSCOPE_API_KEY", temperature=0.2),
        ModelConfig(name="OpenAI 主模型", provider="OpenAI", model_id="gpt-4.1", api_key_ref="OPENAI_API_KEY", temperature=0.1),
        ModelConfig(name="本地推理集群", provider="OpenAI Compatible", model_id="deepseek-v3", base_url="http://model-gateway/v1", temperature=0.3),
        SandboxPolicy(name="标准 Python 沙箱", runtime="python:3.11", cpu_limit="1 vCPU", memory_limit="1 GiB", timeout_seconds=60, network_mode="allowlist"),
        SandboxPolicy(name="高隔离代码执行", runtime="python:3.12", cpu_limit="2 vCPU", memory_limit="2 GiB", timeout_seconds=120, network_mode="deny"),
        Role(name="平台管理员", description="全部平台配置和审计权限", permissions=["*"], user_count=0),
        Role(name="Agent 开发者", description="创建、测试和发布 Agent", permissions=["agent:read", "agent:write", "workflow:read", "workflow:write", "eval:read", "eval:run", "mcp:read", "skill:read", "model:read", "sandbox:read", "session:read", "session:write"], user_count=0),
        Role(name="审计员", description="只读查看会话、链路与日志", permissions=["session:read", "trace:read"], user_count=0),
        Workflow(name="智能客服协作流", description="意图识别、检索和工单协作", status="published", graph={"nodes": [{"id": "start", "type": "start", "label": "用户请求"}, {"id": "router", "type": "agent", "label": "意图路由"}, {"id": "kb", "type": "agent", "label": "知识库专家"}, {"id": "reply", "type": "agent", "label": "客服助手"}], "edges": [{"source": "start", "target": "router"}, {"source": "router", "target": "kb"}, {"source": "kb", "target": "reply"}]})
    ])
    db.commit()


def purge_demo_observability_data(db: Session) -> None:
    """Remove the fixed showcase sessions left by earlier releases."""
    db.execute(delete(ChatMessage).where(ChatMessage.session_id.in_(DEMO_SESSION_IDS)))
    db.execute(delete(Trace).where(Trace.trace_id.in_(DEMO_TRACE_IDS)))
    db.execute(delete(Conversation).where(Conversation.session_id.in_(DEMO_SESSION_IDS)))
    db.execute(delete(EvaluationRun).where(EvaluationRun.name.in_(DEMO_EVAL_NAMES)))


DEFAULT_ROLES = {
    "平台管理员": ["*"],
    "Agent 开发者": [
        "agent:read", "agent:write", "workflow:read", "workflow:write",
        "eval:read", "eval:run", "mcp:read", "skill:read", "model:read",
        "sandbox:read", "session:read", "session:write",
    ],
    "审计员": ["session:read", "trace:read"],
}

TENANT_TABLES = (
    Agent, McpServer, Skill, ModelConfig, Workflow, SandboxPolicy, Role,
    Conversation, ChatMessage, Trace, Dataset, DatasetCase, EvaluationRun, EvaluationResult,
)


def _get_or_create_tenant(db: Session, slug: str, name: str, description: str) -> Tenant:
    row = db.scalar(select(Tenant).where(Tenant.slug == slug))
    if row:
        return row
    row = Tenant(slug=slug, name=name, description=description, status="active")
    db.add(row)
    db.flush()
    return row


def _get_or_create_role(db: Session, tenant_id: int, name: str, description: str, permissions: list[str]) -> Role:
    row = db.scalar(select(Role).where(Role.tenant_id == tenant_id, Role.name == name))
    if row is None:
        row = db.scalar(select(Role).where(Role.name == name, Role.tenant_id.in_([tenant_id, 1, None])))
    if row is None:
        row = Role(name=name, description=description, permissions=permissions, user_count=0, tenant_id=tenant_id)
        db.add(row)
        db.flush()
        return row
    row.tenant_id = tenant_id
    if not row.permissions:
        row.permissions = permissions
    return row


def _get_or_create_user(db: Session, tenant_id: int, username: str, display_name: str, password: str, role_id: int) -> User:
    row = db.scalar(select(User).where(User.username == username))
    if row:
        row.tenant_id = tenant_id
        row.role_id = role_id
        row.display_name = display_name
        row.enabled = True
        if not row.password_hash:
            row.password_hash = hash_password(password)
        return row
    row = User(
        tenant_id=tenant_id,
        username=username,
        display_name=display_name,
        password_hash=hash_password(password),
        role_id=role_id,
        enabled=True,
    )
    db.add(row)
    db.flush()
    return row


def ensure_iam(db: Session) -> None:
    default = _get_or_create_tenant(db, "default", "默认租户", "控制面主租户，承接历史数据")
    demo = _get_or_create_tenant(db, "demo", "演示租户", "用于验证跨租户隔离")
    db.flush()

    admin_role = _get_or_create_role(db, default.id, "平台管理员", "全部平台配置和审计权限", ["*"])
    dev_role = _get_or_create_role(db, default.id, "Agent 开发者", "创建、测试和发布 Agent", DEFAULT_ROLES["Agent 开发者"])
    auditor_role = _get_or_create_role(db, default.id, "审计员", "只读查看会话、链路与日志", DEFAULT_ROLES["审计员"])
    demo_role = _get_or_create_role(db, demo.id, "租户管理员", "演示租户内的全部权限", ["tenant:admin", "agent:read", "agent:write", "session:read", "session:write", "model:read", "mcp:read", "skill:read"])

    linmo = _get_or_create_user(db, default.id, "linmo", "林默", "admin123", admin_role.id)
    _get_or_create_user(db, default.id, "developer", "陈开发", "dev123", dev_role.id)
    _get_or_create_user(db, default.id, "auditor", "周审计", "audit123", auditor_role.id)
    demo_user = _get_or_create_user(db, demo.id, "demo", "演示管理员", "demo123", demo_role.id)
    db.flush()

    for model in TENANT_TABLES:
        db.execute(update(model).where(model.tenant_id.is_(None)).values(tenant_id=default.id))
        db.execute(update(model).where(model.owner_id.is_(None)).values(owner_id=linmo.id))

    if db.scalar(select(Agent.id).where(Agent.tenant_id == demo.id).limit(1)) is None:
        db.add(Agent(
            name="演示客服",
            description="演示租户专属 Agent，默认租户不可见",
            model_name="Qwen-Max",
            status="published",
            tenant_id=demo.id,
            owner_id=demo_user.id,
        ))

    for role in db.scalars(select(Role)).all():
        role.user_count = db.scalar(select(func.count()).select_from(User).where(User.role_id == role.id)) or 0
    db.flush()
