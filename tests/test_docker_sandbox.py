import subprocess
from types import SimpleNamespace

from app.services.docker_sandbox import run_in_docker


def _run(tmp_path, **overrides):
    values = {
        "runtime": "docker:python:3.11-slim",
        "kind": "python",
        "payload": "print('container-ok')",
        "workdir": tmp_path,
        "tenant_id": 23,
        "execution_id": "exec-abc",
        "timeout_seconds": 10,
        "memory_bytes": 256 * 1024 * 1024,
        "cpu_limit": "1 vCPU",
        "network_denied": True,
    }
    values.update(overrides)
    return run_in_docker(**values)


def test_docker_sandbox_uses_hardened_container(monkeypatch, tmp_path):
    commands = []
    monkeypatch.setattr("app.services.docker_sandbox.shutil.which", lambda _name: "/usr/bin/docker")
    monkeypatch.setenv("DATABASE_URL", "mysql://must-not-enter-container")

    def fake_run(command, **kwargs):
        commands.append((command, kwargs))
        return SimpleNamespace(returncode=0, stdout="container-ok\n", stderr="")

    monkeypatch.setattr("app.services.docker_sandbox.subprocess.run", fake_run)
    result = _run(tmp_path)

    assert result == {"ok": True, "backend": "docker", "output": "container-ok"}
    command, kwargs = commands[0]
    assert command[:4] == ["docker", "run", "--rm", "--pull=never"]
    assert ["--network", "none"] == command[command.index("--network"):command.index("--network") + 2]
    assert "--read-only" in command
    assert "--cap-drop=ALL" in command
    assert "no-new-privileges" in command
    assert "--pids-limit" in command
    assert "mysql://must-not-enter-container" not in " ".join(command)
    assert "DATABASE_URL" not in kwargs["env"]
    mount = command[command.index("--mount") + 1]
    assert str(tmp_path.resolve()) in mount
    assert not list((tmp_path / ".agentforge").glob("run-*.py"))


def test_networked_docker_requires_controlled_egress(monkeypatch, tmp_path):
    monkeypatch.setattr("app.services.docker_sandbox.shutil.which", lambda _name: "/usr/bin/docker")
    monkeypatch.delenv("SANDBOX_EGRESS_NETWORK", raising=False)
    result = _run(tmp_path, network_denied=False)
    assert result["ok"] is False
    assert "SANDBOX_EGRESS_NETWORK" in result["error"]


def test_docker_timeout_force_removes_only_execution_container(monkeypatch, tmp_path):
    commands = []
    monkeypatch.setattr("app.services.docker_sandbox.shutil.which", lambda _name: "/usr/bin/docker")

    def fake_run(command, **kwargs):
        commands.append(command)
        if command[1] == "run":
            raise subprocess.TimeoutExpired(command, kwargs.get("timeout", 0))
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr("app.services.docker_sandbox.subprocess.run", fake_run)
    result = _run(tmp_path)
    assert result["ok"] is False
    assert "超时" in result["error"]
    container_name = commands[0][commands[0].index("--name") + 1]
    assert commands[1] == ["docker", "rm", "-f", container_name]
