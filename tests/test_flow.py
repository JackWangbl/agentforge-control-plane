import json
from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.database import SessionLocal
from app.main import app
from app.models import Agent
from app.services.flow_runtime import (
    agent_flows,
    find_agent_flow,
    flow_openai_tools,
    flow_prompt_hint,
    flow_step_spans,
    run_tool_flow,
)
from app.services.tool_runtime import agent_allows_tool, agent_openai_tools, execute_tool


def _recorder(outputs):
    calls = []

    def execute(tool, args):
        calls.append((tool, args))
        return outputs.get(tool, "{}")

    return execute, calls


def test_flow_feeds_each_step_output_into_the_next():
    flow = {
        "name": "refund",
        "steps": [
            {"id": "order", "tool": "get_order", "arguments": {"id": "{{input.order_id}}"}},
            {"id": "rule", "tool": "get_rule", "arguments": {"category": "{{steps.order.output.category}}"}},
            {"tool": "calculate", "arguments": {"expression": "{{steps.order.output.amount}}*0.9"}},
        ],
    }
    execute, calls = _recorder({
        "get_order": json.dumps({"amount": 200, "category": "家电"}, ensure_ascii=False),
        "get_rule": json.dumps({"window_days": 7}, ensure_ascii=False),
        "calculate": "200*0.9 = 180",
    })
    report = json.loads(run_tool_flow(flow, {"order_id": "A1"}, execute=execute, allows=lambda tool: True))

    assert [tool for tool, _ in calls] == ["get_order", "get_rule", "calculate"]
    assert calls[0][1] == {"id": "A1"}
    assert calls[1][1] == {"category": "家电"}
    assert calls[2][1] == {"expression": "200*0.9"}
    assert report["status"] == "ok"
    assert [step["status"] for step in report["steps"]] == ["ok", "ok", "ok"]
    assert report["result"] == "200*0.9 = 180"


def test_flow_keeps_argument_types_and_interpolates_inside_strings():
    flow = {
        "name": "typed",
        "steps": [
            {"tool": "probe", "arguments": {"count": "{{input.count}}", "label": "共 {{input.count}} 单"}},
        ],
    }
    execute, calls = _recorder({"probe": "{}"})
    run_tool_flow(flow, {"count": 3}, execute=execute, allows=lambda tool: True)

    assert calls[0][1] == {"count": 3, "label": "共 3 单"}


def test_flow_aborts_and_skips_remaining_steps():
    flow = {
        "name": "guarded",
        "steps": [
            {"tool": "step_one"},
            {"tool": "step_two"},
            {"tool": "step_three"},
        ],
    }
    execute, calls = _recorder({
        "step_one": json.dumps({"ok": True}),
        "step_two": json.dumps({"error": "下游超时"}, ensure_ascii=False),
        "step_three": json.dumps({"ok": True}),
    })
    report = json.loads(run_tool_flow(flow, {}, execute=execute, allows=lambda tool: True))

    assert [tool for tool, _ in calls] == ["step_one", "step_two"]
    assert report["status"] == "aborted"
    assert [step["status"] for step in report["steps"]] == ["ok", "error", "skip"]
    assert report["steps"][1]["error"] == "下游超时"


def test_flow_continue_policy_runs_the_rest_and_reports_partial():
    flow = {
        "name": "lenient",
        "on_error": "continue",
        "steps": [
            {"tool": "step_one"},
            {"tool": "step_two"},
        ],
    }
    execute, calls = _recorder({
        "step_one": json.dumps({"error": "查不到"}, ensure_ascii=False),
        "step_two": json.dumps({"ok": True}),
    })
    report = json.loads(run_tool_flow(flow, {}, execute=execute, allows=lambda tool: True))

    assert [tool for tool, _ in calls] == ["step_one", "step_two"]
    assert report["status"] == "partial"
    assert [step["status"] for step in report["steps"]] == ["error", "ok"]


def test_flow_rejects_unresolved_reference_without_calling_the_tool():
    flow = {"name": "broken", "steps": [{"tool": "probe", "arguments": {"x": "{{steps.9.output.y}}"}}]}
    execute, calls = _recorder({"probe": "{}"})
    report = json.loads(run_tool_flow(flow, {}, execute=execute, allows=lambda tool: True))

    assert calls == []
    assert report["status"] == "aborted"
    assert "未解析的引用" in report["steps"][0]["error"]


def test_flow_rejects_unbound_and_nested_tools():
    flow = {
        "name": "unsafe",
        "on_error": "continue",
        "steps": [{"tool": "secret_tool"}, {"tool": "flow_other"}],
    }
    execute, calls = _recorder({})
    report = json.loads(run_tool_flow(flow, {}, execute=execute, allows=lambda tool: False))

    assert calls == []
    assert report["steps"][0]["error"] == "Agent 未绑定工具 secret_tool"
    assert report["steps"][1]["error"] == "链路步骤不能再调用另一条链路"


