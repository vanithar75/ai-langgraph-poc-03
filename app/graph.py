from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any, Literal, Optional

from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from . import fixtures
from .state import ApprovalEvent, IncidentState

ROOT = Path(__file__).resolve().parent.parent
DB_PATH = ROOT / ".checkpoints" / "cad_assist.db"


def _append_timeline(
    state: IncidentState, step: str, detail: str, psers: str | None = None
) -> list[dict[str, Any]]:
    timeline = list(state.get("timeline") or [])
    entry: dict[str, Any] = {"step": step, "detail": detail}
    if psers:
        entry["psers"] = psers
    timeline.append(entry)
    return timeline


def _record_approval(
    state: IncidentState, gate: str, decision: dict[str, Any]
) -> list[ApprovalEvent]:
    history = list(state.get("approval_history") or [])
    history.append(
        {
            "gate": gate,
            "action": str(decision.get("action", "approve")),
            "role": str(decision.get("role", "call_taker")),
            "feedback": str(decision.get("feedback") or ""),
            "note": str(decision.get("note") or ""),
        }
    )
    return history


def extract_facts(state: IncidentState) -> dict[str, Any]:
    extracted = fixtures.extract_from_narrative(
        narrative=state.get("narrative") or "",
        sample_id=state.get("sample_id"),
        revision_notes=state.get("revision_notes") or "",
    )
    tags = list(state.get("psers_tags") or [])
    if "PSERS.PLAT.NG911" not in tags:
        tags.append("PSERS.PLAT.NG911")
    return {
        "sample_id": extracted["sample_id"],
        "protocol_path": extracted["protocol_path"],
        "location": extracted["location"],
        "chief_complaint": extracted["chief_complaint"],
        "incident_type": extracted["incident_type"],
        "people": extracted["people"],
        "vehicles": extracted["vehicles"],
        "hazards": extracted["hazards"],
        "urgency_cues": extracted["urgency_cues"],
        "protocol_answers": extracted.get("default_answers") or {},
        "status": "awaiting_facts_approval",
        "pending_gate": "facts",
        "psers_tags": tags,
        "timeline": _append_timeline(
            state,
            "extract_facts",
            f"Drafted facts for {extracted['incident_type']}",
            "PSERS.PLAT.NG911",
        ),
    }


def gate_facts(state: IncidentState) -> dict[str, Any]:
    decision = interrupt(
        {
            "gate": "facts",
            "title": "Gate 1 — Call Taker confirms incident facts",
            "role_hint": "Call Taker must approve/edit location and chief complaint",
            "actions": ["approve", "edit", "reject"],
            "payload": {
                "location": state.get("location") or {},
                "chief_complaint": state.get("chief_complaint") or "",
                "incident_type": state.get("incident_type") or "",
                "people": state.get("people") or [],
                "vehicles": state.get("vehicles") or [],
                "hazards": state.get("hazards") or [],
                "urgency_cues": state.get("urgency_cues") or [],
            },
        }
    )
    action = str(decision.get("action", "approve"))
    history = _record_approval(state, "facts", decision)
    if action == "reject":
        return {
            "status": "rejected",
            "pending_gate": None,
            "approval_history": history,
            "timeline": _append_timeline(state, "gate_facts", "Rejected at Gate 1"),
        }

    updates: dict[str, Any] = {
        "status": "facts_approved",
        "pending_gate": None,
        "approval_history": history,
        "timeline": _append_timeline(
            state, "gate_facts", f"Call Taker {action}d facts"
        ),
    }
    edits = decision.get("edits") or {}
    if action == "edit":
        for key in (
            "location",
            "chief_complaint",
            "incident_type",
            "people",
            "vehicles",
            "hazards",
            "urgency_cues",
        ):
            if key in edits:
                updates[key] = edits[key]
    return updates


def run_protocol(state: IncidentState) -> dict[str, Any]:
    proto = fixtures.load_protocol(state.get("protocol_path") or "cardiac")
    answers = dict(state.get("protocol_answers") or {})
    # Ensure every question has an answer key for UI editing
    for q in proto["protocol_questions"]:
        answers.setdefault(q["id"], "unknown")

    priority, plan, units, escalate = fixtures.suggest_priority_and_plan(
        protocol_path=state.get("protocol_path") or "cardiac",
        answers=answers,
        urgency_cues=list(state.get("urgency_cues") or []),
    )
    return {
        **proto,
        "protocol_answers": answers,
        "priority": priority,
        "response_plan": plan,
        "recommended_units": units,
        "supervisor_escalate": escalate,
        "status": "awaiting_priority_approval",
        "pending_gate": "priority",
        "timeline": _append_timeline(
            state,
            "run_protocol",
            f"{proto['protocol_name']} → {priority}",
            "PSERS.PLAT.CAD.UNIT_RECOMMEND",
        ),
    }


