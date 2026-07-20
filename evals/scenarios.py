"""Shared helpers for driving the CAD Assist graph inside evals.

Every scenario runs against a throwaway SQLite checkpoint DB so eval runs are
isolated and deterministic.
"""

from __future__ import annotations

import sqlite3
import tempfile
import uuid
from pathlib import Path
from typing import Any, Optional

from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.types import Command

from app import fixtures
from app.graph import build_graph


def fresh_graph() -> tuple[Any, sqlite3.Connection]:
    tmp = Path(tempfile.mkdtemp()) / "eval.db"
    conn = sqlite3.connect(str(tmp), check_same_thread=False)
    return build_graph(SqliteSaver(conn)), conn


def start(
    graph: Any,
    sample_id: Optional[str] = None,
    narrative: str = "",
    mode: str = "demo",
    incident_id: Optional[str] = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    incident_id = incident_id or f"eval-{uuid.uuid4().hex[:8]}"
    if sample_id and not narrative:
        sample = fixtures.get_sample(sample_id)
        narrative = sample["narrative"] if sample else ""
    config = {"configurable": {"thread_id": incident_id}}
    result = graph.invoke(
        {
            "incident_id": incident_id,
            "sample_id": sample_id or "",
            "narrative": narrative,
            "mode": mode,
            "created_at": "",
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
        },
        config=config,
    )
    return result, config


def pending_gate(result: dict[str, Any]) -> Optional[str]:
    interrupts = result.get("__interrupt__") if isinstance(result, dict) else None
    if interrupts:
        value = interrupts[0].value
        return value.get("gate") if isinstance(value, dict) else None
    return None


def resume(graph: Any, config: dict[str, Any], action: str, role: str, **kw) -> dict[str, Any]:
    decision = {"action": action, "role": role}
    decision.update(kw)
    return graph.invoke(Command(resume=decision), config=config)


def state_of(graph: Any, config: dict[str, Any]) -> dict[str, Any]:
    return dict(graph.get_state(config).values or {})


def walk_gates(
    graph: Any,
    config: dict[str, Any],
    result: dict[str, Any],
    stop_at: Optional[str] = None,
) -> dict[str, Any]:
    """Approve through gates until locked, END, or a target gate is reached.

    Role is chosen automatically per gate. If ``stop_at`` is set, returns as
    soon as that gate is pending (without approving it).
    """
    roles = {
        "facts": "call_taker",
        "priority": "call_taker",
        "supervisor": "supervisor",
        "dispatch": "dispatcher",
    }
    guard = 0
    while True:
        guard += 1
        if guard > 12:
            raise RuntimeError("walk_gates exceeded max iterations")
        gate = pending_gate(result)
        if gate is None:
            return result
        if stop_at and gate == stop_at:
            return result
        result = resume(graph, config, "approve", roles.get(gate, "call_taker"))