def test_flow_step_spans_expand_one_span_per_step():
    payload = json.dumps({
        "flow": "refund",
        "status": "aborted",
        "steps": [
            {"index": 0, "id": "order", "tool": "get_order", "status": "ok", "duration_ms": 5, "output": {"amount": 1}},
            {"index": 1, "id": "1", "tool": "get_rule", "status": "error", "error": "下游超时"},
            {"index": 2, "id": "2", "tool": "calculate", "status": "skip"},
        ],
    }, ensure_ascii=False)
    spans = flow_step_spans("flow_refund", payload)

    assert [span["status"] for span in spans] == ["ok", "error", "skip"]
    assert spans[0]["name"] == "flow_refund.order"
    assert spans[1]["title"] == "链路第 2 步 · get_rule"
    assert spans[1]["detail"] == "下游超时"
    assert all(span["kind"] == "tool" for span in spans)


def test_flow_definition_normalizes_and_exposes_one_tool_each():
    agent = Agent(name="probe", model_name="Qwen-Max", tool_flows=[
        {"name": "good", "description": "先查再算", "steps": [{"tool": "get_current_time"}, {"tool": "calculate"}]},
        {"name": "", "steps": [{"tool": "x"}]},
        "not-a-dict",
        {"name": "empty", "steps": []},
    ])

    assert [flow["name"] for flow in agent_flows(agent)] == ["good"]
    assert find_agent_flow(agent, "flow_good") is not None
    assert find_agent_flow(agent, "flow_missing") is None
    tools = flow_openai_tools(agent)
    assert [tool["function"]["name"] for tool in tools] == ["flow_good"]
    assert "get_current_time → calculate" in tools[0]["function"]["description"]
    assert "flow_good" in flow_prompt_hint(agent)


def test_agent_flow_runs_bound_builtin_tools_end_to_end():
    suffix = uuid4().hex[:6]
    with TestClient(app) as client:
        builtin = next(row for row in client.get("/api/mcp").json() if row["name"] == "本地工具")
        created = client.post("/api/agents", json={
            "name": f"链路助手{suffix}",
            "model_name": "Qwen-Max",
            "mcp_ids": [builtin["id"]],
            "tool_flows": [{
                "name": "quote",
                "description": "先算折扣再检索口径",
                "parameters": {
                    "type": "object",
                    "properties": {"amount": {"type": "number"}},
                    "required": ["amount"],
                },
                "steps": [
                    {"id": "discount", "tool": "calculate", "arguments": {"expression": "{{input.amount}}*0.9"}},
                    {"tool": "search_knowledge", "arguments": {"query": "客服"}},
                ],
            }],
        })
        assert created.status_code == 201, created.text
        agent_id = created.json()["id"]
        assert created.json()["tool_flows"][0]["name"] == "quote"

        with SessionLocal() as db:
            row = db.scalar(select(Agent).where(Agent.id == agent_id))
            assert agent_allows_tool(row, db, "flow_quote") is True
            assert agent_allows_tool(row, db, "flow_missing") is False
            names = [(item.get("function") or {}).get("name") for item in agent_openai_tools(row, db)]
            assert "flow_quote" in names
            assert "calculate" in names
            report = json.loads(execute_tool("flow_quote", {"amount": 200}, db, row))

        assert report["status"] == "ok"
        assert report["steps"][0]["output"] == "200*0.9 = 180"
        assert report["steps"][1]["tool"] == "search_knowledge"
        assert "检索结果" in report["steps"][1]["output"]

        copied = client.post(f"/api/agents/{agent_id}/copy")
        assert copied.status_code == 201, copied.text
        assert copied.json()["tool_flows"][0]["name"] == "quote"

        client.delete(f"/api/agents/{copied.json()['id']}")
        client.delete(f"/api/agents/{agent_id}")


def test_agent_flow_definition_is_validated_on_save():
    suffix = uuid4().hex[:6]
    with TestClient(app) as client:
        builtin = next(row for row in client.get("/api/mcp").json() if row["name"] == "本地工具")
        base = {"name": f"校验助手{suffix}", "model_name": "Qwen-Max", "mcp_ids": [builtin["id"]]}

        unbound = client.post("/api/agents", json={
            **base,
            "tool_flows": [{"name": "bad", "steps": [{"tool": "get_order"}]}],
        })
        assert unbound.status_code == 422
        assert "get_order" in unbound.text

        nested = client.post("/api/agents", json={
            **base,
            "tool_flows": [{"name": "bad", "steps": [{"tool": "flow_other"}]}],
        })
        assert nested.status_code == 422

        duplicate = client.post("/api/agents", json={
            **base,
            "tool_flows": [
                {"name": "same", "steps": [{"tool": "calculate"}]},
                {"name": "same", "steps": [{"tool": "calculate"}]},
            ],
        })
        assert duplicate.status_code == 422

        no_steps = client.post("/api/agents", json={**base, "tool_flows": [{"name": "empty", "steps": []}]})
        assert no_steps.status_code == 422

        created = client.post("/api/agents", json={**base, "tool_flows": [
            {"name": "ok_flow", "steps": [{"tool": "calculate"}]},
        ]})
        assert created.status_code == 201, created.text
        agent_id = created.json()["id"]

        broken_update = client.put(f"/api/agents/{agent_id}", json={
            "tool_flows": [{"name": "ok_flow", "steps": [{"tool": "get_order"}]}],
        })
        assert broken_update.status_code == 422

        cleared = client.put(f"/api/agents/{agent_id}", json={"tool_flows": []})
        assert cleared.status_code == 200, cleared.text
        assert cleared.json()["tool_flows"] == []

        client.delete(f"/api/agents/{agent_id}")
