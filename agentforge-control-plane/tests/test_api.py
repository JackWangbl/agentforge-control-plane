from pathlib import Path
from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.main import app
from app.database import SessionLocal
from app.models import ChatMessage, Conversation, McpServer, ModelConfig, Role, SandboxPolicy, Skill, Trace


def test_health():
    with TestClient(app) as client:
        assert client.get("/api/health").json()["status"] == "ok"


def test_session_filters():
    suffix = uuid4().hex[:8]
    session_id = f"real_{suffix}"
    with SessionLocal() as db:
        db.add(Conversation(
            session_id=session_id,
            user_id=f"user_{suffix}",
            agent_id=1,
            agent_name="客服助手",
            title="真实查询测试",
            status="completed",
            message_count=1,
            total_tokens=12,
            latency_ms=34,
            channel="API",
        ))
        db.add(ChatMessage(
            session_id=session_id,
            agent_id=1,
            role="user",
            content=f"只在消息正文出现的检索词 {suffix}",
            agent_name="客服助手",
        ))
        db.commit()
    with TestClient(app) as client:
        rows = client.get("/api/sessions", params={"q": suffix}).json()
        assert len(rows) == 1
        assert rows[0]["session_id"] == session_id
        detail = client.get(f"/api/sessions/{session_id}")
        assert detail.status_code == 200
        assert detail.json()["messages"][0]["content"].endswith(suffix)
    with SessionLocal() as db:
        db.query(ChatMessage).filter(ChatMessage.session_id == session_id).delete()
        db.query(Conversation).filter(Conversation.session_id == session_id).delete()
        db.commit()


def test_create_and_edit_all_configurable_resources():
    suffix = uuid4().hex[:8]
    scenarios = [
        ("mcp", {"name": f"MCP-{suffix}", "transport": "http", "endpoint": "https://before.example/mcp", "config": {}}, {"endpoint": "https://after.example/mcp"}),
        ("skills", {"name": f"Skill-{suffix}", "description": "before"}, {"description": "after", "version": "2.0.0"}),
        ("models", {"name": f"Model-{suffix}", "provider": "OpenAI", "model_id": "before"}, {"model_id": "after", "temperature": 0.7}),
        ("sandboxes", {"name": f"Sandbox-{suffix}", "runtime": "python:3.11", "timeout_seconds": 60}, {"runtime": "python:3.12", "timeout_seconds": 90}),
        ("roles", {"name": f"Role-{suffix}", "permissions": ["session:read"]}, {"description": "after", "permissions": ["session:read", "trace:read"]}),
    ]
    created_ids = []
    with TestClient(app) as client:
        for resource, create_payload, update_payload in scenarios:
            created = client.post(f"/api/{resource}", json=create_payload)
            assert created.status_code == 201, created.text
            item_id = created.json()["id"]
            created_ids.append((resource, item_id))
            updated = client.put(f"/api/{resource}/{item_id}", json=update_payload)
            assert updated.status_code == 200, updated.text
            assert all(updated.json()[key] == value for key, value in update_payload.items())
            listed = client.get(f"/api/{resource}").json()
            assert any(row["id"] == item_id for row in listed)
    model_by_resource = {"mcp": McpServer, "skills": Skill, "models": ModelConfig, "sandboxes": SandboxPolicy, "roles": Role}
    with SessionLocal() as db:
        for resource, item_id in created_ids:
            row = db.get(model_by_resource[resource], item_id)
            if row:
                db.delete(row)
        db.commit()


def test_edit_rejects_missing_record_and_invalid_value():
    with TestClient(app) as client:
        assert client.put("/api/models/999999", json={"name": "missing"}).status_code == 404
        assert client.put("/api/models/1", json={"temperature": 9}).status_code == 422


