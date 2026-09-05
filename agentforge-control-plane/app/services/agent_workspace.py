"""Per-agent filesystem workspace for playground sessions, traces, and config."""
from __future__ import annotations

import json
import os
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional
from uuid import uuid4

from app.database import ROOT
from app.models import Agent


def workspaces_root() -> Path:
    raw = (os.getenv("WORKSPACES_DIR") or "").strip()
    return Path(raw).expanduser() if raw else ROOT / "workspaces"


def new_trace_id() -> str:
    return uuid4().hex


def slugify(name: str) -> str:
    text = re.sub(r"[^\w\u4e00-\u9fff]+", "-", (name or "agent").strip(), flags=re.UNICODE)
    text = re.sub(r"-{2,}", "-", text).strip("-")
    return (text or "agent")[:48]


def workspace_relpath(agent: Agent) -> str:
    stored = (getattr(agent, "workspace", "") or "").strip()
    if stored:
        return stored.replace("\\", "/").strip("/")
    return f"workspaces/{agent.id}-{slugify(agent.name)}"


def workspace_dir(agent: Agent) -> Path:
    rel = workspace_relpath(agent)
    if rel.startswith("workspaces/"):
        return workspaces_root() / rel.split("/", 1)[1]
    return workspaces_root() / Path(rel).name


def ensure_workspace(agent: Agent) -> Path:
    path = workspace_dir(agent)
    (path / "sessions").mkdir(parents=True, exist_ok=True)
    (path / "traces").mkdir(parents=True, exist_ok=True)
    (path / "files").mkdir(parents=True, exist_ok=True)
    agent.workspace = workspace_relpath(agent)
    write_manifest(agent)
    return path


def ensure_workspaces(agents: list[Agent]) -> None:
    for agent in agents:
        ensure_workspace(agent)


def remove_workspace(agent: Agent) -> None:
    root = workspaces_root().resolve()
    path = workspace_dir(agent).resolve()
    if path.exists() and path.is_dir() and root in path.parents:
        shutil.rmtree(path, ignore_errors=True)


def write_manifest(agent: Agent) -> None:
    path = workspace_dir(agent)
    path.mkdir(parents=True, exist_ok=True)
    payload = {
        "id": agent.id,
        "name": agent.name,
        "description": agent.description or "",
        "model_name": agent.model_name or "",
        "version": agent.version or "",
        "system_prompt": agent.system_prompt or "",
        "skill_ids": list(agent.skill_ids or []),
        "mcp_ids": list(agent.mcp_ids or []),
        "updated_at": _iso(datetime.now(timezone.utc)),
    }
    _write_json(path / "agent.json", payload)


def session_path(agent: Agent, session_id: str) -> Path:
    safe = re.sub(r"[^A-Za-z0-9._-]", "_", session_id or "")
    return workspace_dir(agent) / "sessions" / f"{safe}.json"


def load_session(agent: Agent, session_id: str) -> Optional[dict[str, Any]]:
    path = session_path(agent, session_id)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def list_sessions(agent: Agent) -> list[dict[str, Any]]:
    folder = workspace_dir(agent) / "sessions"
    if not folder.exists():
        return []
    rows = []
    for path in folder.glob("*.json"):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        rows.append(_session_summary(data))
    rows.sort(key=lambda item: item.get("updated_at") or "", reverse=True)
    return rows


def persist_run(
    *,
    agent: Agent,
    session_id: str,
    title: str,
    message: str,
    reply: str,
    mode: str,
    model_name: str,
    trace_id: str,
    spans: list[dict[str, Any]],
    usage: dict[str, Any],
    latency_ms: int,
) -> dict[str, Any]:
    ensure_workspace(agent)
    now = _iso(datetime.now(timezone.utc))
    data = load_session(agent, session_id) or {
        "session_id": session_id,
        "agent_id": agent.id,
        "agent_name": agent.name,
        "title": title[:80],
        "channel": "Playground",
        "messages": [],
        "traces": [],
        "created_at": now,
    }
    data["agent_id"] = agent.id
    data["agent_name"] = agent.name
    data["title"] = data.get("title") or title[:80]
    data["updated_at"] = now
    data.setdefault("messages", []).extend(
        [
            {"role": "user", "content": message, "agent_name": "我", "created_at": now},
            {"role": "assistant", "content": reply, "agent_name": agent.name, "created_at": now, "error": mode == "error"},
        ]
    )
    trace = {
        "trace_id": trace_id,
        "session_id": session_id,
        "agent_id": agent.id,
        "agent_name": agent.name,
        "model": model_name,
        "status": "error" if mode == "error" else "ok",
        "duration_ms": latency_ms,
        "spans": spans,
        "usage": usage or {},
        "created_at": now,
    }
    data.setdefault("traces", []).append(trace)
    _write_json(session_path(agent, session_id), data)
    _write_json(workspace_dir(agent) / "traces" / f"{trace_id}.json", trace)
    return data


def workspace_status(agent: Agent) -> dict[str, Any]:
    ensure_workspace(agent)
    sessions = list_sessions(agent)
    traces = list((workspace_dir(agent) / "traces").glob("*.json")) if (workspace_dir(agent) / "traces").exists() else []
    return {
        "path": agent.workspace or workspace_relpath(agent),
        "session_count": len(sessions),
        "trace_count": len(traces),
        "sessions": sessions,
        "latest_session_id": sessions[0]["session_id"] if sessions else "",
    }


def _session_summary(data: dict[str, Any]) -> dict[str, Any]:
    messages = data.get("messages") or []
    traces = data.get("traces") or []
    return {
        "session_id": data.get("session_id") or "",
        "title": data.get("title") or "",
        "message_count": len(messages),
        "trace_count": len(traces),
        "updated_at": data.get("updated_at") or data.get("created_at") or "",
        "messages": messages,
        "traces": traces,
    }


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
