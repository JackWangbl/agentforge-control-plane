from uuid import uuid4

from fastapi.testclient import TestClient

from app.database import SessionLocal
from app.main import app
from app.models import Agent, McpServer, SandboxPolicy, Skill
from app.services.agent_workspace import workspace_relpath
from app.services.sandbox_runtime import execute_in_sandbox, sandbox_workdir
from app.services.tool_runtime import selected_mcps, selected_skills


def _login(client: TestClient, username: str, password: str) -> None:
    response = client.post("/api/auth/login", json={"username": username, "password": password})
    assert response.status_code == 200, response.text
    client.headers.update({"Authorization": f"Bearer {response.json()['token']}"})


def test_agent_cannot_bind_foreign_tenant_resources() -> None:
    suffix = uuid4().hex[:8]
    with TestClient(app) as client:
        _login(client, "linmo", "admin123")
        tenants = client.get("/api/tenants").json()
        foreign_tenant_id = next(row["id"] for row in tenants if row["slug"] == "demo")
        switched = {"X-Tenant-Id": str(foreign_tenant_id)}
        skill = client.post(
            "/api/skills",
            json={"name": f"foreign-skill-{suffix}"},
            headers=switched,
        )
        mcp = client.post("/api/mcp", json={
            "name": f"foreign-mcp-{suffix}",
            "transport": "http",
            "endpoint": "https://example.invalid/mcp",
        }, headers=switched)
        sandbox = client.post(
            "/api/sandboxes",
            json={"name": f"foreign-box-{suffix}"},
            headers=switched,
        )
        assert skill.status_code == mcp.status_code == sandbox.status_code == 201

        for field, value in (
            ("skill_ids", [skill.json()["id"]]),
            ("mcp_ids", [mcp.json()["id"]]),
            ("sandbox_id", sandbox.json()["id"]),
        ):
            response = client.post("/api/agents", json={
                "name": f"blocked-{field}-{suffix}",
                "model_name": "test-model",
                field: value,
            })
            assert response.status_code == 422, response.text


def test_runtime_ignores_legacy_foreign_bindings() -> None:
    with SessionLocal() as db:
        suffix = uuid4().hex[:8]
        foreign_skill = Skill(
            name=f"legacy-skill-{suffix}",
            tenant_id=999,
            enabled=True,
            source="manual",
        )
        foreign_mcp = McpServer(
            name=f"legacy-mcp-{suffix}",
            tenant_id=999,
            transport="http",
            endpoint="https://example.invalid/mcp",
        )
        db.add_all([foreign_skill, foreign_mcp])
        db.flush()
        agent = Agent(
            id=99991,
            tenant_id=1,
            name="legacy-invalid-binding",
            model_name="test",
            skill_ids=[foreign_skill.id],
            mcp_ids=[foreign_mcp.id],
        )
        assert selected_skills(agent, db) == []
        assert selected_mcps(agent, db) == []


def test_sandbox_does_not_inherit_control_plane_secrets(monkeypatch) -> None:
    monkeypatch.setenv("CONTROL_PLANE_TEST_SECRET", "must-not-leak")
    policy = SandboxPolicy(
        id=991,
        name="isolated-env",
        runtime="python:3.11",
        memory_limit="256 MiB",
        timeout_seconds=5,
        network_mode="deny",
        enabled=True,
    )
    result = execute_in_sandbox(
        policy,
        "python",
        "import os; print(os.getenv('CONTROL_PLANE_TEST_SECRET'))",
        tenant_id=7,
        execution_id="exec-a",
    )
    assert result["ok"] is True
    assert result["output"] == "None"


def test_production_local_sandbox_fails_closed(monkeypatch) -> None:
    monkeypatch.delenv("ALLOW_UNSAFE_LOCAL_SANDBOX", raising=False)
    policy = SandboxPolicy(
        id=992,
        name="prod-box",
        runtime="python:3.11",
        memory_limit="256 MiB",
        timeout_seconds=5,
        network_mode="deny",
        enabled=True,
    )
    result = execute_in_sandbox(policy, "python", "print('unsafe')", tenant_id=7)
    assert result["ok"] is False
    assert "默认禁用" in result["error"]


def test_workspace_and_sandbox_paths_include_tenant() -> None:
    left = Agent(id=700, tenant_id=1, name="same", model_name="test")
    right = Agent(id=700, tenant_id=2, name="same", model_name="test")
    assert workspace_relpath(left) != workspace_relpath(right)
    policy = SandboxPolicy(id=88, name="same", enabled=True)
    assert sandbox_workdir(policy, 1, "run") != sandbox_workdir(policy, 2, "run")