def test_modal_cancel_buttons_cannot_submit_form():
    root = Path(__file__).resolve().parents[1]
    html = (root / "static" / "index.html").read_text(encoding="utf-8")
    script = (root / "static" / "app.js").read_text(encoding="utf-8")
    assert html.count('type="button"') >= 2
    assert html.count("data-close-modal") >= 2
    assert "button.addEventListener('click',()=>$('#modal').close())" in script
    assert "id?'PUT':'POST'" in script


def test_model_check_and_playground_create_observable_records():
    with TestClient(app) as client:
        client.patch("/api/models/1/status", json={"enabled": True})
        check = client.post("/api/models/1/test")
        assert check.status_code == 200
        assert check.json()["status"] in {"ready", "missing_credential"}
        run = client.post(
            "/api/playground/run",
            json={"agent_id": 1, "model_config_id": 1, "message": "调试入口回归测试"},
        )
        assert run.status_code == 200, run.text
        body = run.json()
        assert body["mode"] in {"ready", "preview", "error"}
        assert body["reply"]
        assert body["reply"] == body["output"]
        assert any(item["role"] == "assistant" and item["content"] == body["reply"] for item in body["messages"])
        assert len(body["trace_id"]) == 32
        assert all(ch in "0123456789abcdef" for ch in body["trace_id"])
        assert body["spans"]
        assert body["workspace"]
        assert body["agent_id"] == 1
        assert any(item.get("kind") == "agent" for item in body["spans"])
        assert any(item.get("kind") == "llm" for item in body["spans"])
        workspace = client.get("/api/agents/1/workspace").json()
        assert workspace["path"] == body["workspace"]
        stored_session = client.get(f"/api/agents/1/workspace/sessions/{body['session_id']}")
        assert stored_session.status_code == 200
        assert any(item.get("content") == "调试入口回归测试" for item in stored_session.json().get("messages") or [])
        follow = client.post(
            "/api/playground/run",
            json={"agent_id": 1, "model_config_id": 1, "message": "第二句继续聊", "session_id": body["session_id"]},
        )
        assert follow.status_code == 200, follow.text
        assert follow.json()["session_id"] == body["session_id"]
        assert len(follow.json()["messages"]) == 4
        stored = client.get(f"/api/playground/sessions/{body['session_id']}").json()
        assert len(stored["messages"]) == 4
        sessions = client.get("/api/sessions", params={"session_id": body["session_id"]}).json()
        traces = client.get("/api/traces").json()
        assert len(sessions) == 1
        assert any(trace["trace_id"] == body["trace_id"] for trace in traces)
        saved = next(row for row in traces if row["trace_id"] == body["trace_id"])
        detail = client.get(f"/api/traces/{saved['id']}")
        assert detail.status_code == 200, detail.text
        payload = detail.json()
        assert payload["trace_id"] == body["trace_id"]
        assert payload["spans"]
        assert payload["messages"]
        assert any(item["content"] == "调试入口回归测试" for item in payload["messages"])
        by_key = client.get(f"/api/traces/{body['trace_id']}")
        assert by_key.status_code == 200
        assert by_key.json()["id"] == saved["id"]
        assert client.get("/api/traces/missing-trace").status_code == 404
    with SessionLocal() as db:
        conversation = db.scalar(select(Conversation).where(Conversation.session_id == body["session_id"]))
        trace = db.scalar(select(Trace).where(Trace.trace_id == body["trace_id"]))
        if conversation:
            db.delete(conversation)
        if trace:
            db.delete(trace)
        for message in db.scalars(select(ChatMessage).where(ChatMessage.session_id == body["session_id"])).all():
            db.delete(message)
        db.commit()


def test_model_can_be_disabled_and_cannot_be_used_until_enabled():
    with TestClient(app) as client:
        disabled = client.patch("/api/models/1/status", json={"enabled": False})
        assert disabled.status_code == 200
        assert disabled.json()["enabled"] is False
        assert client.post("/api/models/1/test").status_code == 409
        assert client.post(
            "/api/playground/run",
            json={"agent_id": 1, "model_config_id": 1, "message": "不应执行"},
        ).status_code == 409
        listed = client.get("/api/models").json()
        assert next(row for row in listed if row["id"] == 1)["enabled"] is False
        enabled = client.patch("/api/models/1/status", json={"enabled": True})
        assert enabled.status_code == 200
        assert enabled.json()["enabled"] is True


