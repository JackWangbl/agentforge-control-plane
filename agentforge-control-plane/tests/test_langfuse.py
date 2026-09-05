from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

from app.database import settings
from app.main import app
from app.services.langfuse_tracer import export_playground_run, langfuse_enabled, public_trace_url


def test_observability_endpoint_without_keys():
    with TestClient(app) as client:
        body = client.get("/api/observability").json()
        assert "langfuse" in body
        assert body["langfuse"]["enabled"] is False
        assert body["studio"]["url"].startswith("http")
        assert "reachable" in body["studio"]
        health = client.get("/api/health").json()
        assert health["status"] == "ok"
        assert health["langfuse"]["enabled"] is False
        assert "studio" in health


def test_playground_uses_agent_workspace_not_langfuse():
    with TestClient(app) as client:
        run = client.post(
            "/api/playground/run",
            json={"agent_id": 1, "model_config_id": 1, "message": "工作空间回归"},
        )
        assert run.status_code == 200, run.text
        body = run.json()
        assert "langfuse_url" not in body
        assert "langfuse_enabled" not in body
        assert body["workspace"]
        workspace = client.get("/api/agents/1/workspace")
        assert workspace.status_code == 200, workspace.text
        payload = workspace.json()
        assert payload["path"] == body["workspace"]
        assert payload["session_count"] >= 1
        assert any(item["session_id"] == body["session_id"] for item in payload["sessions"])


def test_export_http_posts_ingestion_batch(monkeypatch):
    monkeypatch.setenv("LANGFUSE_TEST_EXPORT", "1")
    monkeypatch.setattr(settings, "langfuse_public_key", "pk-test")
    monkeypatch.setattr(settings, "langfuse_secret_key", "sk-test")
    monkeypatch.setattr(settings, "langfuse_host", "https://cloud.langfuse.com")
    monkeypatch.setattr(settings, "langfuse_project_id", "proj_test")
    assert langfuse_enabled() is True

    response = MagicMock()
    response.raise_for_status.return_value = None
    response.content = b"{}"
    response.json.return_value = {"successes": [{"id": "1", "status": 201}], "errors": []}
    client = MagicMock()
    client.__enter__.return_value = client
    client.__exit__.return_value = False
    client.post.return_value = response

    trace_id = "a" * 32
    with patch("app.services.langfuse_tracer._export_sdk", return_value=None), patch(
        "app.services.langfuse_tracer.httpx.Client", return_value=client
    ):
        url = export_playground_run(
            trace_id=trace_id,
            session_id="debug_test",
            agent_name="个人助手",
            model_name="本地推理集群",
            model_id="deepseek-v4-flash",
            message="你好",
            reply="收到",
            mode="preview",
            spans=[
                {"name": "agent.resolve", "title": "解析 Agent", "kind": "agent", "status": "ok", "duration_ms": 8, "detail": ""},
                {"name": "model.chat", "title": "调用模型", "kind": "llm", "status": "ok", "duration_ms": 20, "detail": "preview"},
            ],
            usage={"prompt_tokens": 12, "completion_tokens": 8},
            latency_ms=30,
        )
    assert url == f"https://cloud.langfuse.com/project/proj_test/traces/{trace_id}"
    assert client.post.called
    sent = client.post.call_args
    assert sent.args[0].endswith("/api/public/ingestion")
    batch = sent.kwargs["json"]["batch"]
    assert batch[0]["type"] == "trace-create"
    assert batch[0]["body"]["id"] == trace_id
    assert any(item["type"] == "generation-create" for item in batch)


def test_public_trace_url_rewrites_legacy_path(monkeypatch):
    monkeypatch.setattr(settings, "langfuse_public_key", "pk-test")
    monkeypatch.setattr(settings, "langfuse_secret_key", "sk-test")
    monkeypatch.setattr(settings, "langfuse_host", "https://cloud.langfuse.com")
    monkeypatch.setattr(settings, "langfuse_project_id", "proj_test")
    monkeypatch.setenv("LANGFUSE_TEST_EXPORT", "1")
    hex_id = "b" * 32
    url = public_trace_url(f"https://cloud.langfuse.com/trace/{hex_id}", hex_id)
    assert url == f"https://cloud.langfuse.com/project/proj_test/traces/{hex_id}"
    assert public_trace_url("https://cloud.langfuse.com/trace/tr_debug_old", "tr_debug_old") == ""
    assert public_trace_url("https://cloud.langfuse.com/project/proj_test/traces/abc", "abc").endswith("/traces/abc")


def test_studio_export_registers_run_and_posts_traces(monkeypatch):
    from app.services.studio_tracer import export_playground_to_studio

    monkeypatch.setenv("STUDIO_TEST_EXPORT", "1")
    register = MagicMock()
    register.status_code = 200
    traces = MagicMock()
    traces.status_code = 200
    traces.raise_for_status.return_value = None
    client = MagicMock()
    client.__enter__.return_value = client
    client.__exit__.return_value = False
    client.post.side_effect = [register, traces]
    with patch("app.services.studio_tracer.httpx.Client", return_value=client):
        ok = export_playground_to_studio(
            agent_name="个人助手",
            session_id="debug_studio",
            trace_id="c" * 32,
            message="你好",
            reply="收到",
            mode="ready",
            model_name="本地推理集群",
            model_id="deepseek-v4-flash",
            spans=[{"kind": "llm", "title": "调用模型", "status": "ok", "duration_ms": 12}],
            usage={"prompt_tokens": 4, "completion_tokens": 3},
            latency_ms=20,
            title="你好",
        )
    assert ok is True
    assert client.post.call_count == 2
    run_call, trace_call = client.post.call_args_list
    assert run_call.args[0].endswith("/trpc/registerRun")
    assert run_call.kwargs["json"]["project"] == "个人助手"
    assert run_call.kwargs["json"]["id"] == "debug_studio"
    assert trace_call.args[0].endswith("/v1/traces")
    batch = trace_call.kwargs["json"]["resourceSpans"][0]["scope_spans"][0]["spans"]
    assert any(item["name"].startswith("invoke_agent") for item in batch)
    assert any(item["name"].startswith("chat") for item in batch)
