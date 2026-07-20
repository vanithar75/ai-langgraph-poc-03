from __future__ import annotations

import json
import os
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, PlainTextResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from langgraph.types import Command
from pydantic import BaseModel, Field

from . import fixtures
from .graph import DB_PATH, create_app_graph

ROOT = Path(__file__).resolve().parent.parent
STATIC = ROOT / "static"

app = FastAPI(
    title="CAD Assist Control Loop",
    version="0.2.0",
    description="Training/demo HITL call-take → CAD draft. Not operational PSAP software.",
)
graph, _conn = create_app_graph()

if STATIC.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC)), name="static")


class IncidentStartRequest(BaseModel):
    narrative: Optional[str] = None
    sample_id: Optional[str] = None
    mode: str = "demo"


class ResumeRequest(BaseModel):
    action: str = "approve"
    role: str = "call_taker"
    feedback: str = ""
    note: str = ""
    edits: dict[str, Any] = Field(default_factory=dict)


class AmendRequest(BaseModel):
    role: str = "dispatcher"
    reason: str = ""
    edits: dict[str, Any] = Field(default_factory=dict)


def _list_incidents() -> list[dict[str, Any]]:
    """Enumerate incidents by reading distinct checkpoint threads.

    Uses a short-lived read connection to avoid contending with the
    checkpointer's writer connection.
    """
    try:
        read_conn = sqlite3.connect(str(DB_PATH))
        rows = read_conn.execute(
            "SELECT DISTINCT thread_id FROM checkpoints"
        ).fetchall()
        read_conn.close()
    except Exception:
        return []

    incidents: list[dict[str, Any]] = []
    for (thread_id,) in rows:
        try:
            snap = graph.get_state({"configurable": {"thread_id": thread_id}})
        except Exception:
            continue
        values = snap.values or {}
        if not values:
            continue
        pending = None
        if snap.tasks:
            for task in snap.tasks:
                if getattr(task, "interrupts", None):
                    val = task.interrupts[0].value
                    pending = val.get("gate") if isinstance(val, dict) else None
                    break
        incidents.append(
            {
                "incident_id": thread_id,
                "incident_type": values.get("incident_type"),
                "status": values.get("status"),
                "priority": values.get("priority"),
                "locked": bool(values.get("locked")),
                "pending_gate": pending,
                "supervisor_escalate": bool(values.get("supervisor_escalate")),
                "created_at": values.get("created_at"),
            }
        )
    incidents.sort(key=lambda i: i.get("created_at") or "", reverse=True)
    return incidents


def _interrupt_payload(result: dict[str, Any] | Any) -> Optional[dict[str, Any]]:
    if isinstance(result, dict) and "__interrupt__" in result:
        interrupts = result["__interrupt__"]
        if interrupts:
            value = interrupts[0].value
            return value if isinstance(value, dict) else {"payload": value}
    return None


def _snapshot_state(incident_id: str) -> dict[str, Any]:
    config = {"configurable": {"thread_id": incident_id}}
    snap = graph.get_state(config)
    values = dict(snap.values or {})
    interrupt_value = None
    if snap.tasks:
        for task in snap.tasks:
            if getattr(task, "interrupts", None):
                interrupt_value = task.interrupts[0].value
                break
    return {
        "incident_id": incident_id,
        "state": values,
        "pending_interrupt": interrupt_value,
        "next": list(snap.next or []),
    }


@app.get("/")
def index():
    index_path = STATIC / "index.html"
    if not index_path.exists():
        raise HTTPException(404, "UI not found")
    return FileResponse(index_path)


@app.get("/api/health")
def health():
    return {
        "ok": True,
        "product": "CAD Assist Control Loop",
        "mode_default": "demo",
        "llm_configured": bool(os.getenv("OPENAI_API_KEY")),
        "disclaimer": "Training/demo assist only. Not operational PSAP software.",
    }


@app.get("/api/samples")
def samples():
    return {
        "samples": [
            {
                "id": s["id"],
                "title": s["title"],
                "protocol_path": s["protocol_path"],
                "narrative": s["narrative"],
            }
            for s in fixtures.sample_incidents()
        ]
    }


@app.get("/api/units")
def units():
    return {"units": fixtures.unit_roster()}


