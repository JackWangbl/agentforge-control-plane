from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class McpCreate(BaseModel):
    name: str = Field(min_length=2, max_length=100)
    transport: str = Field(pattern="^(http|sse|stdio)$")
    endpoint: str
    enabled: bool = True
    config: dict[str, Any] = {}


class SkillCreate(BaseModel):
    name: str = Field(min_length=2, max_length=100)
    description: str = ""
    source: str = "manual"
    version: str = "1.0.0"
    instruction: str = ""
    enabled: bool = True


class ModelCreate(BaseModel):
    name: str
    provider: str
    model_id: str
    base_url: str = ""
    api_key: str = ""
    api_key_ref: str = ""
    temperature: float = Field(default=0.2, ge=0, le=2)
    enabled: bool = True


class WorkflowCreate(BaseModel):
    name: str
    description: str = ""
    status: str = "draft"
    graph: dict[str, Any]


class WorkflowUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    status: Optional[str] = None
    graph: Optional[dict[str, Any]] = None


class AgentCreate(BaseModel):
    name: str = Field(min_length=2, max_length=80)
    description: str = ""
    model_name: str
    status: str = "draft"
    version: str = "v1.0.0"
    system_prompt: str = ""
    skill_ids: list[int] = []
    mcp_ids: list[int] = []


class SandboxCreate(BaseModel):
    name: str
    runtime: str = "python:3.11"
    cpu_limit: str = "1 vCPU"
    memory_limit: str = "1 GiB"
    timeout_seconds: int = Field(default=60, ge=1, le=3600)
    network_mode: str = "deny"
    enabled: bool = True


class RoleCreate(BaseModel):
    name: str
    description: str = ""
    permissions: list[str] = []


class McpUpdate(BaseModel):
    name: Optional[str] = Field(default=None, min_length=2, max_length=100)
    transport: Optional[str] = Field(default=None, pattern="^(http|sse|stdio)$")
    endpoint: Optional[str] = None
    enabled: Optional[bool] = None
    config: Optional[dict[str, Any]] = None


class SkillUpdate(BaseModel):
    name: Optional[str] = Field(default=None, min_length=2, max_length=100)
    description: Optional[str] = None
    source: Optional[str] = None
    version: Optional[str] = None
    instruction: Optional[str] = None
    enabled: Optional[bool] = None


class ModelUpdate(BaseModel):
    name: Optional[str] = None
    provider: Optional[str] = None
    model_id: Optional[str] = None
    base_url: Optional[str] = None
    api_key: Optional[str] = None
    api_key_ref: Optional[str] = None
    temperature: Optional[float] = Field(default=None, ge=0, le=2)
    enabled: Optional[bool] = None


class AgentUpdate(BaseModel):
    name: Optional[str] = Field(default=None, min_length=2, max_length=80)
    description: Optional[str] = None
    model_name: Optional[str] = None
    status: Optional[str] = None
    version: Optional[str] = None
    system_prompt: Optional[str] = None
    skill_ids: Optional[list[int]] = None
    mcp_ids: Optional[list[int]] = None


class SandboxUpdate(BaseModel):
    name: Optional[str] = None
    runtime: Optional[str] = None
    cpu_limit: Optional[str] = None
    memory_limit: Optional[str] = None
    timeout_seconds: Optional[int] = Field(default=None, ge=1, le=3600)
    network_mode: Optional[str] = None
    enabled: Optional[bool] = None


class RoleUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    permissions: Optional[list[str]] = None


class PlaygroundRun(BaseModel):
    agent_id: int
    model_config_id: int
    message: str = Field(min_length=1, max_length=20000)
    session_id: Optional[str] = None


class ResourceStatusUpdate(BaseModel):
    enabled: bool


class DatasetCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    description: str = ""


class DatasetCaseCreate(BaseModel):
    input: str = Field(min_length=1)
    expected: str = ""
    case_key: str = ""
    tags: list[str] = []


class LoginRequest(BaseModel):
    username: str
    password: str


class UserCreate(BaseModel):
    username: str = Field(min_length=2, max_length=60)
    display_name: str = ""
    password: str = Field(min_length=4, max_length=80)
    role_id: int
    tenant_id: Optional[int] = None
    enabled: bool = True


class UserUpdate(BaseModel):
    display_name: Optional[str] = None
    password: Optional[str] = None
    role_id: Optional[int] = None
    tenant_id: Optional[int] = None
    enabled: Optional[bool] = None


class TenantCreate(BaseModel):
    slug: str = Field(min_length=2, max_length=60)
    name: str
    description: str = ""


class EvaluationLaunch(BaseModel):
    agent_id: int
    dataset_id: int
    name: str = ""
    scorer: str = "contains"
    judge_model_id: Optional[int] = None
    case_ids: list[int] = []
