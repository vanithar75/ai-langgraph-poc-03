from __future__ import annotations

import os
import uuid
from pathlib import Path
from typing import Any, Optional

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from langgraph.types import Command
from pydantic import BaseModel, Field

from . import fixtures
from .graph import create_app_graph

ROOT = Path(__file__).resolve().parent.parent
STATIC = ROOT / "static"

app = FastAPI(title="Demo Director", version="0.1.0")
graph, _conn = create_app_graph()

if STATIC.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC)), name="static")


class DemoStartRequest(BaseModel):
    account_name: Optional[str] = None
    industry: str = ""
    icp: str = ""
    persona: str = "VP Sales Ops"
    pains: list[str] = Field(default_factory=list)
    must_win_outcomes: list[str] = Field(default_factory=list)
    duration_minutes: int = 45
    notes: str = ""
    sample_id: Optional[str] = None
    mode: str = "demo"


class ResumeRequest(BaseModel):
    action: str = "approve"
    role: str = "se"
    feedback: str = ""
    note: str = ""
    edits: dict[str, Any] = Field(default_factory=dict)


def _interrupt_payload(result: dict[str, Any] | Any) -> Optional[dict[str, Any]]:
    if isinstance(result, dict) and "__interrupt__" in result:
        interrupts = result["__interrupt__"]
        if interrupts:
            value = interrupts[0].value
            return value if isinstance(value, dict) else {"payload": value}
    return None


def _snapshot_state(demo_id: str) -> dict[str, Any]:
    config = {"configurable": {"thread_id": demo_id}}
    snap = graph.get_state(config)
    values = dict(snap.values or {})
    interrupt_value = None
    if snap.tasks:
        for task in snap.tasks:
            if getattr(task, "interrupts", None):
                interrupt_value = task.interrupts[0].value
                break
    return {
        "demo_id": demo_id,
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
        "product": fixtures.product_catalog()["product_name"],
        "mode_default": "demo",
        "llm_configured": bool(os.getenv("OPENAI_API_KEY")),
    }


@app.get("/api/samples")
def samples():
    return {"samples": fixtures.sample_accounts()}


@app.get("/api/capabilities")
def capabilities():
    return fixtures.product_catalog()


@app.post("/api/demos")
def start_demo(body: DemoStartRequest):
    payload = body.model_dump()
    if body.sample_id:
        sample = next(
            (s for s in fixtures.sample_accounts() if s["id"] == body.sample_id),
            None,
        )
        if not sample:
            raise HTTPException(404, f"Unknown sample_id: {body.sample_id}")
        payload = {**sample, "mode": body.mode}
        payload.pop("id", None)

    if not payload.get("account_name"):
        raise HTTPException(400, "account_name is required (or provide sample_id)")

    demo_id = str(uuid.uuid4())
    mode = "llm" if payload.get("mode") == "llm" and os.getenv("OPENAI_API_KEY") else "demo"
    initial: dict[str, Any] = {
        "demo_id": demo_id,
        "account_name": payload["account_name"],
        "industry": payload.get("industry") or "",
        "icp": payload.get("icp") or "",
        "persona": payload.get("persona") or "Buyer",
        "pains": list(payload.get("pains") or []),
        "must_win_outcomes": list(payload.get("must_win_outcomes") or []),
        "duration_minutes": int(payload.get("duration_minutes") or 45),
        "notes": payload.get("notes") or "",
        "mode": mode,
        "revision_count": 0,
        "revision_notes": "",
        "approval_history": [],
        "timeline": [],
        "locked": False,
        "status": "started",
    }
    config = {"configurable": {"thread_id": demo_id}}
    result = graph.invoke(initial, config=config)
    interrupt_value = _interrupt_payload(result)
    snap = _snapshot_state(demo_id)
    return {**snap, "invoke_interrupt": interrupt_value}


@app.get("/api/demos/{demo_id}")
def get_demo(demo_id: str):
    try:
        return _snapshot_state(demo_id)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(404, f"Demo not found: {demo_id}") from exc


@app.post("/api/demos/{demo_id}/resume")
def resume_demo(demo_id: str, body: ResumeRequest):
    config = {"configurable": {"thread_id": demo_id}}
    try:
        snap = graph.get_state(config)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(404, f"Demo not found: {demo_id}") from exc

    if not snap.next:
        return {**_snapshot_state(demo_id), "message": "No pending interrupt"}

    decision = {
        "action": body.action,
        "role": body.role,
        "feedback": body.feedback,
        "note": body.note,
        "edits": body.edits,
    }
    graph.invoke(Command(resume=decision), config=config)
    return _snapshot_state(demo_id)


@app.get("/api/demos/{demo_id}/export.md")
def export_markdown(demo_id: str):
    snap = _snapshot_state(demo_id)
    state = snap["state"]
    content = state.get("export_markdown") or fixtures.build_export_markdown(state)
    filename = f"demo-director-{state.get('account_name', 'plan').replace(' ', '-').lower()}.md"
    return PlainTextResponse(
        content,
        media_type="text/markdown",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
