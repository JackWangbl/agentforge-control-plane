from fastapi.testclient import TestClient

from app.main import app
from app.services.experiment_runtime import _split_prompt


def test_split_prompt_reads_expected_answer():
    assert _split_prompt("你好 || 助手") == ("你好", "助手")
    assert _split_prompt("只有问题") == ("只有问题", "")


def test_experiment_assign_is_sticky_and_records_playground():
    with TestClient(app) as client:
        agents = client.get("/api/agents").json()
        assert len(agents) >= 2
        created = client.post("/api/experiments", json={
            "name": "提示词分流单测",
            "assignment_unit": "session",
            "traffic_percent": 100,
            "variants": [
                {"key": "A", "name": "对照", "agent_id": agents[0]["id"], "weight": 50},
                {"key": "B", "name": "实验", "agent_id": agents[1]["id"], "weight": 50},
            ],
        })
        assert created.status_code == 201, created.text
        exp_id = created.json()["id"]
        assert created.json()["status"] == "draft"
        blocked = client.post(f"/api/experiments/{exp_id}/assign", json={"unit_key": "sess_ab_1"})
        assert blocked.status_code == 409
        started = client.post(f"/api/experiments/{exp_id}/start")
        assert started.status_code == 200
        assert started.json()["status"] == "running"
        first = client.post(f"/api/experiments/{exp_id}/assign", json={"unit_key": "sess_ab_1"})
        assert first.status_code == 200, first.text
        assert first.json()["holdout"] is False
        assert first.json()["variant_key"] in {"A", "B"}
        again = client.post(f"/api/experiments/{exp_id}/assign", json={"unit_key": "sess_ab_1"})
        assert again.json()["variant_key"] == first.json()["variant_key"]
        models = client.get("/api/models").json()
        run = client.post("/api/playground/run", json={
            "agent_id": agents[0]["id"],
            "model_config_id": models[0]["id"],
            "message": "你好，分流测试",
            "session_id": "sess_ab_1",
            "experiment_id": exp_id,
        })
        assert run.status_code == 200, run.text
        body = run.json()
        assert body["experiment"]["variant_key"] == first.json()["variant_key"]
        assert body["agent_id"] == first.json()["agent_id"]
        preview = client.get(f"/api/experiments/{exp_id}").json()
        assert preview["preview_samples"] == 200
        assert abs(sum(item["preview_share"] for item in preview["variants"]) - 100) < 1
        assert sum(item["preview_count"] for item in preview["variants"]) == preview["preview_samples"] - preview["preview_holdout"]
        report = preview
        assert sum(item["runs"] for item in report["variants"]) >= 1
        assert report["total_assignments"] >= 1
        assert any(item["actual_share"] > 0 for item in report["variants"])
        compared = client.post(f"/api/experiments/{exp_id}/compare", json={
            "prompts": ["你好，分流对比测试"],
            "scorer": "contains",
            "case_limit": 1,
        })
        assert compared.status_code == 200, compared.text
        snap = compared.json()["last_compare"]
        assert snap and snap["cases"]
        assert len(snap["cases"][0]["variants"]) >= 2
        assert snap["winner"] in {"A", "B"}
        assert snap.get("has_expected") is False
        assert all((item.get("pass_rate") in (None, 0) or item.get("ok_rate", 0) >= 0) for item in snap["summary"])
        again = client.get(f"/api/experiments/{exp_id}").json()
        assert again["last_compare"]["winner"] == snap["winner"]
        client.post(f"/api/experiments/{exp_id}/complete")
        client.delete(f"/api/experiments/{exp_id}")


def test_user_hash_keeps_same_agent_across_sessions():
    with TestClient(app) as client:
        agents = client.get("/api/agents").json()
        created = client.post("/api/experiments", json={
            "name": "用户哈希粘滞",
            "assignment_strategy": "user_hash",
            "traffic_percent": 100,
            "variants": [
                {"key": "A", "name": "对照", "agent_id": agents[0]["id"], "weight": 50},
                {"key": "B", "name": "实验", "agent_id": agents[1]["id"], "weight": 50},
            ],
        })
        assert created.status_code == 201, created.text
        assert created.json()["assignment_strategy"] == "user_hash"
        exp_id = created.json()["id"]
        client.post(f"/api/experiments/{exp_id}/start")
        first = client.post(f"/api/experiments/{exp_id}/assign", json={"user_key": "u_keep", "session_id": "s1"})
        second = client.post(f"/api/experiments/{exp_id}/assign", json={"user_key": "u_keep", "session_id": "s2"})
        assert first.status_code == 200, first.text
        assert second.json()["variant_key"] == first.json()["variant_key"]
        assert second.json()["agent_id"] == first.json()["agent_id"]
        client.delete(f"/api/experiments/{exp_id}")