def test_update_creatable_resources():
    suffix = uuid4().hex[:8]
    created_ids = []
    cases = [
        ("agents", {"name": f"编辑Agent-{suffix}", "model_name": "Qwen-Max", "description": "初始说明"}, {"description": "已更新说明"}),
        ("mcp", {"name": f"编辑MCP-{suffix}", "transport": "http", "endpoint": "https://example.com/mcp"}, {"endpoint": "https://example.com/mcp-v2"}),
        ("skills", {"name": f"编辑Skill-{suffix}", "description": "初始能力", "source": "skills/demo/SKILL.md"}, {"description": "已更新能力", "version": "1.1.0"}),
        ("models", {"name": f"编辑模型-{suffix}", "provider": "OpenAI", "model_id": "gpt-4.1", "temperature": 0.2}, {"temperature": 0.5, "base_url": "https://api.openai.com/v1"}),
        ("sandboxes", {"name": f"编辑沙箱-{suffix}", "runtime": "python:3.11", "timeout_seconds": 60}, {"timeout_seconds": 90, "network_mode": "allowlist"}),
        ("roles", {"name": f"编辑角色-{suffix}", "description": "只读", "permissions": ["session:read"]}, {"description": "可审计", "permissions": ["session:read", "trace:read"]}),
    ]
    with TestClient(app) as client:
        for resource, create_payload, update_payload in cases:
            created = client.post(f"/api/{resource}", json=create_payload)
            assert created.status_code == 201, created.text
            item_id = created.json()["id"]
            updated = client.put(f"/api/{resource}/{item_id}", json=update_payload)
            assert updated.status_code == 200, updated.text
            body = updated.json()
            for key, value in update_payload.items():
                assert body[key] == value
            for key, value in create_payload.items():
                if key not in update_payload:
                    assert body[key] == value
            created_ids.append((resource, item_id))
        missing = client.put("/api/agents/999999", json={"description": "不存在"})
        assert missing.status_code == 404
        for resource, item_id in created_ids:
            assert client.delete(f"/api/{resource}/{item_id}").status_code == 200


def test_delete_creatable_resources():
    suffix = uuid4().hex[:8]
    cases = [
        ("agents", {"name": f"删除Agent-{suffix}", "model_name": "Qwen-Max"}),
        ("mcp", {"name": f"删除MCP-{suffix}", "transport": "http", "endpoint": "https://example.com/mcp"}),
        ("skills", {"name": f"删除Skill-{suffix}", "description": "待删除"}),
        ("models", {"name": f"删除模型-{suffix}", "provider": "OpenAI", "model_id": "gpt-4.1"}),
        ("sandboxes", {"name": f"删除沙箱-{suffix}", "runtime": "python:3.11"}),
        ("roles", {"name": f"删除角色-{suffix}", "description": "待删除", "permissions": ["session:read"]}),
    ]
    with TestClient(app) as client:
        for resource, payload in cases:
            created = client.post(f"/api/{resource}", json=payload)
            assert created.status_code == 201, created.text
            item_id = created.json()["id"]
            deleted = client.delete(f"/api/{resource}/{item_id}")
            assert deleted.status_code == 200, deleted.text
            assert deleted.json() == {"id": item_id, "deleted": True}
            listed = client.get(f"/api/{resource}").json()
            assert all(row["id"] != item_id for row in listed)
            assert client.delete(f"/api/{resource}/{item_id}").status_code == 404
        assert client.delete("/api/agents/999999").status_code == 404
        assert client.delete("/api/traces/1").status_code == 404


