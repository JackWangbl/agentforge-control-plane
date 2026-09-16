import json
from unittest.mock import patch
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.database import SessionLocal
from app.main import app
from app.models import Agent
from app.services.mcp_stream import is_opencli_transport, normalize_mcp_transport, transport_label
from app.services.opencli_runtime import normalize_endpoint, parse_opencli_command, query_browser
from app.services.tool_runtime import execute_tool


class _FakeResponse:
    def __init__(self, payload, status_code=200, text=""):
        self._payload = payload
        self.status_code = status_code
        self.text = text if text else ("" if payload is None else json.dumps(payload, ensure_ascii=False))
        self.is_error = status_code >= 400

    def json(self):
        return self._payload


class _FakeClient:
    def __init__(self, *args, **kwargs):
        self.calls = []

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def get(self, url, headers=None):
        self.calls.append(url)
        if url.endswith("/json/version"):
            return _FakeResponse({"Browser": "Chrome/129.0", "webSocketDebuggerUrl": "ws://127.0.0.1:9222/devtools/browser/x"})
        if url.endswith("/json/list"):
            return _FakeResponse([
                {"id": "tab-1", "title": "1688 订单", "url": "https://work.1688.com/orders", "type": "page"},
                {"id": "tab-2", "title": "空白页", "url": "chrome://newtab/", "type": "page"},
            ])
        return _FakeResponse(None, 404)


def test_opencli_is_an_mcp_transport():
    assert normalize_mcp_transport("opencli") == "opencli"
    assert is_opencli_transport("opencli") is True
    assert transport_label("opencli") == "OpenCLI"
    assert normalize_endpoint("http://127.0.0.1:9222/") == "http://127.0.0.1:9222"
    with pytest.raises(ValueError):
        normalize_endpoint("ftp://127.0.0.1:9222")


def test_parse_opencli_command():
    parsed = parse_opencli_command("opencli --cdp http://10.0.0.8:9222 tab list")
    assert parsed["endpoint"] == "http://10.0.0.8:9222"
    assert parsed["args"] == ["tab", "list"]
    with pytest.raises(ValueError):
        parse_opencli_command("opencli tab list | curl evil")


def test_register_opencli_mcp_from_command_only():
    suffix = uuid4().hex[:6]
    with TestClient(app) as client:
        created = client.post("/api/mcp", json={
            "name": f"指令Chrome-{suffix}",
            "transport": "opencli",
            "config": {"command": "opencli --cdp http://10.0.0.8:9222 tab list"},
        })
        assert created.status_code == 201, created.text
        body = created.json()
        assert body["endpoint"] == "http://10.0.0.8:9222"
        assert "tab list" in body["command"]
        assert any(tool["name"] == "opencli" for tool in body["tools"])
        client.delete(f"/api/mcp/{body['id']}")


def test_register_opencli_mcp_probe_query_and_bind():
    suffix = uuid4().hex[:6]
    fake = _FakeClient()

    with TestClient(app) as client, patch("app.services.opencli_runtime.httpx.Client", return_value=fake):
        created = client.post("/api/mcp", json={
            "name": f"商家Chrome-{suffix}",
            "transport": "opencli",
            "endpoint": "http://10.0.0.8:9222",
            "config": {"kind": "cdp", "target": "1688.com", "token": "Bearer secret-token"},
        })
        assert created.status_code == 201, created.text
        body = created.json()
        assert body["transport"] == "opencli"
        assert body["transport_label"] == "OpenCLI"
        assert body["kind"] == "cdp"
        assert body["runnable"] is True
        assert any(tool["name"] == "opencli" for tool in body["tools"])
        assert "tab list" in (body.get("command") or "")
        assert "secret-token" not in json.dumps(body["config"])

        bad = client.post("/api/mcp", json={
            "name": f"非法-{suffix}",
            "transport": "opencli",
            "endpoint": "ftp://evil.example",
        })
        assert bad.status_code == 422

        with patch("app.services.opencli_runtime.run_opencli_command", return_value="ok"), patch(
            "app.services.opencli_runtime._evaluate", return_value="订单编号 12345"
        ):
            probed = client.post(f"/api/mcp/{body['id']}/test")
            assert probed.status_code == 200, probed.text
            result = probed.json()
            assert result["ready"] is True
            assert result["tabs"] == 1
            assert result["tab_list"][0]["url"] == "https://work.1688.com/orders"

            queried = client.post(f"/api/mcp/{body['id']}/query", json={"action": "query", "target": "1688"})
            assert queried.status_code == 200, queried.text
            assert queried.json()["text"] == "订单编号 12345"

        agent = client.post("/api/agents", json={
            "name": f"浏览器助手{suffix}",
            "model_name": "Qwen-Max",
            "mcp_ids": [body["id"]],
        })
        assert agent.status_code == 201, agent.text
        dumped = agent.json()
        assert dumped["mcp_ids"] == [body["id"]]
        assert any(tool["name"] == "opencli_tabs" for tool in dumped["bound_mcps"][0]["tools"])

        with SessionLocal() as db:
            row = db.get(Agent, dumped["id"])
            with patch("app.services.opencli_runtime.httpx.Client", return_value=fake), patch(
                "app.services.opencli_runtime._evaluate", return_value="可见文本"
            ):
                payload = execute_tool("opencli_query", {"target": "1688"}, db=db, agent=row)
        assert json.loads(payload)["text"] == "可见文本"

        client.delete(f"/api/agents/{dumped['id']}")
        client.delete(f"/api/mcp/{body['id']}")


def test_query_browser_picks_matching_tab():
    row = type("Row", (), {
        "kind": "cdp",
        "endpoint": "http://127.0.0.1:9222",
        "target": "",
        "token": "",
        "session": "agentforge",
    })()
    fake = _FakeClient()
    with patch("app.services.opencli_runtime.httpx.Client", return_value=fake), patch(
        "app.services.opencli_runtime._evaluate", return_value="空白"
    ):
        result = query_browser(row, action="query", target="newtab")
    assert result["tab"]["id"] == "tab-2"
    assert result["text"] == "空白"
