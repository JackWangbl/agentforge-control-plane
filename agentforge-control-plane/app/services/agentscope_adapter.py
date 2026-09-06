"""AgentScope 2.0 integration boundary.

Imports stay lazy so the control plane can boot before model credentials are configured.
"""
import os
from typing import Any, Optional

import httpx

from app.database import settings

DEFAULT_STUDIO_URL = "http://127.0.0.1:3000"


def agentscope_studio_url() -> str:
    url = (getattr(settings, "agentscope_studio_url", "") or os.getenv("AGENTSCOPE_STUDIO_URL") or "").strip()
    return url.rstrip("/") or DEFAULT_STUDIO_URL


def studio_status() -> dict[str, Any]:
    url = agentscope_studio_url()
    configured = bool((getattr(settings, "agentscope_studio_url", "") or os.getenv("AGENTSCOPE_STUDIO_URL") or "").strip())
    return {"url": url, "reachable": _probe_studio(url), "configured": configured}


def _probe_studio(url: str) -> bool:
    if os.getenv("PYTEST_CURRENT_TEST"):
        return False
    try:
        with httpx.Client(timeout=1.2, follow_redirects=True) as client:
            response = client.get(url)
        return response.status_code < 500
    except Exception:
        return False


def initialize_agentscope() -> bool:
    try:
        import agentscope
        kwargs: dict[str, str] = {}
        kwargs["studio_url"] = agentscope_studio_url()
        if tracing_url := os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT"):
            kwargs["tracing_url"] = tracing_url
        # AgentScope 2.x no longer exposes the legacy top-level ``init`` API.
        # Importing the package is sufficient for the integrations used by this
        # control plane; retain the call for older compatible releases.
        initializer = getattr(agentscope, "init", None)
        if callable(initializer):
            initializer(**kwargs)
        return True
    except ImportError:
        return False


async def build_mcp_client(config: dict[str, Any]) -> Any:
    from agentscope.mcp import HttpStatelessClient, StdIOStatefulClient
    from app.services.mcp_stream import agentscope_transport, normalize_mcp_transport

    transport = normalize_mcp_transport(config.get("transport"))
    if transport in {"streamable_http", "sse"}:
        return HttpStatelessClient(
            name=config["name"],
            transport=agentscope_transport(transport),
            url=config["endpoint"],
        )
    return StdIOStatefulClient(name=config["name"], command=config["endpoint"])


def create_toolkit() -> Any:
    from agentscope.tool import Toolkit
    return Toolkit()


def complete_chat(
    *,
    model_id: str,
    base_url: str,
    api_key: str,
    temperature: float,
    system_prompt: str,
    messages: list[dict[str, Any]],
    tools: Optional[list[dict[str, Any]]] = None,
) -> dict[str, Any]:
    import httpx

    root = (base_url or "https://api.openai.com/v1").rstrip("/")
    payload: dict[str, Any] = {
        "model": normalize_chat_model(model_id, root),
        "temperature": temperature,
        "messages": [{"role": "system", "content": system_prompt}, *messages],
    }
    if tools:
        payload["tools"] = tools
        payload["tool_choice"] = "auto"
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    with httpx.Client(timeout=45.0) as client:
        response = client.post(f"{root}/chat/completions", json=payload, headers=headers)
    if response.is_error:
        detail = http_error_detail(response)
        if api_key:
            detail = detail.replace(api_key, "****")
        raise RuntimeError(f"{response.status_code} {detail}")
    data = response.json()
    message = (data.get("choices") or [{}])[0].get("message") or {}
    content = str(message.get("content") or "").strip()
    usage = data.get("usage") if isinstance(data.get("usage"), dict) else {}
    return {"content": content, "tool_calls": message.get("tool_calls") or [], "usage": usage}


DEEPSEEK_MODEL_ALIASES = {
    "deepseek-v4": "deepseek-v4-flash",
    "deepseek-v3": "deepseek-v4-flash",
    "deepseek-chat": "deepseek-v4-flash",
    "deepseek-reasoner": "deepseek-v4-pro",
}


def normalize_chat_model(model_id: str, base_url: str = "") -> str:
    mid = (model_id or "").strip()
    key = mid.lower()
    if key in DEEPSEEK_MODEL_ALIASES and ("deepseek.com" in (base_url or "") or key.startswith("deepseek")):
        return DEEPSEEK_MODEL_ALIASES[key]
    return mid


def http_error_detail(response: Any) -> str:
    try:
        data = response.json()
        err = data.get("error") if isinstance(data, dict) else None
        if isinstance(err, dict):
            return str(err.get("message") or err)
        if isinstance(data, dict) and data.get("message"):
            return str(data["message"])
        return (response.text or str(response.status_code))[:500]
    except Exception:
        return (getattr(response, "text", None) or str(getattr(response, "status_code", "")))[:500]
