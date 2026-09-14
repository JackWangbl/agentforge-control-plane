"""Execute code inside AgentScope sandboxes, with a policy-enforced local fallback."""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Agent, SandboxPolicy
from app.services.docker_sandbox import (
    allowed_images,
    docker_available,
    docker_image,
    run_in_docker,
)

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


def sandbox_workdir(
    row: SandboxPolicy,
    tenant_id: int = 0,
    execution_id: str = "standalone",
) -> Path:
    safe_execution = re.sub(r"[^a-zA-Z0-9._-]+", "-", execution_id)[:96]
    box_name = re.sub(r"[^a-zA-Z0-9._-]+", "-", row.name or "box")
    path = SANDBOX_ROOT / f"tenant-{tenant_id}" / f"policy-{row.id or 0}-{box_name}" / safe_execution
    path.mkdir(parents=True, exist_ok=True)
    return path


def detect_backends() -> list[str]:
    found = ["local"]
    if docker_available():
        found.append("docker")
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
    if docker_image(runtime) is not None:
        return "docker"
    if any(token in runtime for token in ("docker", "agentscope", "runtime-sandbox", "e2b", "k8s")):
        if "agentscope_runtime" in detect_backends():
            return "agentscope_runtime"
        if "agentscope_workspace" in detect_backends():
            return "agentscope_workspace"
    return "local"


def backend_ready(row: SandboxPolicy) -> bool:
    backend = preferred_backend(row)
    if backend == "docker":
        image = docker_image(row.runtime)
        return bool(docker_available() and image and image in allowed_images())
    if backend == "local":
        return _local_backend_allowed()
    return backend in detect_backends()


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


def _safe_subprocess_env(workdir: Path) -> dict[str, str]:
    allowed = ("PATH", "LANG", "LC_ALL", "TZ", "SSL_CERT_FILE", "SSL_CERT_DIR")
    env = {key: os.environ[key] for key in allowed if os.environ.get(key)}
    env.update({"HOME": str(workdir), "TMPDIR": str(workdir), "PYTHONDONTWRITEBYTECODE": "1"})
    return env


def _local_backend_allowed() -> bool:
    return os.getenv("ALLOW_UNSAFE_LOCAL_SANDBOX", "").strip().lower() in {
        "1", "true", "yes",
    }


def _run_local(
    kind: str,
    payload: str,
    row: SandboxPolicy,
    tenant_id: int = 0,
    execution_id: str = "standalone",
) -> dict[str, Any]:
    if not _local_backend_allowed():
        return {
            "ok": False,
            "backend": "local",
            "output": "",
            "error": "宿主机本地沙箱默认禁用，请配置独立沙箱运行时",
        }
    timeout = parse_timeout(row)
    workdir = sandbox_workdir(row, tenant_id, execution_id)
    deny = network_denied(row)
    env = _safe_subprocess_env(workdir)
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


def execute_in_sandbox(
    row: SandboxPolicy,
    kind: str,
    payload: str,
    tenant_id: int = 0,
    execution_id: str = "standalone",
) -> dict[str, Any]:
    if not row.enabled:
        return {"ok": False, "backend": "none", "output": "", "error": "沙箱已停用"}
    body = (payload or "").strip()
    if not body:
        return {"ok": False, "backend": "none", "output": "", "error": "没有可执行内容"}
    backend = preferred_backend(row)
    if backend == "docker":
        return run_in_docker(
            runtime=row.runtime,
            kind=kind,
            payload=body,
            workdir=sandbox_workdir(row, tenant_id, execution_id),
            tenant_id=tenant_id,
            execution_id=execution_id,
            timeout_seconds=parse_timeout(row),
            memory_bytes=parse_memory_bytes(row),
            cpu_limit=row.cpu_limit,
            network_denied=network_denied(row),
        )
    if backend == "agentscope_runtime":
        result = _run_agentscope_runtime(kind, body, parse_timeout(row))
        if result and not result.get("fallback"):
            return result
    return _run_local(kind, body, row, tenant_id, execution_id)


def probe_sandbox(row: SandboxPolicy) -> dict[str, Any]:
    tenant_id = int(getattr(row, "tenant_id", 0) or 0)
    execution_id = f"probe-{row.id or 0}"
    python = execute_in_sandbox(
        row,
        "python",
        "print('sandbox-ok', 1+1)",
        tenant_id,
        execution_id,
    )
    network = execute_in_sandbox(
        row,
        "python",
        "import socket\nprint(socket.create_connection(('1.1.1.1', 53), 1))",
        tenant_id,
        execution_id,
    )
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


def selected_sandbox(agent: Agent, db: Session) -> Optional[SandboxPolicy]:
    sandbox_id = getattr(agent, "sandbox_id", None)
    if not sandbox_id:
        return None
    row = db.scalar(select(SandboxPolicy).where(
        SandboxPolicy.id == int(sandbox_id),
        SandboxPolicy.tenant_id == agent.tenant_id,
    ))
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


def run_sandbox_tool(
    row: SandboxPolicy,
    name: str,
    arguments: dict[str, Any],
    *,
    tenant_id: int = 0,
    execution_id: str = "standalone",
) -> str:
    if name == "sandbox_run_python":
        result = execute_in_sandbox(row, "python", str(arguments.get("code") or ""), tenant_id, execution_id)
    elif name == "sandbox_run_shell":
        result = execute_in_sandbox(row, "shell", str(arguments.get("command") or ""), tenant_id, execution_id)
    else:
        return json.dumps({"error": f"未知沙箱工具 {name}"}, ensure_ascii=False)
    if result.get("ok"):
        return result.get("output") or "(无输出)"
    return json.dumps({"error": result.get("error") or "沙箱执行失败", "output": result.get("output") or "", "backend": result.get("backend")}, ensure_ascii=False)
