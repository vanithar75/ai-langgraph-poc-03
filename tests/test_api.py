from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def _start(sample_id: str) -> str:
    res = client.post("/api/incidents", json={"sample_id": sample_id})
    assert res.status_code == 200, res.text
    return res.json()["incident_id"]


def _resume(incident_id: str, action: str, role: str) -> dict:
    res = client.post(
        f"/api/incidents/{incident_id}/resume",
        json={"action": action, "role": role},
    )
    assert res.status_code == 200, res.text
    return res.json()


def _walk_to_lock(incident_id: str) -> dict:
    roles = {
        "facts": "call_taker",
        "priority": "call_taker",
        "supervisor": "supervisor",
        "dispatch": "dispatcher",
    }
    snap = client.get(f"/api/incidents/{incident_id}").json()
    for _ in range(12):
        interrupt = snap.get("pending_interrupt")
        if not interrupt:
            break
        gate = interrupt["gate"]
        snap = _resume(incident_id, "approve", roles[gate])
    return snap


def test_list_incidents_endpoint():
    incident_id = _start("cardiac")
    res = client.get("/api/incidents")
    assert res.status_code == 200
    ids = [i["incident_id"] for i in res.json()["incidents"]]
    assert incident_id in ids
    row = next(i for i in res.json()["incidents"] if i["incident_id"] == incident_id)
    assert row["pending_gate"] == "facts"
    assert row["locked"] is False


def test_audit_endpoint_after_lock():
    incident_id = _start("cardiac")
    _walk_to_lock(incident_id)
    res = client.get(f"/api/incidents/{incident_id}/audit.json")
    assert res.status_code == 200
    audit = res.json()
    gates = {a["gate"] for a in audit["approval_history"]}
    assert {"facts", "priority", "supervisor", "dispatch"} <= gates
    assert audit["total_seconds"] is not None
    assert audit["locked"] is True


def test_amend_requires_lock_then_versions():
    incident_id = _start("cardiac")
    # Before lock -> 409
    early = client.post(
        f"/api/incidents/{incident_id}/amend",
        json={"role": "dispatcher", "reason": "too early"},
    )
    assert early.status_code == 409

    _walk_to_lock(incident_id)
    res = client.post(
        f"/api/incidents/{incident_id}/amend",
        json={"role": "dispatcher", "reason": "Add road closure note"},
    )
    assert res.status_code == 200, res.text
    state = res.json()["state"]
    assert state["locked"] is True
    assert state["cad_version"] == 2
    assert any(a["reason"] == "Add road closure note" for a in state["amendments"])


def test_new_config_only_incident_type_overdose():
    incident_id = _start("overdose")
    snap = _walk_to_lock(incident_id)
    state = snap["state"]
    assert state["incident_type"] == "Medical — Overdose/Behavioral"
    assert state["locked"] is True
    assert "eido_stub" in state["cad_payload"]
