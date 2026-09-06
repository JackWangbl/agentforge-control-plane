"""Local MCP tools and Skill documents that the playground can actually run."""
from __future__ import annotations

import ast
import json
import operator
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Agent, McpServer, Skill
from app.services.browser_runtime import browser_tool_specs, execute_browser_tool
from app.services.mcp_stream import call_streamable_http_tool, is_http_stream_transport
from app.services.sandbox_runtime import run_sandbox_tool, sandbox_tool_specs, selected_sandbox

ROOT = Path(__file__).resolve().parents[2]
SKILLS_DIR = ROOT / "skills"

_OPS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
    ast.UAdd: operator.pos,
    ast.USub: operator.neg,
}

BUILTIN_MCP_NAME = "本地工具"
BUILTIN_MCP_ENDPOINT = "builtin:local-tools"
KEEP_SKILL_NAMES = ("客服回复规范", "会议纪要")
SKILL_FILES = {
    "客服回复规范": SKILLS_DIR / "customer-reply" / "SKILL.md",
    "会议纪要": SKILLS_DIR / "meeting-notes" / "SKILL.md",
}
BUILTIN_BROWSER_NAME = "浏览器工具"
BUILTIN_BROWSER_ENDPOINT = "builtin:browser"
JUNK_NAME = re.compile(
    r"^(编辑|删除|UI删除)|^(MCP|Skill|密钥模型|画布)-[0-9a-fA-F]{6,}|^新流程 "
)
DUMMY_MCP_NAMES = {"高德地图服务", "企业数据库", "网页检索"}
DUMMY_SKILL_NAMES = {"数据洞察", "工单分流", "报告生成"}


def builtin_tool_specs() -> list[dict[str, Any]]:
    return [
        {
            "name": "get_current_time",
            "description": "返回当前日期和时间（Asia/Shanghai）。用户问现在几点、今天日期时必须调用。",
            "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
        },
        {
            "name": "calculate",
            "description": "计算四则运算表达式，支持 + - * / ** 和括号。",
            "parameters": {
                "type": "object",
                "properties": {"expression": {"type": "string", "description": "例如 (19.9*3)+8"}},
                "required": ["expression"],
            },
        },
        {
            "name": "search_knowledge",
            "description": "在已启用的 Skill 说明和平台简介中检索相关内容。",
            "parameters": {
                "type": "object",
                "properties": {"query": {"type": "string", "description": "检索关键词"}},
                "required": ["query"],
            },
        },
        {
            "name": "list_agents",
            "description": "列出控制面里已登记的 Agent 名称、模型与职责。",
            "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
        },
    ]


def openai_tools() -> list[dict[str, Any]]:
    return [_as_openai_tool(spec) for spec in builtin_tool_specs()]


def _as_openai_tool(spec: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": spec["name"],
            "description": spec["description"],
            "parameters": spec.get("parameters") or {"type": "object", "properties": {}},
        },
    }


def tool_specs_for_mcp(row: McpServer) -> list[dict[str, Any]]:
    if is_builtin_mcp(row) and "browser" in (row.endpoint or "").lower():
        return browser_tool_specs()
    if is_builtin_mcp(row):
        return builtin_tool_specs()
    stored = (row.config or {}).get("tools")
    if not isinstance(stored, list):
        return []
    specs = []
    for item in stored:
        if not isinstance(item, dict) or not item.get("name"):
            continue
        specs.append({
            "name": item["name"],
            "description": item.get("description") or "",
            "parameters": item.get("parameters") or {"type": "object", "properties": {}},
        })
    return specs