@app.post("/api/incidents")
def start_incident(body: IncidentStartRequest):
    sample = None
    narrative = (body.narrative or "").strip()
    if body.sample_id:
        sample = fixtures.get_sample(body.sample_id)
        if not sample:
            raise HTTPException(404, f"Unknown sample_id: {body.sample_id}")
        narrative = narrative or sample["narrative"]

    if not narrative:
        raise HTTPException(400, "narrative or sample_id is required")

    incident_id = str(uuid.uuid4())
    mode = "llm" if body.mode == "llm" and os.getenv("OPENAI_API_KEY") else "demo"
    initial: dict[str, Any] = {
        "incident_id": incident_id,
        "sample_id": body.sample_id or (sample["id"] if sample else ""),
        "narrative": narrative,
        "mode": mode,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "revision_count": 0,
        "revision_notes": "",
        "approval_history": [],
        "timeline": [],
        "locked": False,
        "status": "started",
        "psers_tags": [],
        "supervisor_escalate": False,
        "protocol_answers": {},
        "cad_version": 1,
        "amendments": [],
    }
    config = {"configurable": {"thread_id": incident_id}}
    result = graph.invoke(initial, config=config)
    snap = _snapshot_state(incident_id)
    return {**snap, "invoke_interrupt": _interrupt_payload(result)}


@app.get("/api/incidents")
def list_incidents():
    return {"incidents": _list_incidents()}


@app.get("/api/incidents/{incident_id}")
def get_incident(incident_id: str):
    try:
        return _snapshot_state(incident_id)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(404, f"Incident not found: {incident_id}") from exc


@app.get("/api/incidents/{incident_id}/audit.json")
def audit_incident(incident_id: str):
    snap = _snapshot_state(incident_id)
    if not snap["state"]:
        raise HTTPException(404, f"Incident not found: {incident_id}")
    return fixtures.build_audit(snap["state"])


@app.post("/api/incidents/{incident_id}/amend")
def amend_incident(incident_id: str, body: AmendRequest):
    config = {"configurable": {"thread_id": incident_id}}
    try:
        snap = graph.get_state(config)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(404, f"Incident not found: {incident_id}") from exc
    state = dict(snap.values or {})
    if not state:
        raise HTTPException(404, f"Incident not found: {incident_id}")
    if not state.get("locked"):
        raise HTTPException(
            409, "Incident must be locked (Gate 3 approved) before amendment"
        )
    if not body.reason.strip():
        raise HTTPException(400, "amendment reason is required")
    updates = fixtures.apply_amendment(state, body.role, body.reason, body.edits)
    graph.update_state(config, updates)
    return _snapshot_state(incident_id)


@app.post("/api/incidents/{incident_id}/resume")
def resume_incident(incident_id: str, body: ResumeRequest):
    config = {"configurable": {"thread_id": incident_id}}
    try:
        snap = graph.get_state(config)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(404, f"Incident not found: {incident_id}") from exc

    if not snap.next:
        return {**_snapshot_state(incident_id), "message": "No pending interrupt"}

    decision = {
        "action": body.action,
        "role": body.role,
        "feedback": body.feedback,
        "note": body.note,
        "edits": body.edits,
    }
    graph.invoke(Command(resume=decision), config=config)
    return _snapshot_state(incident_id)


@app.get("/api/incidents/{incident_id}/export.md")
def export_markdown(incident_id: str):
    snap = _snapshot_state(incident_id)
    state = snap["state"]
    content = state.get("export_markdown") or fixtures.build_export_markdown(state)
    filename = f"cad-assist-{state.get('cad_incident_number', incident_id[:8])}.md"
    return PlainTextResponse(
        content,
        media_type="text/markdown",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.get("/api/incidents/{incident_id}/cad.json")
def export_cad_json(incident_id: str):
    snap = _snapshot_state(incident_id)
    state = snap["state"]
    payload = state.get("cad_payload") or {}
    if not payload:
        raise HTTPException(404, "CAD payload not available yet")
    return JSONResponse(
        payload,
        headers={
            "Content-Disposition": (
                f'attachment; filename="cad-{state.get("cad_incident_number", "draft")}.json"'
            )
        },
    )
