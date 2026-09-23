"""Register a remote OpenCLI / Chrome CDP endpoint and query a live browser."""
from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import subprocess
from typing import Any, Optional
from urllib.parse import urlparse

import httpx

from types import SimpleNamespace

from app.models import McpServer

OPENCLI_KINDS = ("cdp", "daemon")
TEXT_LIMIT = 8000
_UNSAFE_COMMAND = re.compile(r"[|&;<>`$(){}!\n]|&&|\|\|")


def normalize_kind(value: Optional[str]) -> str:
    kind = (value or "cdp").strip().lower()
    return kind if kind in OPENCLI_KINDS else "cdp"


def kind_label(value: Optional[str]) -> str:
    return "OpenCLI Daemon" if normalize_kind(value) == "daemon" else "Chrome CDP"


def normalize_endpoint(value: str) -> str:
    raw = (value or "").strip().rstrip("/")
    if not raw:
        raise ValueError("请填写 OpenCLI / CDP 地址")
    parsed = urlparse(raw)
    if parsed.scheme not in {"http", "https"}:
        raise ValueError("地址必须是 http 或 https，例如 http://127.0.0.1:9222")
    if not parsed.netloc:
        raise ValueError("地址不完整")
    return raw


def parse_opencli_command(raw: str) -> dict[str, Any]:
    text = (raw or "").strip()
    if not text:
        raise ValueError("请填写 OpenCLI 指令")
    if _UNSAFE_COMMAND.search(text):
        raise ValueError("指令里不能包含管道、重定向或命令拼接")
    try:
        tokens = shlex.split(text)
    except ValueError as exc:
        raise ValueError(f"指令无法解析：{exc}") from exc
    if not tokens:
        raise ValueError("请填写 OpenCLI 指令")
    first = os.path.basename(tokens[0]).lower()
    if first in {"opencli", "open-cli"} or first.endswith("opencli"):
        tokens = tokens[1:]
    endpoint = ""
    kind = "cdp"
    target = ""
    session = "agentforge"
    host = ""
    port = ""
    args: list[str] = []
    index = 0
    while index < len(tokens):
        token = tokens[index]
        low = token.lower()
        if low in {"--cdp", "--endpoint", "--url"}:
            index += 1
            if index >= len(tokens):
                raise ValueError(f"{token} 后面需要地址")
            endpoint = tokens[index]
            index += 1
            continue
        if low in {"--host"}:
            index += 1
            if index >= len(tokens):
                raise ValueError("--host 后面需要主机")
            host = tokens[index]
            kind = "daemon"
            index += 1
            continue
        if low in {"--port"}:
            index += 1
            if index >= len(tokens):
                raise ValueError("--port 后面需要端口")
            port = tokens[index]
            kind = "daemon"
            index += 1
            continue
        if low in {"--daemon"}:
            kind = "daemon"
            index += 1
            continue
        if low in {"--session", "-s"}:
            index += 1
            if index < len(tokens):
                session = tokens[index]
            index += 1
            continue
        if low in {"--target", "--filter"}:
            index += 1
            if index < len(tokens):
                target = tokens[index]
            index += 1
            continue
        if token.startswith("http://") or token.startswith("https://"):
            endpoint = token.rstrip("/,")
            index += 1
            continue
        args.append(token)
        index += 1
    if not endpoint and host:
        endpoint = f"http://{host}:{port or '19825'}"
    if endpoint:
        endpoint = normalize_endpoint(endpoint)
        if ":19825" in endpoint:
            kind = "daemon"
    return {
        "command": text,
        "args": args,
        "endpoint": endpoint,
        "kind": kind,
        "target": target,
        "session": session or "agentforge",
    }