def openai_tools_for_mcps(rows: list[McpServer]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    tools: list[dict[str, Any]] = []
    for row in rows:
        for spec in tool_specs_for_mcp(row):
            name = spec["name"]
            if name in seen:
                continue
            seen.add(name)
            tools.append(_as_openai_tool(spec))
    return tools


def is_builtin_mcp(row: McpServer) -> bool:
    endpoint = (row.endpoint or "").lower()
    config = row.config or {}
    return config.get("kind") == "builtin" or endpoint.startswith("builtin:") or "app.mcp_server" in endpoint


def list_mcp_tools(row: McpServer) -> list[dict[str, Any]]:
    return [{"name": spec["name"], "description": spec["description"]} for spec in tool_specs_for_mcp(row)]


def read_skill_file(name: str) -> str:
    path = SKILL_FILES.get(name)
    if not path or not path.exists():
        return ""
    text = path.read_text(encoding="utf-8")
    if text.startswith("---"):
        parts = text.split("---", 2)
        if len(parts) >= 3:
            return parts[2].strip()
    return text.strip()


def skill_instruction(row: Skill) -> str:
    text = (row.instruction or "").strip()
    return text or read_skill_file(row.name)


def skill_slug(name: str) -> str:
    slug = re.sub(r"[^\w\u4e00-\u9fff-]+", "-", (name or "").strip(), flags=re.UNICODE)
    slug = slug.strip("-") or "skill"
    return slug[:80]


def persist_skill_markdown(row: Skill) -> None:
    body = (row.instruction or "").strip()
    if not body:
        return
    path = SKILL_FILES.get(row.name) or (SKILLS_DIR / skill_slug(row.name) / "SKILL.md")
    path.parent.mkdir(parents=True, exist_ok=True)
    description = (row.description or "").replace("\n", " ")
    content = (
        f"---\nname: {row.name}\nversion: {row.version or '1.0.0'}\n"
        f"description: {description}\n---\n\n{body}\n"
    )
    path.write_text(content, encoding="utf-8")
    try:
        row.source = str(path.relative_to(ROOT))
    except ValueError:
        row.source = str(path)


def normalize_id_list(value: Any) -> list[int]:
    if not value:
        return []
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            return []
    ids = []
    for item in value:
        try:
            ids.append(int(item))
        except (TypeError, ValueError):
            continue
    return ids


def selected_skills(agent: Agent, db: Session) -> list[Skill]:
    ids = normalize_id_list(getattr(agent, "skill_ids", None))
    if not ids:
        return []
    rows = db.scalars(select(Skill).where(Skill.id.in_(ids), Skill.enabled.is_(True)).order_by(Skill.id)).all()
    return list(rows)


def selected_mcps(agent: Agent, db: Session) -> list[McpServer]:
    ids = normalize_id_list(getattr(agent, "mcp_ids", None))
    if not ids:
        return []
    rows = db.scalars(select(McpServer).where(McpServer.id.in_(ids), McpServer.enabled.is_(True)).order_by(McpServer.id)).all()
    return list(rows)


def skill_prompt_block(db: Session, agent: Optional[Agent] = None) -> str:
    rows = selected_skills(agent, db) if agent is not None else db.scalars(select(Skill).where(Skill.enabled.is_(True)).order_by(Skill.id)).all()
    chunks = []
    for row in rows:
        body = skill_instruction(row)
        if not body:
            continue
        chunks.append(f"### Skill：{row.name}\n{body}")
    if not chunks:
        return ""
    return "你必须遵循以下已绑定 Skill：\n\n" + "\n\n".join(chunks)


def mcp_tool_hint(db: Session, agent: Optional[Agent] = None) -> str:
    rows = selected_mcps(agent, db) if agent is not None else [
        row for row in db.scalars(select(McpServer).where(McpServer.enabled.is_(True))).all() if list_mcp_tools(row)
    ]
    names = []
    for row in rows:
        for tool in list_mcp_tools(row):
            names.append(tool["name"])
    if selected_sandbox(agent, db) if agent is not None else None:
        names.extend(spec["name"] for spec in sandbox_tool_specs())
    if not names:
        return ""
    return "你可以调用这些 MCP 工具：" + "、".join(names) + "。需要实时时间、计算、检索技能说明、查看 Agent 列表或在沙箱里跑代码时必须先调用工具，不要猜测。"


def agent_allows_tool(agent: Agent, db: Session, tool_name: str) -> bool:
    if tool_name.startswith("sandbox_") and selected_sandbox(agent, db):
        return True
    for row in selected_mcps(agent, db):
        if any(tool.get("name") == tool_name for tool in list_mcp_tools(row)):
            return True
    return False


def build_system_prompt(agent: Agent, db: Session) -> str:
    base = agent.system_prompt.strip() if agent.system_prompt else f"你是{agent.name}。{agent.description or '你是一名专业的企业助手。'}"
    extras = [skill_prompt_block(db, agent), mcp_tool_hint(db, agent)]
    extra = "\n\n".join(part for part in extras if part)
    return f"{base}\n\n{extra}".strip() if extra else base


def execute_tool(name: str, arguments: dict[str, Any], db: Optional[Session] = None, agent: Optional[Agent] = None) -> str:
    if name == "get_current_time":
        return _current_time()
    if name == "calculate":
        return _calculate(str(arguments.get("expression") or ""))
    if name == "search_knowledge":
        return _search_knowledge(str(arguments.get("query") or ""), db)
    if name == "list_agents":
        return _list_agents(db)
    if name.startswith("browser_"):
        try:
            return execute_browser_tool(name, arguments)
        except Exception as exc:
            return json.dumps({"error": str(exc)}, ensure_ascii=False)
    if name.startswith("sandbox_") and db is not None:
        box = selected_sandbox(agent, db) if agent is not None else None
        if box is None and agent is None:
            from sqlalchemy import select as sql_select
            from app.models import SandboxPolicy
            box = db.scalar(sql_select(SandboxPolicy).where(SandboxPolicy.enabled.is_(True)).order_by(SandboxPolicy.id))
        if box is None:
            return json.dumps({"error": "当前 Agent 未绑定可用沙箱"}, ensure_ascii=False)
        return run_sandbox_tool(box, name, arguments)
    if db is not None:
        remote = _remote_mcp_for_tool(db, name)
        if remote is not None:
            try:
                return call_streamable_http_tool(remote, name, arguments)
            except Exception as exc:
                return json.dumps({"error": str(exc)}, ensure_ascii=False)
    return json.dumps({"error": f"未知工具 {name}"}, ensure_ascii=False)


def _remote_mcp_for_tool(db: Session, name: str) -> Optional[McpServer]:
    rows = db.scalars(select(McpServer).where(McpServer.enabled.is_(True)).order_by(McpServer.id)).all()
    for row in rows:
        if is_builtin_mcp(row) or not is_http_stream_transport(row.transport):
            continue
        if any(tool.get("name") == name for tool in tool_specs_for_mcp(row)):
            return row
    return None


def parse_tool_arguments(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    if not raw:
        return {}
    try:
        data = json.loads(raw)
        return data if isinstance(data, dict) else {}
    except json.JSONDecodeError:
        return {}


def _current_time() -> str:
    try:
        from zoneinfo import ZoneInfo
        now = datetime.now(ZoneInfo("Asia/Shanghai"))
    except Exception:
        now = datetime.now(timezone.utc)
    return now.strftime("当前时间：%Y-%m-%d %H:%M:%S %Z")


def _eval_num(node: ast.AST) -> float:
    if isinstance(node, ast.Expression):
        return _eval_num(node.body)
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        return float(node.value)
    if isinstance(node, ast.UnaryOp) and type(node.op) in _OPS:
        return _OPS[type(node.op)](_eval_num(node.operand))
    if isinstance(node, ast.BinOp) and type(node.op) in _OPS:
        left = _eval_num(node.left)
        right = _eval_num(node.right)
        if isinstance(node.op, ast.Pow) and abs(right) > 12:
            raise ValueError("指数过大")
        if isinstance(node.op, (ast.Div, ast.FloorDiv, ast.Mod)) and right == 0:
            raise ValueError("除数不能为 0")
        return _OPS[type(node.op)](left, right)
    raise ValueError("只支持数字和四则运算")


def _calculate(expression: str) -> str:
    expr = (expression or "").strip()
    if not expr or len(expr) > 80:
        return "请提供不超过 80 个字符的算式，例如 19.9*3+8"
    try:
        tree = ast.parse(expr, mode="eval")
        value = _eval_num(tree)
        if abs(value - round(value)) < 1e-9:
            value = int(round(value))
        return f"{expr} = {value}"
    except Exception as exc:
        return f"无法计算「{expr}」：{exc}"


def _search_knowledge(query: str, db: Optional[Session]) -> str:
    q = (query or "").strip()
    if not q:
        return "请提供检索关键词。"
    hits = []
    if db is not None:
        for row in db.scalars(select(Skill).where(Skill.enabled.is_(True))).all():
            blob = f"{row.name}\n{row.description}\n{skill_instruction(row)}"
            if q.lower() in blob.lower():
                hits.append(f"- {row.name}：{(row.description or skill_instruction(row)[:80])}")
    if not hits:
        hits.append("未在已启用 Skill 中找到直接匹配。可用技能：" + "、".join(KEEP_SKILL_NAMES))
    return "检索结果：\n" + "\n".join(hits[:6])


def _list_agents(db: Optional[Session]) -> str:
    if db is None:
        return "当前没有可用的 Agent 列表。"
    rows = db.scalars(select(Agent).order_by(Agent.id)).all()
    if not rows:
        return "控制面里还没有 Agent。"
    lines = [f"- {row.name}（{row.model_name}）：{row.description or '未填写职责'}" for row in rows]
    return "已登记 Agent：\n" + "\n".join(lines)


def purge_junk_and_seed_tools(db: Session) -> None:
    from app.models import ModelConfig, Role, SandboxPolicy, Workflow

    for model in (McpServer, Skill, Agent, ModelConfig, Role, SandboxPolicy, Workflow):
        for row in list(db.scalars(select(model)).all()):
            name = getattr(row, "name", "")
            dummy = (
                (model is McpServer and name in DUMMY_MCP_NAMES)
                or (model is Skill and name in DUMMY_SKILL_NAMES)
            )
            if dummy or JUNK_NAME.search(name or ""):
                db.delete(row)

    builtin = db.scalar(select(McpServer).where(McpServer.name == BUILTIN_MCP_NAME))
    tools = [{"name": spec["name"], "description": spec["description"]} for spec in builtin_tool_specs()]
    payload = {
        "transport": "stdio",
        "endpoint": BUILTIN_MCP_ENDPOINT,
        "enabled": True,
        "tools_count": len(tools),
        "config": {"kind": "builtin", "tools": tools},
    }
    if builtin:
        for key, value in payload.items():
            setattr(builtin, key, value)
    else:
        db.add(McpServer(name=BUILTIN_MCP_NAME, **payload))

    browser_tools = [{"name": spec["name"], "description": spec["description"]} for spec in browser_tool_specs()]
    browser = db.scalar(select(McpServer).where(McpServer.name == BUILTIN_BROWSER_NAME))
    browser_payload = {
        "transport": "stdio",
        "endpoint": BUILTIN_BROWSER_ENDPOINT,
        "enabled": True,
        "tools_count": len(browser_tools),
        "config": {"kind": "builtin", "tools": browser_tools},
    }
    if browser:
        for key, value in browser_payload.items():
            setattr(browser, key, value)
    else:
        db.add(McpServer(name=BUILTIN_BROWSER_NAME, **browser_payload))

    for name, path in SKILL_FILES.items():
        body = read_skill_file(name)
        description = "按企业客服口径回复用户" if name == "客服回复规范" else "把讨论整理成结论、待办和风险"
        source = str(path.relative_to(ROOT))
        row = db.scalar(select(Skill).where(Skill.name == name))
        if row:
            row.description = description
            row.source = source
            row.instruction = body
            row.enabled = True
            row.version = "1.0.0"
        else:
            db.add(Skill(name=name, description=description, source=source, version="1.0.0", instruction=body, enabled=True))

    _restore_unwanted_1688_skill_edits(db)
    db.commit()


def _restore_unwanted_1688_skill_edits(db: Session) -> None:
    marker = "不要输出「执行阻断说明」"
    path = SKILLS_DIR / "1688" / "SKILL.md"
    if not path.exists():
        return
    text = path.read_text(encoding="utf-8")
    body = text
    if text.startswith("---"):
        parts = text.split("---", 2)
        if len(parts) >= 3:
            body = parts[2].strip()
    for row in db.scalars(select(Skill)).all():
        if marker in (row.instruction or ""):
            row.instruction = body
