"""MCP Streamable HTTP / SSE probe and tool calls."""
from __future__ import annotations

import json
from typing import Any, Optional
from urllib.parse import urlparse

import httpx

from app.models import McpServer

PROTOCOL_VERSIONS = ("2025-03-26", "2024-11-05")
HTTP_STREAM_ALIASES = {"streamable_http", "http", "http_stream", "stream"}
ALLOWED_TRANSPORTS = {"stdio", "sse", "streamable_http"}


def normalize_mcp_transport(value: Optional[str]) -> str:
    raw = (value or "stdio").strip().lower().replace("-", "_")
    if raw in HTTP_STREAM_ALIASES:
        return "streamable_http"
    if raw == "sse":
        return "sse"
    if raw == "stdio":
        return "stdio"
    return raw


def is_http_stream_transport(value: Optional[str]) -> bool:
    return normalize_mcp_transport(value) == "streamable_http"


def transport_label(value: Optional[str]) -> str:
    kind = normalize_mcp_transport(value)
    return {"streamable_http": "HTTP Stream", "sse": "SSE", "stdio": "StdIO"}.get(kind, (value or "").upper())


def agentscope_transport(value: Optional[str]) -> str:
    kind = normalize_mcp_transport(value)
    return "streamable_http" if kind == "streamable_http" else kind


def request_headers(row: McpServer) -> dict[str, str]:
    headers: dict[str, str] = {}
    config = row.config or {}
    extra = config.get("headers")
    if isinstance(extra, dict):
        for key, item in extra.items():
            if key and item is not None and str(item).strip():
                headers[str(key)] = str(item).strip()
    token = str(config.get("api_key") or config.get("token") or "").strip()
    if token and "Authorization" not in headers:
        headers["Authorization"] = token if token.lower().startswith("bearer ") else f"Bearer {token}"
    return headers


def merge_mcp_config(existing: Optional[dict[str, Any]], incoming: Optional[dict[str, Any]]) -> dict[str, Any]:
    base = dict(existing or {})
    extra = dict(incoming or {})
    headers = dict(base.get("headers") or {})
    if isinstance(extra.get("headers"), dict):
        headers.update({str(k): str(v) for k, v in extra.pop("headers").items() if v is not None})
    if headers:
        base["headers"] = headers
    base.update(extra)
    return base


def _rpc(method: str, params: Optional[dict[str, Any]] = None, req_id: int = 1) -> dict[str, Any]:
    payload: dict[str, Any] = {"jsonrpc": "2.0", "id": req_id, "method": method}
    if params is not None:
        payload["params"] = params
    return payload


def _parse_sse(text: str) -> dict[str, Any]:
    blocks: list[str] = []
    current: list[str] = []
    for line in (text or "").splitlines():
        if line.startswith("data:"):
            current.append(line[5:].lstrip())
        elif line.strip() == "" and current:
            blocks.append("\n".join(current))
            current = []
    if current:
        blocks.append("\n".join(current))
    for block in reversed(blocks):
        if not block.strip():
            continue
        try:
            data = json.loads(block)
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict):
            return data
    return {}


def _parse_response(response: httpx.Response) -> dict[str, Any]:
    ctype = (response.headers.get("content-type") or "").lower()
    text = response.text or ""
    if "text/event-stream" in ctype or text.lstrip().startswith(("event:", "data:")):
        return _parse_sse(text)
    if not text.strip():
        return {}
    try:
        data = response.json()
    except Exception:
        return _parse_sse(text)
    return data if isinstance(data, dict) else {}


def _rpc_error(body: dict[str, Any]) -> str:
    err = body.get("error")
    if isinstance(err, dict):
        return str(err.get("message") or err)
    if err:
        return str(err)
    return ""