def run_opencli_command(row: Any, command: str = "") -> str:
    parsed = resolve_opencli_command(row, command)
    binary = shutil.which("opencli")
    if not binary:
        raise RuntimeError("本机未安装 opencli。请安装后再执行指令，或在指令里写 http://host:9222 走 Chrome CDP。")
    argv = [binary, *parsed["args"]] if parsed["args"] else [binary, "tab", "list"]
    env = os.environ.copy()
    if parsed.get("endpoint"):
        env["OPENCLI_CDP_ENDPOINT"] = parsed["endpoint"]
        if parsed.get("kind") == "daemon":
            env["OPENCLI_DAEMON"] = parsed["endpoint"]
    if parsed.get("target"):
        env["OPENCLI_CDP_TARGET"] = parsed["target"]
    try:
        completed = subprocess.run(argv, capture_output=True, text=True, timeout=45, env=env, check=False)
    except Exception as exc:
        raise RuntimeError(f"执行 OpenCLI 失败：{exc}") from exc
    output = (completed.stdout or completed.stderr or "").strip()
    if completed.returncode != 0 and not output:
        raise RuntimeError(f"opencli 退出码 {completed.returncode}")
    return output[:TEXT_LIMIT]


def resolve_opencli_command(row: Any, command: str = "") -> dict[str, Any]:
    extra = (command or "").strip()
    base = str(getattr(row, "command", None) or "").strip()
    if extra:
        parsed = parse_opencli_command(extra)
        if (not parsed.get("endpoint")) and base:
            inherited = parse_opencli_command(base)
            parsed["endpoint"] = inherited.get("endpoint") or parsed.get("endpoint")
            parsed["kind"] = inherited.get("kind") or parsed.get("kind")
            if parsed.get("session") == "agentforge" and inherited.get("session"):
                parsed["session"] = inherited["session"]
            if not parsed.get("target") and inherited.get("target"):
                parsed["target"] = inherited["target"]
        if not parsed.get("endpoint"):
            fallback = str(getattr(row, "endpoint", "") or "")
            if fallback.startswith("http"):
                parsed["endpoint"] = fallback
        return parsed
    if base:
        parsed = parse_opencli_command(base)
        if not parsed.get("endpoint"):
            fallback = str(getattr(row, "endpoint", "") or "")
            if fallback.startswith("http"):
                parsed["endpoint"] = fallback
        return parsed
    raise ValueError("没有可执行的 OpenCLI 指令")


def from_mcp(row: McpServer) -> Any:
    config = getattr(row, "config", None) or {}
    headers = config.get("headers") if isinstance(config.get("headers"), dict) else {}
    token = str(
        config.get("token")
        or config.get("api_key")
        or headers.get("Authorization")
        or headers.get("authorization")
        or ""
    )
    kind_raw = config.get("opencli_kind") or config.get("kind")
    if str(kind_raw or "").lower() not in OPENCLI_KINDS:
        kind_raw = "cdp"
    return SimpleNamespace(
        kind=normalize_kind(kind_raw),
        endpoint=getattr(row, "endpoint", "") or "",
        target=str(config.get("target") or ""),
        session=str(config.get("session") or "agentforge") or "agentforge",
        token=token,
        command=str(config.get("command") or ""),
    )


def apply_opencli_config(config: Optional[dict[str, Any]], *, endpoint: str = "") -> dict[str, Any]:
    data = dict(config or {})
    command = str(data.get("command") or "").strip()
    raw_endpoint = (endpoint or data.get("endpoint") or "").strip()
    if not command:
        if not raw_endpoint:
            raise ValueError("请填写 OpenCLI 指令，例如：opencli --cdp http://10.0.0.8:9222 tab list")
        parts = [f"opencli --cdp {raw_endpoint}"]
        if data.get("target"):
            parts.append(f"--target {data['target']}")
        parts.append("tab list")
        command = " ".join(parts)
    parsed = parse_opencli_command(command)
    resolved = parsed.get("endpoint") or raw_endpoint
    if resolved:
        resolved = normalize_endpoint(resolved)
    data["command"] = command
    data["kind"] = parsed["kind"] if not data.get("kind") else normalize_kind(data.get("kind"))
    data["opencli_kind"] = data["kind"]
    data["target"] = str(data.get("target") or parsed.get("target") or "")
    data["session"] = str(data.get("session") or parsed.get("session") or "agentforge") or "agentforge"
    data["endpoint"] = resolved
    data["tools"] = [
        {"name": spec["name"], "description": spec["description"], "parameters": spec.get("parameters")}
        for spec in opencli_tool_specs()
    ]
    return data


def _as_row(row: Any) -> Any:
    if hasattr(row, "config") and not hasattr(row, "token"):
        return from_mcp(row)
    return row


