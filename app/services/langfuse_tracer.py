"""Export playground traces to Langfuse. Missing keys keep local tracing only."""
from __future__ import annotations

import base64
import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Any, Optional
from uuid import uuid4

import httpx

from app.database import settings
from app.services.agentscope_adapter import studio_status

logger = logging.getLogger(__name__)

_project_id_cache = ""
_project_lookup_tried = False


def new_trace_id() -> str:
    """Langfuse requires a 32-char lowercase hex trace id (W3C / OTEL)."""
    return uuid4().hex


def new_span_id() -> str:
    return uuid4().hex[:16]


def langfuse_host() -> str:
    host = (getattr(settings, "langfuse_base_url", "") or getattr(settings, "langfuse_host", "") or "https://cloud.langfuse.com").strip()
    return host.rstrip("/") or "https://cloud.langfuse.com"


def langfuse_enabled() -> bool:
    if os.getenv("PYTEST_CURRENT_TEST") and os.getenv("LANGFUSE_TEST_EXPORT") != "1":
        return False
    return bool((getattr(settings, "langfuse_public_key", "") or "").strip() and (getattr(settings, "langfuse_secret_key", "") or "").strip())


def observability_status() -> dict[str, Any]:
    enabled = langfuse_enabled()
    return {
        "langfuse": {
            "enabled": enabled,
            "host": langfuse_host() if enabled else "",
            "configured": bool((getattr(settings, "langfuse_public_key", "") or "").strip()),
        },
        "studio": studio_status(),
    }


def _is_project_trace_url(url: str) -> bool:
    return bool(url) and "/project/" in url and "/traces/" in url


def _format_trace_url(project_id: str, trace_id: str) -> str:
    project_id = (project_id or "").strip()
    trace_id = (trace_id or "").strip()
    if not project_id or not trace_id:
        return ""
    return f"{langfuse_host()}/project/{project_id}/traces/{trace_id}"


def langfuse_project_id() -> str:
    global _project_id_cache, _project_lookup_tried
    configured = (getattr(settings, "langfuse_project_id", "") or "").strip()
    if configured:
        return configured
    if _project_id_cache or _project_lookup_tried:
        return _project_id_cache
    if not langfuse_enabled():
        return ""
    _project_lookup_tried = True
    try:
        with httpx.Client(timeout=5.0, headers=_auth_headers()) as client:
            project_id = _fetch_project_id(client)
        if project_id:
            _project_id_cache = project_id
        return project_id
    except Exception:
        logger.warning("Langfuse project lookup failed", exc_info=True)
        return ""


def _looks_like_langfuse_id(trace_id: str) -> bool:
    tid = (trace_id or "").strip().lower()
    return len(tid) == 32 and all(ch in "0123456789abcdef" for ch in tid)


def public_trace_url(stored: str, trace_id: str = "") -> str:
    """Return a clickable Langfuse UI URL, or empty if it would 404."""
    stored = (stored or "").strip()
    if _is_project_trace_url(stored):
        return stored
    tid = (trace_id or "").strip()
    if stored and "/trace/" in stored and "/traces/" not in stored:
        tid = stored.rstrip("/").split("/")[-1] or tid
    if not tid or not langfuse_enabled() or not _looks_like_langfuse_id(tid):
        return ""
    return _format_trace_url(langfuse_project_id(), tid)


def trace_url(trace_id: str) -> str:
    return public_trace_url("", trace_id)


def export_playground_run(
    *,
    trace_id: str,
    session_id: str,
    agent_name: str,
    model_name: str,
    model_id: str,
    message: str,
    reply: str,
    mode: str,
    spans: list[dict[str, Any]],
    usage: Optional[dict[str, Any]] = None,
    latency_ms: int = 0,
) -> str:
    if not langfuse_enabled():
        return ""
    try:
        url = _export_sdk(
            trace_id=trace_id,
            session_id=session_id,
            agent_name=agent_name,
            model_name=model_name,
            model_id=model_id,
            message=message,
            reply=reply,
            mode=mode,
            spans=spans,
            usage=usage or {},
        )
        if url is not None:
            return public_trace_url(url, trace_id)
    except Exception:
        logger.warning("Langfuse SDK export failed", exc_info=True)
    try:
        return _export_http(
            trace_id=trace_id,
            session_id=session_id,
            agent_name=agent_name,
            model_name=model_name,
            model_id=model_id,
            message=message,
            reply=reply,
            mode=mode,
            spans=spans,
            usage=usage or {},
            latency_ms=latency_ms,
        )
    except Exception:
        logger.warning("Langfuse HTTP export failed", exc_info=True)
        return ""


