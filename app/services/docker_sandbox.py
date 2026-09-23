"""Run untrusted Agent code in short-lived, hardened Docker containers."""
from __future__ import annotations

import hashlib
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any, Optional
from uuid import uuid4


DEFAULT_IMAGE = "python:3.11-slim"
DEFAULT_ALLOWED_IMAGES = {"python:3.11-slim", "python:3.12-slim"}
MAX_OUTPUT_CHARS = 20_000
_SAFE_NAME = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_.:/@-]{0,199}$")
_SAFE_NETWORK = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_.-]{0,127}$")


def docker_available() -> bool:
    return shutil.which("docker") is not None


def docker_image(runtime: str) -> Optional[str]:
    value = (runtime or "").strip()
    lowered = value.lower()
    if lowered == "docker":
        return os.getenv("SANDBOX_DEFAULT_IMAGE", DEFAULT_IMAGE).strip() or DEFAULT_IMAGE
    if lowered.startswith("docker://"):
        return value[len("docker://"):].strip()
    if lowered.startswith("docker:"):
        return value[len("docker:"):].strip()
    return None


def allowed_images() -> set[str]:
    configured = os.getenv("SANDBOX_ALLOWED_IMAGES", "").strip()
    if not configured:
        return set(DEFAULT_ALLOWED_IMAGES)
    return {item.strip() for item in configured.split(",") if item.strip()}


def parse_cpu_limit(value: str) -> float:
    match = re.search(r"\d+(?:\.\d+)?", str(value or "1"))
    parsed = float(match.group(0)) if match else 1.0
    return max(0.1, min(parsed, 8.0))


def run_in_docker(
    *,
    runtime: str,
    kind: str,
    payload: str,
    workdir: Path,
    tenant_id: int,
    execution_id: str,
    timeout_seconds: int,
    memory_bytes: int,
    cpu_limit: str,
    network_denied: bool,
) -> dict[str, Any]:
    image = docker_image(runtime)
    if not image or not _SAFE_NAME.fullmatch(image) or image not in allowed_images():
        return _error("Docker 镜像未列入 SANDBOX_ALLOWED_IMAGES 白名单")
    if not docker_available():
        return _error("Docker 不可用，拒绝回退到控制面宿主机")

    network_args, network_error = _network_args(network_denied)
    if network_error:
        return _error(network_error)

    workdir = workdir.resolve()
    workdir.mkdir(parents=True, exist_ok=True)
    container_name = _container_name(tenant_id, execution_id)
    inner, script_path = _inner_command(kind, payload, workdir)
    command = [
        "docker", "run", "--rm", "--pull=never",
        "--name", container_name,
        "--label", f"agentforge.tenant_id={tenant_id}",
        "--label", f"agentforge.execution_id={_label_value(execution_id)}",
        *network_args,
        "--init",
        "--read-only",
        "--cap-drop=ALL",
        "--security-opt", "no-new-privileges",
        "--pids-limit", "64",
        "--memory", str(memory_bytes),
        "--memory-swap", str(memory_bytes),
        "--cpus", str(parse_cpu_limit(cpu_limit)),
        "--ulimit", "nofile=256:256",
        "--user", f"{os.getuid()}:{os.getgid()}",
        "--workdir", "/workspace",
        "--env", "HOME=/workspace",
        "--env", "TMPDIR=/tmp",
        "--env", "PYTHONDONTWRITEBYTECODE=1",
        "--mount", f"type=bind,src={workdir},dst=/workspace,rw",
        "--tmpfs", "/tmp:rw,noexec,nosuid,nodev,size=64m",
        image,
        *inner,
    ]
    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            env=_docker_cli_env(),
        )
    except subprocess.TimeoutExpired:
        _force_remove(container_name)
        return _error(f"Docker 沙箱执行超时（{timeout_seconds}s）")
    except Exception as exc:
        _force_remove(container_name)
        return _error(str(exc))
    finally:
        if script_path is not None:
            script_path.unlink(missing_ok=True)

    output = _bounded_output(completed.stdout, completed.stderr)
    if completed.returncode != 0:
        return _error(output or f"Docker exit {completed.returncode}", output=output)
    return {"ok": True, "backend": "docker", "output": output}


def _network_args(denied: bool) -> tuple[list[str], str]:
    if denied:
        return ["--network", "none"], ""
    network = os.getenv("SANDBOX_EGRESS_NETWORK", "").strip()
    if not network or not _SAFE_NETWORK.fullmatch(network):
        return [], "联网沙箱必须配置受控的 SANDBOX_EGRESS_NETWORK"
    return ["--network", network], ""


def _inner_command(kind: str, payload: str, workdir: Path) -> tuple[list[str], Optional[Path]]:
    if kind == "python":
        scripts = workdir / ".agentforge"
        scripts.mkdir(parents=True, exist_ok=True)
        script = scripts / f"run-{uuid4().hex}.py"
        script.write_text(payload, encoding="utf-8")
        return ["python", "-I", f"/workspace/.agentforge/{script.name}"], script
    return ["/bin/sh", "-lc", payload], None


def _container_name(tenant_id: int, execution_id: str) -> str:
    digest = hashlib.sha256(f"{tenant_id}:{execution_id}:{uuid4().hex}".encode()).hexdigest()[:20]
    return f"agentforge-t{max(0, tenant_id)}-{digest}"


def _label_value(value: str) -> str:
    return re.sub(r"[^a-zA-Z0-9_.-]+", "-", value)[:120] or "standalone"


def _docker_cli_env() -> dict[str, str]:
    allowed = ("PATH", "DOCKER_HOST", "DOCKER_CONTEXT", "DOCKER_TLS_VERIFY", "DOCKER_CERT_PATH")
    return {key: os.environ[key] for key in allowed if os.environ.get(key)}


def _force_remove(container_name: str) -> None:
    try:
        subprocess.run(
            ["docker", "rm", "-f", container_name],
            capture_output=True,
            text=True,
            timeout=5,
            env=_docker_cli_env(),
        )
    except Exception:
        pass


def _bounded_output(stdout: Optional[str], stderr: Optional[str]) -> str:
    combined = (stdout or "") + (("\n" + stderr) if stderr else "")
    combined = combined.strip()
    if len(combined) <= MAX_OUTPUT_CHARS:
        return combined
    return combined[:MAX_OUTPUT_CHARS] + "\n…（沙箱输出已截断）"


def _error(message: str, *, output: str = "") -> dict[str, Any]:
    return {"ok": False, "backend": "docker", "output": output, "error": message}