def gate_priority(state: IncidentState) -> dict[str, Any]:
    decision = interrupt(
        {
            "gate": "priority",
            "title": "Gate 2 — Confirm priority & response plan",
            "role_hint": "Call Taker approves; use Revise to re-extract with feedback. Supervisor escalate is advisory.",
            "actions": ["approve", "edit", "revise", "reject"],
            "payload": {
                "protocol_name": state.get("protocol_name"),
                "protocol_disclaimer": state.get("protocol_disclaimer"),
                "protocol_questions": state.get("protocol_questions") or [],
                "protocol_answers": state.get("protocol_answers") or {},
                "priority": state.get("priority") or "",
                "response_plan": state.get("response_plan") or [],
                "recommended_units": state.get("recommended_units") or [],
                "supervisor_escalate": bool(state.get("supervisor_escalate")),
            },
        }
    )
    action = str(decision.get("action", "approve"))
    role = str(decision.get("role") or "call_taker")
    history = _record_approval(state, "priority", decision)

    if action == "reject":
        return {
            "status": "rejected",
            "pending_gate": None,
            "approval_history": history,
            "timeline": _append_timeline(state, "gate_priority", "Rejected at Gate 2"),
        }

    if action == "revise":
        feedback = str(decision.get("feedback") or "Re-check location and complaint.")
        return {
            "status": "revising",
            "pending_gate": None,
            "revision_notes": feedback,
            "revision_count": int(state.get("revision_count") or 0) + 1,
            "approval_history": history,
            "timeline": _append_timeline(
                state, "gate_priority", f"Revise requested by {role}: {feedback[:100]}"
            ),
        }

    updates: dict[str, Any] = {
        "status": "priority_approved",
        "pending_gate": None,
        "revision_notes": "",
        "approval_history": history,
        "timeline": _append_timeline(
            state, "gate_priority", f"{role} {action}d priority/plan"
        ),
    }
    edits = decision.get("edits") or {}
    if action == "edit":
        for key in (
            "priority",
            "response_plan",
            "recommended_units",
            "protocol_answers",
            "supervisor_escalate",
        ):
            if key in edits:
                updates[key] = edits[key]
        # Recompute plan if answers edited but priority not explicitly set
        if "protocol_answers" in edits and "priority" not in edits:
            pr, plan, units, esc = fixtures.suggest_priority_and_plan(
                state.get("protocol_path") or "cardiac",
                edits["protocol_answers"],
                list(state.get("urgency_cues") or []),
            )
            updates.setdefault("priority", pr)
            updates.setdefault("response_plan", plan)
            updates.setdefault("recommended_units", units)
            updates.setdefault("supervisor_escalate", esc)
    return updates


def cad_draft(state: IncidentState) -> dict[str, Any]:
    draft = fixtures.build_cad_draft(state)
    tags = list(state.get("psers_tags") or [])
    if "PSERS.PLAT.CAD.INCIDENT_CREATE" not in tags:
        tags.append("PSERS.PLAT.CAD.INCIDENT_CREATE")
    return {
        **draft,
        "psers_tags": tags,
        "status": "awaiting_dispatch_approval",
        "pending_gate": "dispatch",
        "timeline": _append_timeline(
            state,
            "cad_draft",
            f"Drafted CAD {draft['cad_incident_number']} (not locked)",
            "PSERS.PLAT.CAD.INCIDENT_CREATE",
        ),
    }


