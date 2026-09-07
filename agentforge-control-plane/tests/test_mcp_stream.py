from unittest.mock import MagicMock, patch
from uuid import uuid4

from fastapi.testclient import TestClient

from app.main import app
from app.services.mcp_stream import normalize_mcp_transport, transport_label


def test_normalize_http_aliases_to_streamable_http():
    assert normalize_mcp_transport("http") == "streamable_http"
    assert normalize_mcp_transport("HTTP-Stream") == "streamable_http"
    assert normalize_mcp_transport("streamable_http") == "streamable_http"
    assert normalize_mcp_transport("sse") == "sse"
    assert transport_label("http") == "HTTP Stream"


class _FakeResponse:
    def __init__(self, payload, status_code=200, headers=None):
        self._payload = payload
        self.status_code = status_code
        self.headers = headers or {"content-type": "application/json"}
        self.text = "" if payload is None else __import__("json").dumps(payload)

    def json(self):
        return self._payload


def test_create_http_stream_mcp_and_probe_tools():
    suffix = uuid4().hex[:6]
    initialize = {"jsonrpc": "2.0", "id": 1, "result": {"protocolVersion": "2025-03-26", "capabilities": {}}}
    listed = {
        "jsonrpc": "2.0",
        "id": 2,
        "result": {
            "tools": [
                {"name": "maps_geo", "description": "地址转坐标", "inputSchema": {"type": "object", "properties": {"address": {"type": "string"}}}},
            ]
        },
    }
    calls = []

    def fake_post(url, json=None, headers=None):
        calls.append(json.get("method") if isinstance(json, dict) else "")
        method = (json or {}).get("method")
        if method == "initialize":
            return _FakeResponse(initialize, headers={"content-type": "application/json", "mcp-session-id": "sess-1"})
        if method == "tools/list":
            return _FakeResponse(listed)
        return _FakeResponse({})

    fake_client = MagicMock()
    fake_client.__enter__.return_value.post.side_effect = fake_post
    fake_client.__exit__.return_value = False

    with TestClient(app) as client, patch("app.services.mcp_stream.httpx.Client", return_value=fake_client):
        created = client.post("/api/mcp", json={
            "name": f"高德-{suffix}",
            "transport": "http",
            "endpoint": "https://mcp.amap.com/mcp",
            "config": {"headers": {"Authorization": "Bearer test-key"}},
        })
        assert created.status_code == 201, created.text
        body = created.json()
        assert body["transport"] == "streamable_http"
        assert body["transport_label"] == "HTTP Stream"
        probed = client.post(f"/api/mcp/{body['id']}/test")
        assert probed.status_code == 200, probed.text
        result = probed.json()
        assert result["ready"] is True
        assert any(tool["name"] == "maps_geo" for tool in result["tools"])
        assert "HTTP Stream" in result["message"]
        listed_row = next(row for row in client.get("/api/mcp").json() if row["id"] == body["id"])
        assert listed_row["tools_count"] == 1
        assert listed_row["runnable"] is True
        assert "initialize" in calls
        assert "tools/list" in calls
        client.delete(f"/api/mcp/{body['id']}")
