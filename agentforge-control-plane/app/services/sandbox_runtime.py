"""Execute code inside AgentScope sandboxes, with a policy-enforced local fallback."""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Optional

from app.models import Agent, SandboxPolicy

ROOT = Path(__file__).resolve().parents[2]
SANDBOX_ROOT = Path(os.getenv("SANDBOXES_DIR") or (ROOT / "workspaces" / "_sandboxes"))

_NET_BLOCK = r"""
import socket
class _Denied(socket.socket):
    def __init__(self, *args, **kwargs):
        raise OSError("sandbox network is denied")
socket.socket = _Denied
socket.create_connection = lambda *a, **k: (_ for _ in ()).throw(OSError("sandbox network is denied"))
"""


def parse_timeout(row: SandboxPolicy) -> int:
    return max(1, min(int(row.timeout_seconds or 60), 3600))


def parse_memory_bytes(row: SandboxPolicy) -> int:
    text = str(row.memory_limit or "1 GiB").strip().lower().replace(" ", "")
    match = re.match(r"^(\d+(?:\.\d+)?)(gi?b?|mi?b?|ki?b?|b)?$", text)
    if not match:
        return 1024 * 1024 * 1024
    value = float(match.group(1))
    unit = match.group(2) or "gib"
    if unit.startswith("g"):
        return int(value * 1024 * 1024 * 1024)
    if unit.startswith("m"):
        return int(value * 1024 * 1024)
    if unit.startswith("k"):
        return int(value * 1024)
    return int(value)


def network_denied(row: SandboxPolicy) -> bool:
    return str(row.network_mode or "deny").lower() in {"deny", "denied", "none", "off"}


def sandbox_workdir(row: SandboxPolicy) -> Path:
    path = SANDBOX_ROOT / f"{row.id or 0}-{re.sub(r'[^a-zA-Z0-9._-]+', '-', row.name or 'box')}"
    path.mkdir(parents=True, exist_ok=True)
    return path


def detect_backends() -> list[str]:
    found = ["local"]
    try:
        import agentscope_runtime.sandbox  # noqa: F401
        found.append("agentscope_runtime")
    except Exception:
        pass
    try:
        import agentscope.workspace  # noqa: F401
        found.append("agentscope_workspace")
    except Exception:
        pass
    return found


def preferred_backend(row: SandboxPolicy) -> str:
    runtime = str(row.runtime or "").lower()
    if any(token in runtime for token in ("docker", "agentscope", "runtime-sandbox", "e2b", "k8s")):
        if "agentscope_runtime" in detect_backends():
            return "agentscope_runtime"
        if "agentscope_workspace" in detect_backends():
            return "agentscope_workspace"
    return "local"


def _normalize_agentscope_result(raw: Any) -> str:
    if raw is None:
        return ""
    if isinstance(raw, str):
        return raw
    if isinstance(raw, dict):
        for key in ("text", "output", "content", "result", "stdout"):
            if raw.get(key):
                return str(raw[key])
        return json.dumps(raw, ensure_ascii=False)
    return str(raw)


def _run_agentscope_runtime(kind: str, payload: str, timeout: int) -> Optional[dict[str, Any]]:
    try:
        from agentscope_runtime.sandbox import BaseSandbox
    except Exception:
        return None
    try:
        with BaseSandbox() as box:
            if kind == "python":
                raw = box.run_ipython_cell(code=payload)
            else:
                raw = box.run_shell_command(command=payload)
        return {"ok": True, "backend": "agentscope_runtime", "output": _normalize_agentscope_result(raw)}
    except Exception as exc:
        return {"ok": False, "backend": "agentscope_runtime", "output": "", "error": str(exc), "fallback": True}


def _preexec(memory_bytes: int):
    def _limit() -> None:
        try:
            import resource
            if memory_bytes >= 256 * 1024 * 1024:
                resource.setrlimit(resource.RLIMIT_AS, (memory_bytes, memory_bytes))
            resource.setrlimit(resource.RLIMIT_CPU, (60, 60))
        except Exception:
            return
    return _limit


def _sandbox_exec_prefix(workdir: Path) -> list[str]:
    if sys.platform == "darwin" and shutil.which("sandbox-exec"):
        profile = (
            '(version 1)\n'
            '(allow default)\n'
            '(deny network*)\n'
            f'(allow file-write* (subpath "{workdir}"))\n'
        )
        return ["sandbox-exec", "-p", profile]
    if shutil.which("unshare"):
        return ["unshare", "--net", "--map-root-user"]
    return []


