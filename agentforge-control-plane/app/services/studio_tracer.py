"""Send playground runs to AgentScope Studio (register run + OTLP traces)."""
from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

import httpx

from app.services.agentscope_adapter import agentscope_studio_url

logger = logging.getLogger(__name__)


def studio_export_enabled() -> bool:
    if os.getenv("PYTEST_CURRENT_TEST") and os.getenv("STUDIO_TEST_EXPORT") != "1":
        return False
    return True


def export_playground_to_studio(
    *,
    agent_name: str,
    session_id: str,
    trace_id: str,
    message: str,
    reply: str,
    mode: str,
    model_name: str,
    model_id: str,
    spans: list[dict[str, Any]],
    usage: dict[str, Any],
    latency_ms: int,
    title: str = "",
) -> bool:
    if not studio_export_enabled():
        return False
    try:
        _register_run(agent_name=agent_name, session_id=session_id, title=title or message)
        _post_traces(
            agent_name=agent_name,
            session_id=session_id,
            trace_id=trace_id,
            message=message,
            reply=reply,
            mode=mode,
            model_name=model_name,
            model_id=model_id,
            spans=spans,
            usage=usage or {},
            latency_ms=latency_ms,
        )
        return True
    except Exception:
        logger.warning("AgentScope Studio export failed", exc_info=True)
        return False


def _register_run(*, agent_name: str, session_id: str, title: str) -> None:
    payload = {
        "id": session_id,
        "project": agent_name or "AgentForge",
        "name": (title or session_id)[:80],
        "timestamp": _iso(datetime.now(timezone.utc)),
        "pid": os.getpid(),
        "status": "done",
    }
    with httpx.Client(timeout=2.0) as client:
        response = client.post(f"{agentscope_studio_url()}/trpc/registerRun", json=payload)
        if response.status_code in {200, 201}:
            return
        # Duplicate run id is expected when the same session continues.
        if response.status_code in {400, 409, 500}:
            logger.debug("Studio registerRun skipped: %s", response.status_code)
            return
        response.raise_for_status()


def _post_traces(
    *,
    agent_name: str,
    session_id: str,
    trace_id: str,
    message: str,
    reply: str,
    mode: str,
    model_name: str,
    model_id: str,
    spans: list[dict[str, Any]],
    usage: dict[str, Any],
    latency_ms: int,
) -> None:
    now_ns = int(datetime.now(timezone.utc).timestamp() * 1_000_000_000)
    duration_ns = max(int(latency_ms) * 1_000_000, 1_000_000)
    parent_id = uuid4().hex[:16]
    otel_spans = [
        _span(
            trace_id=trace_id,
            span_id=parent_id,
            name=f"invoke_agent {agent_name}",
            start_ns=now_ns,
            end_ns=now_ns + duration_ns,
            attrs={
                "gen_ai.conversation.id": session_id,
                "gen_ai.operation.name": "invoke_agent",
                "gen_ai.agent.name": agent_name,
                "agentscope.function.input": message,
                "agentscope.function.output": reply,
            },
            error=mode == "error",
        )
    ]
    elapsed = 0
    for item in spans:
        kind = item.get("kind") or "span"
        child_ms = max(int(item.get("duration_ms") or 1), 1)
        start = now_ns + elapsed * 1_000_000
        end = start + child_ms * 1_000_000
        elapsed += child_ms
        if kind == "llm":
            otel_spans.append(
                _span(
                    trace_id=trace_id,
                    span_id=uuid4().hex[:16],
                    parent_id=parent_id,
                    name=f"chat {model_id or model_name}",
                    start_ns=start,
                    end_ns=end,
                    attrs={
                        "gen_ai.conversation.id": session_id,
                        "gen_ai.operation.name": "chat",
                        "gen_ai.request.model": model_id or model_name,
                        "gen_ai.usage.input_tokens": int(usage.get("prompt_tokens") or 0),
                        "gen_ai.usage.output_tokens": int(usage.get("completion_tokens") or 0),
                        "agentscope.function.input": message,
                        "agentscope.function.output": reply,
                    },
                    error=item.get("status") == "error" or mode == "error",
                )
            )
        elif kind == "tool":
            name = item.get("title") or item.get("name") or "tool"
            otel_spans.append(
                _span(
                    trace_id=trace_id,
                    span_id=uuid4().hex[:16],
                    parent_id=parent_id,
                    name=f"execute_tool {name}",
                    start_ns=start,
                    end_ns=end,
                    attrs={
                        "gen_ai.conversation.id": session_id,
                        "gen_ai.operation.name": "execute_tool",
                        "gen_ai.tool.name": name,
                        "agentscope.function.output": item.get("detail") or "",
                    },
                    error=item.get("status") == "error",
                )
            )
    payload = {
        "resourceSpans": [
            {
                "resource": {
                    "attributes": [
                        _attr("service.name", agent_name or "agentforge"),
                        _attr("service.namespace", "agentforge.playground"),
                    ]
                },
                "scope_spans": [
                    {
                        "scope": {"name": "agentforge.playground", "version": "0.1.0"},
                        "spans": otel_spans,
                    }
                ],
            }
        ]
    }
    with httpx.Client(timeout=2.0) as client:
        response = client.post(
            f"{agentscope_studio_url()}/v1/traces",
            headers={"Content-Type": "application/json"},
            json=payload,
        )
        response.raise_for_status()


def _span(
    *,
    trace_id: str,
    span_id: str,
    name: str,
    start_ns: int,
    end_ns: int,
    attrs: dict[str, Any],
    parent_id: str = "",
    error: bool = False,
) -> dict[str, Any]:
    body: dict[str, Any] = {
        "trace_id": trace_id,
        "span_id": span_id,
        "name": name,
        "kind": 1,
        "start_time_unix_nano": str(start_ns),
        "end_time_unix_nano": str(end_ns),
        "attributes": [_attr(key, value) for key, value in attrs.items()],
        "status": {"code": 2 if error else 1, "message": "error" if error else ""},
    }
    if parent_id:
        body["parent_span_id"] = parent_id
    return body


def _attr(key: str, value: Any) -> dict[str, Any]:
    if isinstance(value, bool):
        return {"key": key, "value": {"bool_value": value}}
    if isinstance(value, int) and not isinstance(value, bool):
        return {"key": key, "value": {"int_value": value}}
    if isinstance(value, float):
        return {"key": key, "value": {"double_value": value}}
    return {"key": key, "value": {"string_value": str(value)}}


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
