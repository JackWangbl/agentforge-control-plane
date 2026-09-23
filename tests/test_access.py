from fastapi.testclient import TestClient

from app.main import app


def _login(client: TestClient, username: str, password: str) -> dict:
    response = client.post("/api/auth/login", json={"username": username, "password": password})
    assert response.status_code == 200, response.text
    data = response.json()
    client.headers.update({"Authorization": f"Bearer {data['token']}"})
    return data


def test_login_and_me():
    with TestClient(app) as client:
        data = _login(client, "linmo", "admin123")
        assert data["user"]["username"] == "linmo"
        me = client.get("/api/auth/me").json()
        assert me["is_platform_admin"] is True
        assert any(item["slug"] == "demo" for item in me["tenants"])


def test_auditor_cannot_write_agents():
    with TestClient(app) as client:
        _login(client, "auditor", "audit123")
        assert client.get("/api/sessions").status_code == 200
        assert client.get("/api/agents").status_code == 403
        created = client.post("/api/agents", json={"name": "越权助手", "model_name": "Qwen-Max"})
        assert created.status_code == 403


def test_tenant_isolation_hides_foreign_agents():
    with TestClient(app) as client:
        _login(client, "linmo", "admin123")
        default_names = {row["name"] for row in client.get("/api/agents").json()}
        assert "演示客服" not in default_names
        assert any(name in default_names for name in ("客服助手", "个人助手", "知识库专家"))

        _login(client, "demo", "demo123")
        demo_names = {row["name"] for row in client.get("/api/agents").json()}
        assert "演示客服" in demo_names
        assert "客服助手" not in demo_names


def test_platform_admin_can_switch_tenant():
    with TestClient(app) as client:
        _login(client, "linmo", "admin123")
        home = client.get("/api/tenants").json()
        demo = next(row for row in home if row["slug"] == "demo")
        switched = client.get("/api/agents", headers={"X-Tenant-Id": str(demo["id"])})
        assert switched.status_code == 200
        names = {row["name"] for row in switched.json()}
        assert "演示客服" in names


def test_wrong_password_rejected():
    with TestClient(app) as client:
        assert client.post("/api/auth/login", json={"username": "linmo", "password": "bad"}).status_code == 401


def test_role_update_changes_permissions():
    with TestClient(app) as client:
        _login(client, "linmo", "admin123")
        roles = client.get("/api/roles").json()
        auditor = next(row for row in roles if row["name"] == "审计员")
        updated = client.put(
            f"/api/roles/{auditor['id']}",
            json={"description": "只读审计", "permissions": ["session:read", "trace:read", "agent:read"]},
        )
        assert updated.status_code == 200, updated.text
        assert updated.json()["description"] == "只读审计"
        assert "agent:read" in updated.json()["permissions"]
        client.put(f"/api/roles/{auditor['id']}", json={"description": "只读查看会话、链路与日志", "permissions": ["session:read", "trace:read"]})


def test_logout_revokes_token():
    with TestClient(app) as client:
        data = _login(client, "linmo", "admin123")
        token = data["token"]
        assert client.get("/api/auth/me").status_code == 200
        assert client.post("/api/auth/logout").status_code == 200
        client.headers["Authorization"] = f"Bearer {token}"
        assert client.get("/api/auth/me").status_code == 401
