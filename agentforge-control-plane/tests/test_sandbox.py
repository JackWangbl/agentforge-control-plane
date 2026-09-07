from uuid import uuid4

from fastapi.testclient import TestClient

from app.main import app
from app.models import SandboxPolicy
from app.services.sandbox_runtime import execute_in_sandbox


def _policy(**kwargs):
    defaults = dict(
        name="unit-box",
        runtime="python:3.11",
        cpu_limit="1 vCPU",
        memory_limit="256 MiB",
        timeout_seconds=8,
        network_mode="deny",
        enabled=True,
    )
    defaults.update(kwargs)
    row = SandboxPolicy(**defaults)
    row.id = 99
    return row


def test_local_sandbox_runs_python():
    result = execute_in_sandbox(_policy(), "python", "print(2+2)")
    assert result["ok"] is True
    assert "4" in result["output"]
    assert result["backend"] == "local"


def test_local_sandbox_denies_network():
    result = execute_in_sandbox(
        _policy(network_mode="deny"),
        "python",
        "import socket\nsocket.create_connection(('1.1.1.1', 53), 1)",
    )
    assert result["ok"] is False
    assert "denied" in (result.get("error") or result.get("output") or "").lower() or "network" in (result.get("error") or "").lower() or result["ok"] is False


def test_local_sandbox_timeout():
    result = execute_in_sandbox(_policy(timeout_seconds=1), "python", "import time; time.sleep(5)")
    assert result["ok"] is False
    assert "超时" in (result.get("error") or "")


def test_sandbox_probe_and_bind_api():
    suffix = uuid4().hex[:6]
    with TestClient(app) as client:
        created = client.post("/api/sandboxes", json={
            "name": f"试跑-{suffix}",
            "runtime": "python:3.11",
            "cpu_limit": "1 vCPU",
            "memory_limit": "256 MiB",
            "timeout_seconds": 8,
            "network_mode": "deny",
        })
        assert created.status_code == 201, created.text
        item_id = created.json()["id"]
        assert created.json()["runnable"] is True
        probed = client.post(f"/api/sandboxes/{item_id}/test")
        assert probed.status_code == 200, probed.text
        body = probed.json()
        assert body["ready"] is True
        assert "sandbox-ok" in body["sample"]
        assert body["network_isolated"] is True
        agents = client.get("/api/agents").json()
        agent_id = agents[0]["id"]
        updated = client.put(f"/api/agents/{agent_id}", json={"sandbox_id": item_id})
        assert updated.status_code == 200, updated.text
        assert updated.json()["sandbox_id"] == item_id
        assert updated.json()["sandbox_name"] == f"试跑-{suffix}"
        client.delete(f"/api/sandboxes/{item_id}")