def gate_dispatch(state: IncidentState) -> dict[str, Any]:
    decision = interrupt(
        {
            "gate": "dispatch",
            "title": "Gate 3 — Dispatcher lock before CAD commit",
            "role_hint": "Dispatcher must approve. Nothing hits CAD until this gate.",
            "actions": ["approve", "edit", "reject"],
            "payload": {
                "cad_incident_number": state.get("cad_incident_number"),
                "cad_narrative": state.get("cad_narrative"),
                "cad_payload": state.get("cad_payload") or {},
                "recommended_units": state.get("recommended_units") or [],
                "priority": state.get("priority"),
            },
        }
    )
    action = str(decision.get("action", "approve"))
    history = _record_approval(state, "dispatch", decision)
    if action == "reject":
        return {
            "status": "rejected",
            "pending_gate": None,
            "locked": False,
            "approval_history": history,
            "timeline": _append_timeline(
                state, "gate_dispatch", "Rejected at Gate 3 — CAD not locked"
            ),
        }

    updates: dict[str, Any] = {
        "status": "dispatch_approved",
        "pending_gate": None,
        "approval_history": history,
        "timeline": _append_timeline(
            state, "gate_dispatch", f"Dispatcher {action}d CAD draft"
        ),
    }
    edits = decision.get("edits") or {}
    if action == "edit":
        if "cad_narrative" in edits:
            updates["cad_narrative"] = edits["cad_narrative"]
        if "recommended_units" in edits:
            updates["recommended_units"] = edits["recommended_units"]
        if "cad_payload" in edits:
            updates["cad_payload"] = edits["cad_payload"]
        elif "cad_narrative" in edits or "recommended_units" in edits:
            merged = {
                **state,
                "cad_narrative": updates.get(
                    "cad_narrative", state.get("cad_narrative")
                ),
                "recommended_units": updates.get(
                    "recommended_units", state.get("recommended_units")
                ),
            }
            draft = fixtures.build_cad_draft(merged)
            updates["cad_payload"] = draft["cad_payload"]
            updates["cad_narrative"] = draft["cad_narrative"]
    return updates


def lock_cad(state: IncidentState) -> dict[str, Any]:
    payload = dict(state.get("cad_payload") or {})
    payload["status"] = "LOCKED"
    payload["locked"] = True
    export_md = fixtures.build_export_markdown(
        {**state, "cad_payload": payload, "locked": True, "status": "locked"}
    )
    return {
        "cad_payload": payload,
        "export_markdown": export_md,
        "locked": True,
        "status": "locked",
        "pending_gate": None,
        "timeline": _append_timeline(
            state,
            "lock_cad",
            f"LOCKED {state.get('cad_incident_number')} — human-approved CAD package",
            "PSERS.PLAT.CAD.INCIDENT_CREATE",
        ),
    }


def route_after_facts(state: IncidentState) -> Literal["run_protocol", "__end__"]:
    return END if state.get("status") == "rejected" else "run_protocol"


def route_after_priority(
    state: IncidentState,
) -> Literal["extract_facts", "cad_draft", "__end__"]:
    if state.get("status") == "rejected":
        return END
    if state.get("status") == "revising":
        return "extract_facts"
    return "cad_draft"


def route_after_dispatch(state: IncidentState) -> Literal["lock_cad", "__end__"]:
    return END if state.get("status") == "rejected" else "lock_cad"


def build_graph(checkpointer: Optional[SqliteSaver] = None):
    graph = StateGraph(IncidentState)
    graph.add_node("extract_facts", extract_facts)
    graph.add_node("gate_facts", gate_facts)
    graph.add_node("run_protocol", run_protocol)
    graph.add_node("gate_priority", gate_priority)
    graph.add_node("cad_draft", cad_draft)
    graph.add_node("gate_dispatch", gate_dispatch)
    graph.add_node("lock_cad", lock_cad)

    graph.add_edge(START, "extract_facts")
    graph.add_edge("extract_facts", "gate_facts")
    graph.add_conditional_edges(
        "gate_facts",
        route_after_facts,
        {"run_protocol": "run_protocol", END: END},
    )
    graph.add_edge("run_protocol", "gate_priority")
    graph.add_conditional_edges(
        "gate_priority",
        route_after_priority,
        {
            "extract_facts": "extract_facts",
            "cad_draft": "cad_draft",
            END: END,
        },
    )
    graph.add_edge("cad_draft", "gate_dispatch")
    graph.add_conditional_edges(
        "gate_dispatch",
        route_after_dispatch,
        {"lock_cad": "lock_cad", END: END},
    )
    graph.add_edge("lock_cad", END)
    return graph.compile(checkpointer=checkpointer)


def make_checkpointer(
    db_path: Path | str = DB_PATH,
) -> tuple[SqliteSaver, sqlite3.Connection]:
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), check_same_thread=False)
    return SqliteSaver(conn), conn


def create_app_graph(db_path: Path | str = DB_PATH):
    checkpointer, conn = make_checkpointer(db_path)
    return build_graph(checkpointer), conn