def _headers(row: Any) -> dict[str, str]:
    token = (row.token or "").strip()
    if not token:
        return {}
    if ":" in token and not token.lower().startswith("bearer "):
        key, value = token.split(":", 1)
        return {key.strip(): value.strip()}
    return {"Authorization": token if token.lower().startswith("bearer ") else f"Bearer {token}"}


def _get(row: Any, path: str, timeout: float = 6.0) -> httpx.Response:
    url = f"{normalize_endpoint(row.endpoint)}{path}"
    with httpx.Client(timeout=timeout, follow_redirects=True) as client:
        return client.get(url, headers=_headers(row))


def probe_opencli(row: Any) -> dict[str, Any]:
    row = _as_row(row)
    sample = ""
    cli_error: Optional[Exception] = None
    if getattr(row, "command", None):
        try:
            sample = run_opencli_command(row)
        except Exception as exc:
            cli_error = exc
    endpoint = str(getattr(row, "endpoint", "") or "")
    if endpoint.startswith("http"):
        try:
            probed = _probe_http(row)
            probed["sample"] = sample
            probed["command"] = getattr(row, "command", "")
            return probed
        except Exception:
            if sample:
                return {
                    "ready": True,
                    "kind": normalize_kind(getattr(row, "kind", None)),
                    "endpoint": endpoint,
                    "browser": "OpenCLI",
                    "tabs": 0,
                    "tab_list": [],
                    "sample": sample,
                    "command": getattr(row, "command", ""),
                }
            if cli_error:
                raise cli_error
            raise
    if sample:
        return {
            "ready": True,
            "kind": normalize_kind(getattr(row, "kind", None)),
            "endpoint": endpoint,
            "browser": "OpenCLI",
            "tabs": 0,
            "tab_list": [],
            "sample": sample,
            "command": getattr(row, "command", ""),
        }
    if cli_error:
        raise cli_error
    return _probe_http(row)


def _probe_http(row: Any) -> dict[str, Any]:
    row = _as_row(row)
    kind = normalize_kind(row.kind)
    endpoint = normalize_endpoint(row.endpoint)
    if kind == "daemon":
        ping = _get(row, "/ping")
        tabs: list[dict[str, Any]] = []
        try:
            tabs = list_tabs(row)
        except Exception:
            tabs = []
        extra = {"tabs": len(tabs), "tab_list": tabs[:30]}
        if ping.is_error:
            status = _get(row, "/status")
            if status.is_error:
                raise RuntimeError(f"Daemon 不可达：HTTP {ping.status_code}")
            body = _safe_json(status) or {"raw": status.text[:400]}
            return {"ready": True, "kind": kind, "endpoint": endpoint, "browser": "OpenCLI Daemon", "detail": body, **extra}
        return {"ready": True, "kind": kind, "endpoint": endpoint, "browser": "OpenCLI Daemon", "detail": _safe_text(ping), **extra}
    version = _get(row, "/json/version")
    if version.is_error:
        raise RuntimeError(f"CDP 不可达：HTTP {version.status_code}")
    data = _safe_json(version) or {}
    tabs = list_tabs(row)
    browser = str(data.get("Browser") or data.get("BrowserVersion") or "Chrome")
    return {
        "ready": True,
        "kind": kind,
        "endpoint": endpoint,
        "browser": browser,
        "tabs": len(tabs),
        "tab_list": tabs[:30],
        "detail": data,
    }


def list_tabs(row: Any, target: str = "") -> list[dict[str, Any]]:
    row = _as_row(row)
    kind = normalize_kind(row.kind)
    if kind == "daemon":
        tabs = _list_daemon_tabs(row)
    else:
        response = _get(row, "/json/list")
        if response.is_error:
            raise RuntimeError(f"读取标签失败：HTTP {response.status_code}")
        payload = _safe_json(response)
        if not isinstance(payload, list):
            raise RuntimeError("CDP /json/list 返回不是标签数组")
        tabs = [_normalize_tab(item) for item in payload if isinstance(item, dict)]
    needle = ((target or "").strip() or (row.target or "")).strip().lower()
    if not needle:
        return tabs
    return [tab for tab in tabs if needle in f"{tab['id']} {tab['url']} {tab['title']}".lower()]