def _export_sdk(**payload: Any) -> Optional[str]:
    try:
        from langfuse import Langfuse
    except Exception:
        return None
    client = Langfuse(
        public_key=settings.langfuse_public_key,
        secret_key=settings.langfuse_secret_key,
        host=langfuse_host(),
    )
    trace = client.trace(
        id=payload["trace_id"],
        name="agentforge.playground",
        user_id="playground_user",
        session_id=payload["session_id"],
        input=payload["message"],
        output=payload["reply"],
        metadata={"agent": payload["agent_name"], "model": payload["model_name"], "mode": payload["mode"]},
        tags=["playground", payload["agent_name"], payload["mode"]],
    )
    usage = payload.get("usage") or {}
    for item in payload.get("spans") or []:
        name = item.get("title") or item.get("name") or "span"
        if item.get("kind") == "llm":
            trace.generation(
                name=name,
                model=payload.get("model_id") or payload.get("model_name"),
                input=payload["message"],
                output=payload["reply"],
                metadata={"detail": item.get("detail") or ""},
                usage={
                    "input": usage.get("prompt_tokens") or 0,
                    "output": usage.get("completion_tokens") or 0,
                    "total": usage.get("total_tokens") or 0,
                    "unit": "TOKENS",
                },
                level="ERROR" if item.get("status") == "error" else "DEFAULT",
            )
        else:
            trace.span(
                name=name,
                input=item.get("name"),
                output=item.get("detail") or item.get("status"),
                metadata={"kind": item.get("kind"), "duration_ms": item.get("duration_ms")},
                level="ERROR" if item.get("status") == "error" else "DEFAULT",
            )
    client.flush()
    try:
        url = client.get_trace_url() or ""
        if _is_project_trace_url(url):
            return url
    except Exception:
        logger.warning("Langfuse SDK URL lookup failed", exc_info=True)
    return _format_trace_url(langfuse_project_id(), payload["trace_id"])


def _export_http(
    *,
    trace_id: str,
    session_id: str,
    agent_name: str,
    model_name: str,
    model_id: str,
    message: str,
    reply: str,
    mode: str,
    spans: list[dict[str, Any]],
    usage: dict[str, Any],
    latency_ms: int,
) -> str:
    now = datetime.now(timezone.utc)
    batch = [
        {
            "id": str(uuid4()),
            "type": "trace-create",
            "timestamp": _iso(now),
            "body": {
                "id": trace_id,
                "timestamp": _iso(now),
                "name": "agentforge.playground",
                "userId": "playground_user",
                "sessionId": session_id,
                "input": message,
                "output": reply,
                "metadata": {"agent": agent_name, "model": model_name, "mode": mode},
                "tags": ["playground", agent_name, mode],
            },
        }
    ]
    elapsed = 0
    for item in spans:
        start = now + timedelta(milliseconds=elapsed)
        duration = int(item.get("duration_ms") or 0)
        elapsed += duration
        end = start + timedelta(milliseconds=max(duration, 1))
        event_type = "generation-create" if item.get("kind") == "llm" else "span-create"
        body: dict[str, Any] = {
            "id": new_span_id(),
            "traceId": trace_id,
            "name": item.get("title") or item.get("name") or "span",
            "startTime": _iso(start),
            "endTime": _iso(end),
            "input": item.get("name"),
            "output": item.get("detail") or item.get("status"),
            "metadata": {"kind": item.get("kind"), "duration_ms": duration},
            "level": "ERROR" if item.get("status") == "error" else "DEFAULT",
        }
        if event_type == "generation-create":
            body["model"] = model_id or model_name
            body["input"] = message
            body["output"] = reply
            body["usage"] = {
                "input": usage.get("prompt_tokens") or 0,
                "output": usage.get("completion_tokens") or 0,
                "total": usage.get("total_tokens") or (usage.get("prompt_tokens") or 0) + (usage.get("completion_tokens") or 0),
                "unit": "TOKENS",
            }
        batch.append({"id": str(uuid4()), "type": event_type, "timestamp": _iso(start), "body": body})
    global _project_id_cache, _project_lookup_tried
    project_id = (getattr(settings, "langfuse_project_id", "") or "").strip()
    with httpx.Client(timeout=5.0, headers=_auth_headers()) as client:
        response = client.post(
            f"{langfuse_host()}/api/public/ingestion",
            json={"batch": batch},
        )
        response.raise_for_status()
        if _ingestion_failed(response):
            logger.warning("Langfuse ingestion returned errors")
            return ""
        if not project_id:
            try:
                project_id = _fetch_project_id(client)
                _project_lookup_tried = True
            except Exception:
                logger.warning("Langfuse project lookup failed", exc_info=True)
                project_id = ""
    if project_id:
        _project_id_cache = project_id
    return _format_trace_url(project_id, trace_id)


def _auth_headers() -> dict[str, str]:
    token = base64.b64encode(f"{settings.langfuse_public_key}:{settings.langfuse_secret_key}".encode()).decode()
    return {"Authorization": f"Basic {token}", "Content-Type": "application/json"}


def _fetch_project_id(client: httpx.Client) -> str:
    response = client.get(f"{langfuse_host()}/api/public/projects")
    response.raise_for_status()
    payload = response.json() if response.content else {}
    rows = payload.get("data") or payload.get("projects") or []
    if not rows:
        return ""
    return str(rows[0].get("id") or "").strip()


def _ingestion_failed(response: httpx.Response) -> bool:
    try:
        payload = response.json()
    except Exception:
        return False
    if not isinstance(payload, dict):
        return False
    return bool(payload.get("errors"))


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