def test_model_api_key_is_stored_masked_and_used_for_readiness():
    suffix = uuid4().hex[:8]
    secret = f"sk-live-secret-{suffix}"
    with TestClient(app) as client:
        created = client.post(
            "/api/models",
            json={"name": f"密钥模型-{suffix}", "provider": "OpenAI", "model_id": "gpt-4.1", "api_key": secret},
        )
        assert created.status_code == 201, created.text
        body = created.json()
        item_id = body["id"]
        assert secret not in created.text
        assert body["has_api_key"] is True
        assert body["has_credential"] is True
        assert body["api_key"] != secret
        listed = client.get("/api/models").json()
        row = next(item for item in listed if item["id"] == item_id)
        assert secret not in str(listed)
        assert row["has_credential"] is True
        check = client.post(f"/api/models/{item_id}/test")
        assert check.status_code == 200
        assert check.json()["ready"] is True
        kept = client.put(f"/api/models/{item_id}", json={"temperature": 0.4, "api_key": ""})
        assert kept.status_code == 200
        assert kept.json()["has_api_key"] is True
        with SessionLocal() as db:
            stored = db.get(ModelConfig, item_id)
            assert stored is not None
            assert stored.api_key == secret
            db.delete(stored)
            db.commit()


def test_deepseek_model_id_aliases():
    from app.services.agentscope_adapter import normalize_chat_model

    assert normalize_chat_model("deepseek-v4", "https://api.deepseek.com") == "deepseek-v4-flash"
    assert normalize_chat_model("deepseek-v4-pro", "https://api.deepseek.com") == "deepseek-v4-pro"


def test_workflow_graph_can_be_saved():
    suffix = uuid4().hex[:8]
    graph = {
        "nodes": [
            {"id": "start", "type": "start", "label": "入口", "x": 40, "y": 180},
            {"id": "agent", "type": "agent", "label": "客服", "x": 220, "y": 180, "agent": "客服助手", "policy": "retry"},
        ],
        "edges": [{"source": "start", "target": "agent"}],
    }
    with TestClient(app) as client:
        created = client.post(
            "/api/workflows",
            json={"name": f"画布-{suffix}", "description": "可编辑", "status": "draft", "graph": {"nodes": [], "edges": []}},
        )
        assert created.status_code == 201, created.text
        item_id = created.json()["id"]
        updated = client.put(f"/api/workflows/{item_id}", json={"graph": graph, "status": "published"})
        assert updated.status_code == 200, updated.text
        body = updated.json()
        assert body["status"] == "published"
        assert body["graph"]["nodes"][0]["x"] == 40
        assert body["graph"]["edges"][0]["target"] == "agent"
        deleted = client.delete(f"/api/workflows/{item_id}")
        assert deleted.status_code == 200
        assert client.get("/api/workflows").json()
        assert all(row["id"] != item_id for row in client.get("/api/workflows").json())


def test_builtin_mcp_and_skills_are_runnable():
    with TestClient(app) as client:
        mcps = client.get("/api/mcp").json()
        assert [row["name"] for row in mcps if row["name"] == "本地工具"]
        local = next(row for row in mcps if row["name"] == "本地工具")
        assert local["runnable"] is True
        assert local["tools_count"] == 4
        browser = next((row for row in mcps if row["name"] == "浏览器工具"), None)
        assert browser
        assert browser["runnable"] is True
        assert any(tool["name"] == "browser_open" for tool in browser.get("tools") or [])
        probed = client.post(f"/api/mcp/{local['id']}/test")
        assert probed.status_code == 200, probed.text
        body = probed.json()
        assert body["ready"] is True
        assert "当前时间" in body["sample"]
        skills = client.get("/api/skills").json()
        names = {row["name"] for row in skills}
        assert "客服回复规范" in names
        assert "会议纪要" in names
        assert "数据洞察" not in names
        skill = next(row for row in skills if row["name"] == "客服回复规范")
        preview = client.post(f"/api/skills/{skill['id']}/test")
        assert preview.status_code == 200, preview.text
        assert preview.json()["ready"] is True
        assert "客服" in preview.json()["instruction"]
        bound = client.put(
            "/api/agents/1",
            json={"mcp_ids": [local["id"]], "skill_ids": [skill["id"]], "system_prompt": "你是绑定了工具的助手。"},
        )
        assert bound.status_code == 200, bound.text
        assert bound.json()["mcp_ids"] == [local["id"]]
        clock = client.post(
            "/api/playground/run",
            json={"agent_id": 1, "model_config_id": 1, "message": "现在几点了"},
        )
        assert clock.status_code == 200, clock.text
        assert "当前时间" in clock.json()["reply"]