def _list_daemon_tabs(row: Any) -> list[dict[str, Any]]:
    try:
        response = _get(row, "/tabs")
        if not response.is_error:
            payload = _safe_json(response)
            rows = payload if isinstance(payload, list) else (payload or {}).get("tabs") or []
            return [_normalize_tab(item) for item in rows if isinstance(item, dict)]
    except Exception:
        pass
    cli = _opencli_cli(row, ["tab", "list"])
    if cli is not None:
        return _tabs_from_cli(cli)
    raise RuntimeError("Daemon 未提供 /tabs。请改用 Chrome CDP 地址（远程调试端口 9222），或保证本机已安装 opencli。")


def query_browser(
    row: Any,
    *,
    action: str = "query",
    target: str = "",
    selector: str = "",
    expression: str = "",
    command: str = "",
) -> dict[str, Any]:
    row = _as_row(row)
    method = (action or "query").strip().lower()
    if method in {"exec", "run", "command"} or (command and method not in {"tabs", "query", "eval"}):
        output = run_opencli_command(row, command or expression)
        return {"action": "exec", "command": command or expression or getattr(row, "command", ""), "output": output}
    if method == "tabs":
        try:
            tabs = list_tabs(row, target)
            return {"action": "tabs", "count": len(tabs), "tabs": tabs, "filter": (target or row.target or "")}
        except Exception:
            output = run_opencli_command(row, command or "tab list")
            return {"action": "exec", "command": command or "tab list", "output": output}
    try:
        tabs = list_tabs(row, target)
    except Exception:
        output = run_opencli_command(row, command or expression)
        return {"action": "exec", "command": command or getattr(row, "command", ""), "output": output}
    chosen = _pick_tab(tabs, target or row.target)
    if chosen is None:
        return {"action": method, "error": "没有匹配的标签。先调用 opencli_tabs，或把 target 写成 URL 关键字 / 标签 id。", "tabs": tabs[:8]}
    if method == "eval":
        script = (expression or "").strip() or "document.title"
        text = _evaluate(row, chosen, script)
        return {"action": "eval", "tab": chosen, "expression": script, "result": text}
    script = _query_script(selector)
    text = _evaluate(row, chosen, script)
    return {"action": "query", "tab": chosen, "text": text}


def opencli_tool_specs() -> list[dict[str, Any]]:
    return [
        {
            "name": "opencli",
            "description": "执行一段 OpenCLI 指令，查询远程浏览器标签或页面数据。不填 command 则执行已登记的默认指令。",
            "parameters": {
                "type": "object",
                "properties": {
                    "opencli": {"type": "string", "description": "OpenCLI MCP 名称，不填则用已绑定的第一个"},
                    "command": {"type": "string", "description": "例如 tab list，或 eval document.title。可省略 opencli 前缀。"},
                },
            },
        },
        {
            "name": "opencli_tabs",
            "description": "列出已注册 OpenCLI / 远程 Chrome 里的浏览器标签（标题、URL、id）。查询某个页面数据前先调用。",
            "parameters": {
                "type": "object",
                "properties": {
                    "opencli": {"type": "string", "description": "OpenCLI 配置名称，不填则用已绑定的第一个"},
                    "target": {"type": "string", "description": "按 URL 或标题关键字过滤"},
                },
            },
        },
        {
            "name": "opencli_query",
            "description": "读取指定浏览器标签的标题、地址和可见文本。target 填标签 id 或 URL 关键字。",
            "parameters": {
                "type": "object",
                "properties": {
                    "opencli": {"type": "string", "description": "OpenCLI 配置名称"},
                    "target": {"type": "string", "description": "标签 id 或 URL/标题关键字"},
                    "selector": {"type": "string", "description": "可选 CSS，只提取该节点文本"},
                },
            },
        },
        {
            "name": "opencli_eval",
            "description": "在指定标签执行只读 JavaScript 并返回结果，例如 document.title 或 JSON.stringify(location.href)。",
            "parameters": {
                "type": "object",
                "properties": {
                    "opencli": {"type": "string", "description": "OpenCLI 配置名称"},
                    "target": {"type": "string", "description": "标签 id 或 URL/标题关键字"},
                    "expression": {"type": "string", "description": "JavaScript 表达式"},
                },
                "required": ["expression"],
            },
        },
    ]


