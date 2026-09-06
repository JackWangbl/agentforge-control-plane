from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class McpCreate(BaseModel):
    name: str = Field(min_length=2, max_length=100)
    transport: str = Field(pattern="^(stdio|sse|http|http_stream|streamable_http)$")
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
    sandbox_id: Optional[int] = None


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
    transport: Optional[str] = Field(default=None, pattern="^(stdio|sse|http|http_stream|streamable_http)$")
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


class AgentCopy(BaseModel):
    name: Optional[str] = Field(default=None, max_length=80)


class AgentRename(BaseModel):
    name: str = Field(min_length=2, max_length=80)


class AgentUpdate(BaseModel):
    name: Optional[str] = Field(default=None, min_length=2, max_length=80)
    description: Optional[str] = None
    model_name: Optional[str] = None
    status: Optional[str] = None
    version: Optional[str] = None
    system_prompt: Optional[str] = None
    skill_ids: Optional[list[int]] = None
    mcp_ids: Optional[list[int]] = None
    sandbox_id: Optional[int] = None


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
    experiment_id: Optional[int] = None
    user_key: Optional[str] = Field(default=None, max_length=120)


class PlaygroundResume(BaseModel):
    agent_id: int
    model_config_id: int
    session_id: str = Field(min_length=1, max_length=120)
    experiment_id: Optional[int] = None
    user_key: Optional[str] = Field(default=None, max_length=120)
    force_rerun_tools: bool = False


class ExperimentVariantIn(BaseModel):
    key: str = ""
    name: str = ""
    agent_id: int
    weight: int = Field(default=50, ge=1, le=1000)


class ExperimentCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    description: str = ""
    assignment_unit: str = "session"
    assignment_strategy: str = "user_hash"
    traffic_percent: int = Field(default=100, ge=1, le=100)
    variants: list[ExperimentVariantIn] = []


class ExperimentUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    assignment_unit: Optional[str] = None
    assignment_strategy: Optional[str] = None
    traffic_percent: Optional[int] = Field(default=None, ge=1, le=100)
    variants: Optional[list[ExperimentVariantIn]] = None


class ExperimentAssign(BaseModel):
    unit_key: str = ""
    session_id: Optional[str] = Field(default=None, max_length=120)
    user_key: Optional[str] = Field(default=None, max_length=120)


class ExperimentCompare(BaseModel):
    dataset_id: Optional[int] = None
    prompts: list[str] = []
    scorer: str = "contains"
    case_limit: int = Field(default=6, ge=1, le=12)


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