def test_skill_saves_markdown_file():
    suffix = uuid4().hex[:8]
    name = f"纪要模板{suffix}"
    body = "# 会议纪要\n\n- 决议：按 Markdown 保存"
    with TestClient(app) as client:
        created = client.post("/api/skills", json={"name": name, "description": "直接写 md", "instruction": body})
        assert created.status_code == 201, created.text
        item = created.json()
        assert item["has_instruction"] is True
        assert item["source"].endswith("SKILL.md")
        path = Path(__file__).resolve().parents[1] / item["source"]
        assert path.exists()
        text = path.read_text(encoding="utf-8")
        assert "按 Markdown 保存" in text
        assert client.delete(f"/api/skills/{item['id']}").status_code == 200
        if path.exists():
            path.unlink()
        if path.parent.exists() and path.parent.name != "skills" and not any(path.parent.iterdir()):
            path.parent.rmdir()


def test_agent_prompt_tools_and_skills_roundtrip():
    suffix = uuid4().hex[:8]
    with TestClient(app) as client:
        mcp = next(row for row in client.get("/api/mcp").json() if row["name"] == "本地工具")
        skills = client.get("/api/skills").json()
        skill_ids = [row["id"] for row in skills]
        created = client.post(
            "/api/agents",
            json={
                "name": f"绑定助手{suffix}",
                "model_name": "Qwen-Max",
                "description": "绑定工具和技能",
                "system_prompt": "你必须先调用工具再回答。",
                "mcp_ids": [mcp["id"]],
                "skill_ids": skill_ids,
            },
        )
        assert created.status_code == 201, created.text
        body = created.json()
        assert body["workspace"].startswith("workspaces/")
        assert body["system_prompt"] == "你必须先调用工具再回答。"
        assert body["mcp_ids"] == [mcp["id"]]
        assert body["skill_ids"] == skill_ids
        assert body["bound_mcps"][0]["id"] == mcp["id"]
        assert body["bound_mcps"][0]["tools"]
        assert {item["id"] for item in body["bound_skills"]} == set(skill_ids)
        listed = client.get("/api/agents").json()
        saved = next(row for row in listed if row["id"] == body["id"])
        assert saved["mcp_ids"] == [mcp["id"]]
        updated = client.put(
            f"/api/agents/{body['id']}",
            json={"system_prompt": "只使用会议纪要。", "skill_ids": skill_ids[:1], "mcp_ids": []},
        )
        assert updated.status_code == 200, updated.text
        assert updated.json()["system_prompt"] == "只使用会议纪要。"
        assert updated.json()["skill_ids"] == skill_ids[:1]
        assert updated.json()["mcp_ids"] == []
        clock = client.post(
            "/api/playground/run",
            json={"agent_id": body["id"], "model_config_id": 1, "message": "现在几点了"},
        )
        assert clock.status_code == 200, clock.text
        assert "当前时间" not in clock.json()["reply"]
        assert clock.json()["workspace"] == body["workspace"]
        assert client.get(f"/api/agents/{body['id']}/workspace").json()["session_count"] >= 1
        assert client.delete(f"/api/agents/{body['id']}").status_code == 200
        assert client.get(f"/api/agents/{body['id']}/workspace").status_code == 404