def _normalize_tab(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": str(item.get("id") or item.get("targetId") or item.get("webSocketDebuggerUrl") or ""),
        "title": str(item.get("title") or ""),
        "url": str(item.get("url") or ""),
        "type": str(item.get("type") or "page"),
    }


def _pick_tab(tabs: list[dict[str, Any]], target: str) -> Optional[dict[str, Any]]:
    needle = (target or "").strip().lower()
    if not tabs:
        return None
    if not needle:
        return next((item for item in tabs if item.get("type") == "page"), tabs[0])
    for item in tabs:
        blob = f"{item.get('id')} {item.get('url')} {item.get('title')}".lower()
        if needle in blob or item.get("id") == target:
            return item
    return None


def _query_script(selector: str) -> str:
    css = (selector or "").strip()
    if css:
        return (
            "(function(){const el=document.querySelector("
            + json.dumps(css)
            + "); return el ? (el.innerText||el.textContent||'') : '';})()"
        )
    return "(document.body && (document.body.innerText||document.body.textContent)||'').slice(0,8000)"


def _evaluate(row: Any, tab: dict[str, Any], expression: str) -> str:
    text = _evaluate_playwright(row, tab, expression)
    if text is not None:
        return text[:TEXT_LIMIT]
    cli = _opencli_cli(row, ["eval", "--tab", tab["id"], expression] if tab.get("id") else ["eval", expression])
    if cli:
        return cli[:TEXT_LIMIT]
    title = tab.get("title") or ""
    url = tab.get("url") or ""
    return f"已定位标签，但无法读取正文（需要 CDP WebSocket 或本机 opencli）。title={title} url={url}"


def _evaluate_playwright(row: Any, tab: dict[str, Any], expression: str) -> Optional[str]:
    try:
        from playwright.sync_api import sync_playwright
    except Exception:
        return None
    endpoint = normalize_endpoint(row.endpoint)
    target_id = tab.get("id") or ""
    want = (tab.get("url") or "").rstrip("/")
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.connect_over_cdp(endpoint)
            pages = [page for context in browser.contexts for page in context.pages]
            page = None
            for item in pages:
                current = (item.url or "").rstrip("/")
                if want and (want in current or current in want):
                    page = item
                    break
            if page is None and target_id:
                for item in pages:
                    if target_id in ((item.url or "") + str(getattr(item, "guid", "") or "")):
                        page = item
                        break
            if page is None and pages:
                page = pages[0]
            if page is None:
                return None
            result = page.evaluate(expression)
            if result is None:
                return ""
            if isinstance(result, (dict, list)):
                return json.dumps(result, ensure_ascii=False)[:TEXT_LIMIT]
            return str(result)[:TEXT_LIMIT]
    except Exception:
        return None


def _opencli_cli(row: Any, args: list[str]) -> Optional[str]:
    binary = shutil.which("opencli")
    if not binary:
        return None
    session = (row.session or "agentforge").strip() or "agentforge"
    env = os.environ.copy()
    if normalize_kind(row.kind) == "cdp":
        env["OPENCLI_CDP_ENDPOINT"] = normalize_endpoint(row.endpoint)
        if row.target:
            env["OPENCLI_CDP_TARGET"] = row.target
    command = [binary, "browser", session, *args]
    try:
        completed = subprocess.run(command, capture_output=True, text=True, timeout=45, env=env, check=False)
    except Exception:
        return None
    output = (completed.stdout or completed.stderr or "").strip()
    return output or None


def _tabs_from_cli(raw: str) -> list[dict[str, Any]]:
    tabs = []
    try:
        data = json.loads(raw)
        rows = data if isinstance(data, list) else data.get("tabs") or []
        for item in rows:
            if isinstance(item, dict):
                tabs.append(_normalize_tab(item))
        if tabs:
            return tabs
    except json.JSONDecodeError:
        pass
    for line in raw.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        tabs.append({"id": line.split()[0], "title": line, "url": "", "type": "page"})
    return tabs


def _safe_json(response: httpx.Response) -> Any:
    try:
        return response.json()
    except Exception:
        return None


def _safe_text(response: httpx.Response) -> str:
    return (response.text or "").strip()[:400]