def _tools_from_result(body: dict[str, Any]) -> list[dict[str, Any]]:
    result = body.get("result") if isinstance(body.get("result"), dict) else {}
    raw = result.get("tools") if isinstance(result, dict) else None
    if not isinstance(raw, list):
        return []
    tools = []
    for item in raw:
        if not isinstance(item, dict) or not item.get("name"):
            continue
        schema = item.get("inputSchema") or item.get("input_schema") or item.get("parameters") or {"type": "object", "properties": {}}
        tools.append({
            "name": str(item["name"]),
            "description": str(item.get("description") or ""),
            "parameters": schema if isinstance(schema, dict) else {"type": "object", "properties": {}},
        })
    return tools


def _post(client: httpx.Client, url: str, payload: dict[str, Any], headers: dict[str, str]) -> tuple[httpx.Response, dict[str, Any]]:
    response = client.post(url, json=payload, headers=headers)
    session = response.headers.get("mcp-session-id") or response.headers.get("Mcp-Session-Id")
    if session:
        headers["Mcp-Session-Id"] = session
    if response.status_code >= 400:
        body = _parse_response(response)
        detail = _rpc_error(body) or (response.text or str(response.status_code))[:240]
        raise RuntimeError(f"{response.status_code} {detail}")
    return response, _parse_response(response)


def _streamable_session(row: McpServer) -> tuple[list[dict[str, Any]], str]:
    url = (row.endpoint or "").strip()
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise RuntimeError("HTTP Stream 需要填写 http(s) 服务地址，例如 https://host/mcp")
    headers = {
        "Accept": "application/json, text/event-stream",
        "Content-Type": "application/json",
        **request_headers(row),
    }
    last_error = ""
    with httpx.Client(timeout=12.0, follow_redirects=True) as client:
        for version in PROTOCOL_VERSIONS:
            try:
                _response, body = _post(client, url, _rpc("initialize", {
                    "protocolVersion": version,
                    "capabilities": {},
                    "clientInfo": {"name": "agentforge-control-plane", "version": "0.1.0"},
                }), headers)
                err = _rpc_error(body)
                if err:
                    last_error = err
                    continue
                client.post(url, json={"jsonrpc": "2.0", "method": "notifications/initialized"}, headers=headers)
                _listed, listed_body = _post(client, url, _rpc("tools/list", {}, 2), headers)
                err = _rpc_error(listed_body)
                if err:
                    last_error = err
                    continue
                return _tools_from_result(listed_body), version
            except Exception as exc:
                last_error = str(exc)
        raise RuntimeError(last_error or "HTTP Stream 握手失败")


def probe_streamable_http(row: McpServer) -> dict[str, Any]:
    tools, version = _streamable_session(row)
    return {
        "tools": tools,
        "protocol": version,
        "message": f"{row.name} 已通过 HTTP Stream 连通，发现 {len(tools)} 个工具。",
    }


def call_streamable_http_tool(row: McpServer, name: str, arguments: dict[str, Any]) -> str:
    url = (row.endpoint or "").strip()
    headers = {
        "Accept": "application/json, text/event-stream",
        "Content-Type": "application/json",
        **request_headers(row),
    }
    with httpx.Client(timeout=30.0, follow_redirects=True) as client:
        _init, body = _post(client, url, _rpc("initialize", {
            "protocolVersion": PROTOCOL_VERSIONS[0],
            "capabilities": {},
            "clientInfo": {"name": "agentforge-control-plane", "version": "0.1.0"},
        }), headers)
        if _rpc_error(body):
            raise RuntimeError(_rpc_error(body))
        client.post(url, json={"jsonrpc": "2.0", "method": "notifications/initialized"}, headers=headers)
        _call, result = _post(client, url, _rpc("tools/call", {"name": name, "arguments": arguments or {}}, 3), headers)
        err = _rpc_error(result)
        if err:
            return json.dumps({"error": err}, ensure_ascii=False)
        payload = result.get("result") if isinstance(result.get("result"), dict) else result
        return json.dumps(payload, ensure_ascii=False) if not isinstance(payload, str) else payload


def apply_discovered_tools(row: McpServer, tools: list[dict[str, Any]]) -> None:
    row.tools_count = len(tools)
    config = dict(row.config or {})
    config["tools"] = tools
    config["kind"] = config.get("kind") or "http"
    row.config = config
