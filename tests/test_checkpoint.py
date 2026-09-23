from uuid import uuid4

from fastapi.testclient import TestClient

from app.main import app


def test_resume_without_checkpoint_is_conflict():
    with TestClient(app) as client:
        client.patch("/api/models/1/status", json={"enabled": True})
        missing = client.post("/api/playground/resume", json={
            "agent_id": 1,
            "model_config_id": 1,
            "session_id": f"debug_{uuid4().hex[:10]}",
        })
        assert missing.status_code == 409, missing.text


def test_playground_checkpoint_skips_completed_tools(monkeypatch):
    calls = {"llm": 0, "tools": []}

    def fake_complete_chat(**kwargs):
        calls["llm"] += 1
        if calls["llm"] == 1:
            return {
                "content": "",
                "tool_calls": [{
                    "id": "call_clock",
                    "type": "function",
                    "function": {"name": "get_current_time", "arguments": "{}"},
                }],
                "usage": {"prompt_tokens": 8, "completion_tokens": 4, "total_tokens": 12},
            }
        if calls["llm"] == 2:
            raise RuntimeError("simulated crash after first tool")
        return {"content": "现在可以继续了", "tool_calls": [], "usage": {"total_tokens": 20}}

    def fake_execute_tool(name, arguments, db=None, agent=None):
        calls["tools"].append(name)
        return '{"now":"2026-09-06T10:00:00Z"}'

    monkeypatch.setattr("app.main.complete_chat", fake_complete_chat)
    monkeypatch.setattr("app.main.execute_tool", fake_execute_tool)
    monkeypatch.setattr("app.main.agent_allows_tool", lambda *args, **kwargs: True)

    with TestClient(app) as client:
        created = client.post("/api/models", json={
            "name": f"ckpt-{uuid4().hex[:6]}",
            "provider": "OpenAI",
            "model_id": "demo-ckpt",
            "api_key": "sk-test-checkpoint",
            "enabled": True,
        })
        assert created.status_code == 201, created.text
        model_id = created.json()["id"]
        run = client.post("/api/playground/run", json={
            "agent_id": 1,
            "model_config_id": model_id,
            "message": "现在几点",
        })
        assert run.status_code == 200, run.text
        body = run.json()
        assert body["mode"] == "error"
        assert body["checkpoint"]
        assert body["checkpoint"]["resumable"] is True
        assert body["checkpoint"]["next"] == "llm"
        assert calls["tools"] == ["get_current_time"]
        resumed = client.post("/api/playground/resume", json={
            "agent_id": 1,
            "model_config_id": model_id,
            "session_id": body["session_id"],
        })
        assert resumed.status_code == 200, resumed.text
        done = resumed.json()
        assert done["mode"] == "ready"
        assert "现在可以继续了" in done["reply"]
        assert done.get("checkpoint") in (None, {})
        assert calls["tools"] == ["get_current_time"]
        assert calls["llm"] == 3
        client.delete(f"/api/models/{model_id}")


def test_eval_resume_only_failed_cases(monkeypatch):
    replies = {
        "你好": "你好",
        "失败用例": "还不行",
    }
    seen = []

    def fake_reply(agent, model, history, db, **kwargs):
        text = next((item["content"] for item in reversed(history) if item.get("role") == "user"), "")
        seen.append(text)
        return replies.get(text, text), "ready", [], {}

    monkeypatch.setattr("app.main.generate_chat_reply", fake_reply)

    with TestClient(app) as client:
        created = client.post("/api/datasets", json={"name": f"ckpt-eval-{uuid4().hex[:6]}"})
        assert created.status_code == 201, created.text
        dataset_id = created.json()["id"]
        client.post(f"/api/datasets/{dataset_id}/cases", json={"input": "你好", "expected": "你好"})
        client.post(f"/api/datasets/{dataset_id}/cases", json={"input": "失败用例", "expected": "三个工作日"})
        agents = client.get("/api/agents").json()
        launched = client.post("/api/evaluations/online", json={
            "agent_id": agents[0]["id"],
            "dataset_id": dataset_id,
            "scorer": "contains",
            "name": "检查点续跑",
        })
        assert launched.status_code == 200, launched.text
        run = launched.json()
        assert run["passed"] == 1
        assert run["failed"] == 1
        assert seen == ["你好", "失败用例"]
        replies["失败用例"] = "预计三个工作日到账"
        seen.clear()
        resumed = client.post(f"/api/evaluations/{run['id']}/resume")
        assert resumed.status_code == 200, resumed.text
        body = resumed.json()
        assert body["passed"] == 2
        assert body["failed"] == 0
        assert seen == ["失败用例"]
        again = client.post(f"/api/evaluations/{run['id']}/resume")
        assert again.status_code == 409
        client.delete(f"/api/datasets/{dataset_id}")