def _run_local(kind: str, payload: str, row: SandboxPolicy) -> dict[str, Any]:
    timeout = parse_timeout(row)
    workdir = sandbox_workdir(row)
    deny = network_denied(row)
    env = os.environ.copy()
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    prefix = _sandbox_exec_prefix(workdir) if deny else []
    if kind == "python":
        script = (_NET_BLOCK if deny else "") + "\n" + payload
        inner = [sys.executable, "-I", "-c", script]
    else:
        inner = ["/bin/sh", "-c", payload]
    try:
        completed = subprocess.run(
            prefix + inner,
            cwd=str(workdir),
            env=env,
            capture_output=True,
            text=True,
            timeout=timeout,
            preexec_fn=_preexec(parse_memory_bytes(row)) if os.name == "posix" else None,
        )
        text = (completed.stdout or "") + (("\n" + completed.stderr) if completed.stderr else "")
        if prefix and completed.returncode != 0 and "Operation not permitted" in text:
            completed = subprocess.run(
                inner,
                cwd=str(workdir),
                env=env,
                capture_output=True,
                text=True,
                timeout=timeout,
                preexec_fn=_preexec(parse_memory_bytes(row)) if os.name == "posix" else None,
            )
            text = (completed.stdout or "") + (("\n" + completed.stderr) if completed.stderr else "")
    except subprocess.TimeoutExpired:
        return {"ok": False, "backend": "local", "output": "", "error": f"执行超时（{timeout}s）"}
    except Exception as exc:
        return {"ok": False, "backend": "local", "output": "", "error": str(exc)}
    if completed.returncode != 0:
        return {"ok": False, "backend": "local", "output": text.strip(), "error": text.strip() or f"exit {completed.returncode}"}
    return {"ok": True, "backend": "local", "output": text.strip()}


def execute_in_sandbox(row: SandboxPolicy, kind: str, payload: str) -> dict[str, Any]:
    if not row.enabled:
        return {"ok": False, "backend": "none", "output": "", "error": "沙箱已停用"}
    body = (payload or "").strip()
    if not body:
        return {"ok": False, "backend": "none", "output": "", "error": "没有可执行内容"}
    backend = preferred_backend(row)
    if backend == "agentscope_runtime":
        result = _run_agentscope_runtime(kind, body, parse_timeout(row))
        if result and not result.get("fallback"):
            return result
    return _run_local(kind, body, row)


def probe_sandbox(row: SandboxPolicy) -> dict[str, Any]:
    python = execute_in_sandbox(row, "python", "print('sandbox-ok', 1+1)")
    network = execute_in_sandbox(row, "python", "import socket\nprint(socket.create_connection(('1.1.1.1', 53), 1))")
    denied = network_denied(row)
    network_ok = (not network.get("ok")) if denied else True
    ready = bool(python.get("ok")) and network_ok
    if denied and network.get("ok"):
        message = f"{row.name} 代码能跑，但网络隔离未生效。"
    elif python.get("ok"):
        message = f"{row.name} 已用 {python.get('backend')} 后端跑通：{python.get('output')}"
    else:
        message = f"{row.name} 试跑失败：{python.get('error') or python.get('output')}"
    return {
        "ready": ready,
        "backend": python.get("backend") or preferred_backend(row),
        "available_backends": detect_backends(),
        "sample": python.get("output") or "",
        "network_isolated": denied and not network.get("ok"),
        "message": message,
        "error": "" if ready else (python.get("error") or network.get("error") or ""),
    }


def selected_sandbox(agent: Agent, db) -> Optional[SandboxPolicy]:
    sandbox_id = getattr(agent, "sandbox_id", None)
    if not sandbox_id:
        return None
    row = db.get(SandboxPolicy, int(sandbox_id))
    if row is None or not row.enabled:
        return None
    return row


def sandbox_tool_specs() -> list[dict[str, Any]]:
    return [
        {
            "name": "sandbox_run_python",
            "description": "在 Agent 绑定的沙箱里执行 Python 代码。需要计算、写文件或验证网络隔离时调用。",
            "parameters": {
                "type": "object",
                "properties": {"code": {"type": "string", "description": "要执行的 Python 代码"}},
                "required": ["code"],
            },
        },
        {
            "name": "sandbox_run_shell",
            "description": "在 Agent 绑定的沙箱里执行 shell 命令，受策略的超时和网络限制约束。",
            "parameters": {
                "type": "object",
                "properties": {"command": {"type": "string", "description": "例如 ls 或 echo hello"}},
                "required": ["command"],
            },
        },
    ]


def run_sandbox_tool(row: SandboxPolicy, name: str, arguments: dict[str, Any]) -> str:
    if name == "sandbox_run_python":
        result = execute_in_sandbox(row, "python", str(arguments.get("code") or ""))
    elif name == "sandbox_run_shell":
        result = execute_in_sandbox(row, "shell", str(arguments.get("command") or ""))
    else:
        return json.dumps({"error": f"未知沙箱工具 {name}"}, ensure_ascii=False)
    if result.get("ok"):
        return result.get("output") or "(无输出)"
    return json.dumps({"error": result.get("error") or "沙箱执行失败", "output": result.get("output") or "", "backend": result.get("backend")}, ensure_ascii=False)
