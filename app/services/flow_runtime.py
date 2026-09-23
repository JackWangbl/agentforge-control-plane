"""Deterministic MCP tool chains bound to an Agent.

A flow is an ordered list of tool steps stored on `Agent.tool_flows`. The model
calls the whole chain through a single `flow_<name>` tool; this module runs the
steps in order, feeds each step's output into the next step's arguments, and
reports every step back so the debug trace shows what actually ran.
"""
from __future__ import annotations

import json
import re
import time
from typing import Any, Callable, Optional

FLOW_TOOL_PREFIX = "flow_"
MAX_FLOW_STEPS = 10
MAX_FLOWS_PER_AGENT = 20
STEP_TEXT_LIMIT = 2000
SPAN_DETAIL_LIMIT = 240

_REFERENCE = re.compile(r"\{\{\s*([A-Za-z0-9_.\-]+)\s*\}\}")
_ONLY_REFERENCE = re.compile(r"^\{\{\s*([A-Za-z0-9_.\-]+)\s*\}\}$")


class FlowReferenceError(ValueError):
    """A step argument points at a value the previous steps never produced."""


def flow_tool_name(name: str) -> str:
    return f"{FLOW_TOOL_PREFIX}{name}"


def is_flow_tool(name: str) -> bool:
    return (name or "").startswith(FLOW_TOOL_PREFIX)


def agent_flows(agent: Any) -> list[dict[str, Any]]:
    raw = getattr(agent, "tool_flows", None) or []
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError:
            return []
    if not isinstance(raw, list):
        return []
    flows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in raw:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        steps = [step for step in (item.get("steps") or []) if isinstance(step, dict)]
        if not name or name in seen or not steps:
            continue
        seen.add(name)
        flows.append({**item, "name": name, "steps": steps[:MAX_FLOW_STEPS]})
    return flows[:MAX_FLOWS_PER_AGENT]


def find_agent_flow(agent: Any, tool_name: str) -> Optional[dict[str, Any]]:
    if not is_flow_tool(tool_name):
        return None
    wanted = tool_name[len(FLOW_TOOL_PREFIX):]
    return next((flow for flow in agent_flows(agent) if flow["name"] == wanted), None)


def flow_step_tools(agent: Any) -> list[str]:
    names = []
    for flow in agent_flows(agent):
        for step in flow["steps"]:
            tool = str(step.get("tool") or "").strip()
            if tool and tool not in names:
                names.append(tool)
    return names


def flow_openai_tools(agent: Any) -> list[dict[str, Any]]:
    tools = []
    for flow in agent_flows(agent):
        chain = " → ".join(str(step.get("tool") or "?") for step in flow["steps"])
        description = str(flow.get("description") or "").strip()
        summary = f"按固定顺序执行 {chain}，一次调用完成整条链路，不要再单独调用其中的工具。"
        tools.append({
            "type": "function",
            "function": {
                "name": flow_tool_name(flow["name"]),
                "description": f"{description} {summary}".strip(),
                "parameters": flow.get("parameters") or {"type": "object", "properties": {}},
            },
        })
    return tools


def flow_prompt_hint(agent: Any) -> str:
    flows = agent_flows(agent)
    if not flows:
        return ""
    lines = []
    for flow in flows:
        chain = " → ".join(str(step.get("tool") or "?") for step in flow["steps"])
        purpose = str(flow.get("description") or "").strip()
        lines.append(f"- {flow_tool_name(flow['name'])}：{purpose + '，' if purpose else ''}会依次执行 {chain}")
    return (
        "以下固定链路已经编排好，需要时调用链路本身，平台会保证顺序和参数传递：\n"
        + "\n".join(lines)
    )


