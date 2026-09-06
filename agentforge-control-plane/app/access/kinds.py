from __future__ import annotations

from enum import Enum


class ResourceKind(str, Enum):
    AGENT = "agent"
    CREDENTIAL = "credential"
    MCP = "mcp"
    SKILL = "skill"
    WORKFLOW = "workflow"
    SANDBOX = "sandbox"
    DATASET = "dataset"
    EVALUATION = "evaluation"
    SESSION = "session"
    TRACE = "trace"
    ROLE = "role"
    USER = "user"
    EXPERIMENT = "experiment"


class ResourcePermission(str, Enum):
    READ = "read"
    EDIT = "edit"


RESOURCE_READ = {
    ResourceKind.AGENT: "agent:read",
    ResourceKind.CREDENTIAL: "model:read",
    ResourceKind.MCP: "mcp:read",
    ResourceKind.SKILL: "skill:read",
    ResourceKind.WORKFLOW: "workflow:read",
    ResourceKind.SANDBOX: "sandbox:read",
    ResourceKind.DATASET: "eval:read",
    ResourceKind.EVALUATION: "eval:read",
    ResourceKind.SESSION: "session:read",
    ResourceKind.TRACE: "trace:read",
    ResourceKind.ROLE: "role:read",
    ResourceKind.USER: "user:read",
    ResourceKind.EXPERIMENT: "experiment:read",
}

RESOURCE_WRITE = {
    ResourceKind.AGENT: "agent:write",
    ResourceKind.CREDENTIAL: "model:write",
    ResourceKind.MCP: "mcp:write",
    ResourceKind.SKILL: "skill:write",
    ResourceKind.WORKFLOW: "workflow:write",
    ResourceKind.SANDBOX: "sandbox:write",
    ResourceKind.DATASET: "eval:run",
    ResourceKind.EVALUATION: "eval:run",
    ResourceKind.SESSION: "session:write",
    ResourceKind.TRACE: "trace:read",
    ResourceKind.ROLE: "role:write",
    ResourceKind.USER: "user:write",
    ResourceKind.EXPERIMENT: "experiment:write",
}

ROUTE_KIND = {
    "agents": ResourceKind.AGENT,
    "models": ResourceKind.CREDENTIAL,
    "mcp": ResourceKind.MCP,
    "skills": ResourceKind.SKILL,
    "workflows": ResourceKind.WORKFLOW,
    "sandboxes": ResourceKind.SANDBOX,
    "roles": ResourceKind.ROLE,
    "traces": ResourceKind.TRACE,
    "datasets": ResourceKind.DATASET,
    "evaluations": ResourceKind.EVALUATION,
    "experiments": ResourceKind.EXPERIMENT,
}

PERMISSION_CATALOG = [
    {"key": "*", "label": "全部权限", "group": "平台"},
    {"key": "platform:admin", "label": "跨租户管理", "group": "平台"},
    {"key": "tenant:admin", "label": "租户管理", "group": "租户"},
    {"key": "user:read", "label": "查看用户", "group": "租户"},
    {"key": "user:write", "label": "管理用户", "group": "租户"},
    {"key": "role:read", "label": "查看角色", "group": "租户"},
    {"key": "role:write", "label": "管理角色", "group": "租户"},
    {"key": "agent:read", "label": "查看 Agent", "group": "构建"},
    {"key": "agent:write", "label": "编辑 Agent", "group": "构建"},
    {"key": "mcp:read", "label": "查看 MCP", "group": "构建"},
    {"key": "mcp:write", "label": "编辑 MCP", "group": "构建"},
    {"key": "skill:read", "label": "查看 Skill", "group": "构建"},
    {"key": "skill:write", "label": "编辑 Skill", "group": "构建"},
    {"key": "model:read", "label": "查看模型", "group": "构建"},
    {"key": "model:write", "label": "编辑模型", "group": "构建"},
    {"key": "workflow:read", "label": "查看编排", "group": "构建"},
    {"key": "workflow:write", "label": "编辑编排", "group": "构建"},
    {"key": "sandbox:read", "label": "查看沙箱", "group": "构建"},
    {"key": "sandbox:write", "label": "编辑沙箱", "group": "构建"},
    {"key": "session:read", "label": "查看会话", "group": "运行"},
    {"key": "session:write", "label": "发起调试", "group": "运行"},
    {"key": "trace:read", "label": "查看链路", "group": "运行"},
    {"key": "eval:read", "label": "查看评测", "group": "质量"},
    {"key": "eval:run", "label": "执行评测", "group": "质量"},
    {"key": "experiment:read", "label": "查看实验", "group": "质量"},
    {"key": "experiment:write", "label": "管理实验", "group": "质量"},
]
