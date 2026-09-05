from fastapi.testclient import TestClient

from app.main import app
from app.services.dataset_import import parse_dataset_text
from app.services.eval_scorer import score_case


def test_parse_csv_and_jsonl():
    csv_text = "id,问题,期望,tags\n1,退款多久到账,三个工作日,退款\n2,你好,你好,问候\n"
    parsed = parse_dataset_text(csv_text, "demo.csv")
    assert parsed["count"] == 2
    assert parsed["cases"][0]["input"] == "退款多久到账"
    assert parsed["cases"][0]["expected"] == "三个工作日"
    jsonl = '{"id":"a","input":"hi","expected":"hello"}\n{"query":"时间"}\n'
    parsed = parse_dataset_text(jsonl, "demo.jsonl")
    assert parsed["count"] == 2
    assert parsed["cases"][1]["expected"] == ""


def test_rule_scorers():
    assert score_case("contains", "三个工作日", "预计三个工作日内到账")["status"] == "passed"
    assert score_case("contains", "三个工作日", "明天就到")["status"] == "failed"
    assert score_case("exact", "OK", "ok")["status"] == "passed"
    assert score_case("regex", r"hello", "say Hello there")["status"] == "passed"
    assert score_case("contains", "", "任意输出")["status"] == "skipped"


def test_dataset_import_and_online_eval():
    with TestClient(app) as client:
        created = client.post("/api/datasets", json={"name": "评测集-单测"})
        assert created.status_code == 201, created.text
        dataset_id = created.json()["id"]
        file_body = "id,input,expected\n1,你好,你好\n2,继续,继续\n"
        imported = client.post(
            "/api/datasets/import",
            files={"file": ("cases.csv", file_body, "text/csv")},
            data={"dataset_id": str(dataset_id), "on_duplicate": "skip"},
        )
        assert imported.status_code == 200, imported.text
        assert imported.json()["added"] == 2
        agents = client.get("/api/agents").json()
        assert agents
        online = client.post("/api/evaluations/online", json={
            "agent_id": agents[0]["id"],
            "dataset_id": dataset_id,
            "scorer": "contains",
            "name": "在线抽检单测",
        })
        assert online.status_code == 200, online.text
        body = online.json()
        assert body["mode"] == "online"
        assert body["status"] == "completed"
        assert body["total"] == 2
        assert len(body["results"]) == 2
        listed = client.get("/api/evaluations").json()
        assert any(row["id"] == body["id"] for row in listed)
        report = client.get(f"/api/evaluations/{body['id']}").json()
        assert report["results"]
        export = client.get(f"/api/evaluations/{body['id']}/export.csv")
        assert export.status_code == 200
        assert "input" in export.text
        offline = client.post("/api/evaluations", json={
            "agent_id": agents[0]["id"],
            "dataset_id": dataset_id,
            "scorer": "contains",
            "name": "离线单测",
        })
        assert offline.status_code == 201, offline.text
        from app.services.eval_runner import execute_run
        execute_run(offline.json()["id"])
        done = client.get(f"/api/evaluations/{offline.json()['id']}").json()
        assert done["status"] == "completed"
        client.delete(f"/api/datasets/{dataset_id}")