def run_tool_flow(
    flow: dict[str, Any],
    arguments: dict[str, Any],
    *,
    execute: Callable[[str, dict[str, Any]], str],
    allows: Callable[[str], bool],
) -> str:
    """Run every step in order and return a JSON report of the whole chain."""
    default_policy = "continue" if flow.get("on_error") == "continue" else "abort"
    scope: dict[str, Any] = {"input": dict(arguments or {}), "steps": {}}
    records: list[dict[str, Any]] = []
    result: Any = None
    stopped = False
    degraded = False

    for index, step in enumerate(flow.get("steps") or []):
        tool = str(step.get("tool") or "").strip()
        ref = str(step.get("id") or "").strip() or str(index)
        record: dict[str, Any] = {"index": index, "id": ref, "tool": tool}
        records.append(record)
        if stopped:
            record.update(status="skip", error="上一步失败且链路策略为 abort，本步未执行")
            continue
        policy = "continue" if step.get("on_error") == "continue" else "abort" if step.get("on_error") else default_policy

        failure = ""
        resolved: dict[str, Any] = {}
        if not tool:
            failure = "步骤没有指定工具"
        elif is_flow_tool(tool):
            failure = "链路步骤不能再调用另一条链路"
        elif not allows(tool):
            failure = f"Agent 未绑定工具 {tool}"
        else:
            try:
                resolved = _resolve(step.get("arguments") or {}, scope)
            except FlowReferenceError as exc:
                failure = str(exc)

        if failure:
            record.update(status="error", error=failure)
            if policy == "abort":
                stopped = True
            else:
                degraded = True
            continue

        record["arguments"] = _shrink(resolved)
        started = time.perf_counter()
        try:
            raw = execute(tool, resolved)
        except Exception as exc:
            raw = json.dumps({"error": str(exc)}, ensure_ascii=False)
        record["duration_ms"] = max(1, int((time.perf_counter() - started) * 1000))

        text = raw if isinstance(raw, str) else json.dumps(raw, ensure_ascii=False, default=str)
        output = _parse_output(text)
        scope["steps"][ref] = {"text": text, "output": output}
        scope["steps"].setdefault(str(index), scope["steps"][ref])

        error = _error_of(output)
        record["output"] = _shrink(output)
        if error:
            record.update(status="error", error=error)
            if policy == "abort":
                stopped = True
            else:
                degraded = True
            continue
        record["status"] = "ok"
        result = output

    status = "aborted" if stopped else "partial" if degraded else "ok"
    return json.dumps(
        {
            "flow": flow.get("name") or "",
            "status": status,
            "steps": records,
            "result": _shrink(result),
        },
        ensure_ascii=False,
        default=str,
    )


def flow_step_spans(tool_name: str, payload: str) -> list[dict[str, Any]]:
    """Turn a flow report into one debug span per step."""
    try:
        report = json.loads(payload)
    except (json.JSONDecodeError, TypeError):
        return []
    if not isinstance(report, dict) or not isinstance(report.get("steps"), list):
        return []
    spans = []
    for record in report["steps"]:
        if not isinstance(record, dict):
            continue
        status = str(record.get("status") or "ok")
        detail = record.get("error") or _as_text(record.get("output"))
        spans.append({
            "name": f"{tool_name}.{record.get('id')}",
            "title": f"链路第 {int(record.get('index') or 0) + 1} 步 · {record.get('tool') or '未知工具'}",
            "kind": "tool",
            "status": status if status in {"ok", "error", "skip"} else "ok",
            "duration_ms": int(record.get("duration_ms") or 0),
            "detail": _as_text(detail)[:SPAN_DETAIL_LIMIT],
        })
    return spans


def _resolve(value: Any, scope: dict[str, Any]) -> Any:
    if isinstance(value, dict):
        return {key: _resolve(item, scope) for key, item in value.items()}
    if isinstance(value, list):
        return [_resolve(item, scope) for item in value]
    if not isinstance(value, str):
        return value
    exact = _ONLY_REFERENCE.match(value.strip())
    if exact:
        return _lookup(exact.group(1), scope)
    return _REFERENCE.sub(lambda match: _as_text(_lookup(match.group(1), scope)), value)


def _lookup(path: str, scope: dict[str, Any]) -> Any:
    node: Any = scope
    for part in path.split("."):
        if isinstance(node, dict) and part in node:
            node = node[part]
            continue
        if isinstance(node, list):
            try:
                node = node[int(part)]
                continue
            except (ValueError, IndexError):
                pass
        raise FlowReferenceError(f"未解析的引用 {{{{{path}}}}}")
    return node


def _parse_output(text: str) -> Any:
    try:
        parsed = json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return text
    return parsed if isinstance(parsed, (dict, list)) else text


def _error_of(output: Any) -> str:
    if isinstance(output, dict) and output.get("error"):
        return str(output["error"])
    return ""


def _as_text(value: Any) -> str:
    if isinstance(value, str):
        return value
    if value is None:
        return ""
    return json.dumps(value, ensure_ascii=False, default=str)


def _shrink(value: Any) -> Any:
    if isinstance(value, str):
        return value if len(value) <= STEP_TEXT_LIMIT else value[:STEP_TEXT_LIMIT] + "…（已截断）"
    if isinstance(value, dict):
        return {key: _shrink(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_shrink(item) for item in value[:20]]
    return value
