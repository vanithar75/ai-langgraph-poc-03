from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any, Literal, Optional

from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from . import fixtures
from .state import ApprovalEvent, DemoState

ROOT = Path(__file__).resolve().parent.parent
DB_PATH = ROOT / ".checkpoints" / "demo_director.db"


def _append_timeline(state: DemoState, step: str, detail: str) -> list[dict[str, Any]]:
    timeline = list(state.get("timeline") or [])
    timeline.append({"step": step, "detail": detail})
    return timeline


def _record_approval(
    state: DemoState, gate: str, decision: dict[str, Any]
) -> list[ApprovalEvent]:
    history = list(state.get("approval_history") or [])
    history.append(
        {
            "gate": gate,
            "action": str(decision.get("action", "approve")),
            "role": str(decision.get("role", "se")),
            "feedback": str(decision.get("feedback") or ""),
            "note": str(decision.get("note") or ""),
        }
    )
    return history


def map_capabilities(state: DemoState) -> dict[str, Any]:
    capability_map = fixtures.map_pains_to_capabilities(
        list(state.get("pains") or []),
        list(state.get("must_win_outcomes") or []),
    )
    return {
        "capability_map": capability_map,
        "status": "awaiting_capability_approval",
        "pending_gate": "capability_map",
        "timeline": _append_timeline(
            state,
            "map_capabilities",
            f"Mapped {len(capability_map)} pains to product capabilities",
        ),
    }


def gate_capability_map(state: DemoState) -> dict[str, Any]:
    decision = interrupt(
        {
            "gate": "capability_map",
            "title": "Gate 1 — Approve pain → capability map",
            "role_hint": "SE reviews; AE may edit talking points",
            "actions": ["approve", "edit", "reject"],
            "payload": {"capability_map": state.get("capability_map") or []},
        }
    )
    action = str(decision.get("action", "approve"))
    history = _record_approval(state, "capability_map", decision)

    if action == "reject":
        return {
            "status": "rejected",
            "pending_gate": None,
            "approval_history": history,
            "timeline": _append_timeline(
                state, "gate_capability_map", "Rejected at Gate 1"
            ),
        }

    capability_map = state.get("capability_map") or []
    edits = decision.get("edits") or {}
    if action == "edit" and edits.get("capability_map") is not None:
        capability_map = edits["capability_map"]

    return {
        "capability_map": capability_map,
        "status": "capability_approved",
        "pending_gate": None,
        "approval_history": history,
        "timeline": _append_timeline(
            state, "gate_capability_map", f"{action.title()} capability map"
        ),
    }


def draft_script(state: DemoState) -> dict[str, Any]:
    revision_notes = state.get("revision_notes") or ""
    demo_script = fixtures.build_demo_script(
        account_name=state.get("account_name") or "Account",
        persona=state.get("persona") or "Buyer",
        duration_minutes=int(state.get("duration_minutes") or 45),
        capability_map=list(state.get("capability_map") or []),
        must_win_outcomes=list(state.get("must_win_outcomes") or []),
        revision_notes=revision_notes,
    )
    checklist = fixtures.build_environment_checklist(
        int(state.get("duration_minutes") or 45)
    )
    success_criteria = fixtures.build_success_criteria(
        list(state.get("must_win_outcomes") or []),
        list(state.get("capability_map") or []),
    )
    detail = "Drafted demo script + checklist"
    if revision_notes:
        detail += f" (revision #{int(state.get('revision_count') or 0)})"

    return {
        "demo_script": demo_script,
        "environment_checklist": checklist,
        "success_criteria": success_criteria,
        "status": "awaiting_script_approval",
        "pending_gate": "script_plan",
        "timeline": _append_timeline(state, "draft_script", detail),
    }


def gate_script_plan(state: DemoState) -> dict[str, Any]:
    decision = interrupt(
        {
            "gate": "script_plan",
            "title": "Gate 2 — SE approves demo path (AE may request revise)",
            "role_hint": "SE: approve/edit · AE: revise with feedback",
            "actions": ["approve", "edit", "revise", "reject"],
            "payload": {
                "demo_script": state.get("demo_script") or [],
                "environment_checklist": state.get("environment_checklist") or [],
                "success_criteria": state.get("success_criteria") or [],
            },
        }
    )
    action = str(decision.get("action", "approve"))
    role = str(decision.get("role") or "se")
    history = _record_approval(state, "script_plan", decision)

    if action == "reject":
        return {
            "status": "rejected",
            "pending_gate": None,
            "approval_history": history,
            "timeline": _append_timeline(
                state, "gate_script_plan", "Rejected at Gate 2"
            ),
        }

    if action == "revise":
        feedback = str(decision.get("feedback") or "Please tighten the demo path.")
        return {
            "status": "revising_script",
            "pending_gate": None,
            "revision_notes": feedback,
            "revision_count": int(state.get("revision_count") or 0) + 1,
            "approval_history": history,
            "timeline": _append_timeline(
                state,
                "gate_script_plan",
                f"AE/SE revise requested by {role}: {feedback[:120]}",
            ),
        }

    demo_script = state.get("demo_script") or []
    checklist = state.get("environment_checklist") or []
    success_criteria = state.get("success_criteria") or []
    edits = decision.get("edits") or {}
    if action == "edit":
        if edits.get("demo_script") is not None:
            demo_script = edits["demo_script"]
        if edits.get("environment_checklist") is not None:
            checklist = edits["environment_checklist"]
        if edits.get("success_criteria") is not None:
            success_criteria = edits["success_criteria"]

    return {
        "demo_script": demo_script,
        "environment_checklist": checklist,
        "success_criteria": success_criteria,
        "status": "script_approved",
        "pending_gate": None,
        "revision_notes": "",
        "approval_history": history,
        "timeline": _append_timeline(
            state, "gate_script_plan", f"{role.upper()} {action}d script plan"
        ),
    }


def route_after_script_gate(
    state: DemoState,
) -> Literal["draft_script", "draft_leavebehind", "__end__"]:
    status = state.get("status")
    if status == "rejected":
        return END
    if status == "revising_script":
        return "draft_script"
    return "draft_leavebehind"


def draft_leavebehind(state: DemoState) -> dict[str, Any]:
    leave_behind = fixtures.build_leave_behind(
        account_name=state.get("account_name") or "Account",
        persona=state.get("persona") or "Buyer",
        capability_map=list(state.get("capability_map") or []),
        success_criteria=list(state.get("success_criteria") or []),
        must_win_outcomes=list(state.get("must_win_outcomes") or []),
    )
    return {
        "leave_behind_md": leave_behind,
        "status": "awaiting_leavebehind_approval",
        "pending_gate": "leave_behind",
        "timeline": _append_timeline(
            state, "draft_leavebehind", "Drafted leave-behind one-pager + CTA"
        ),
    }


def gate_leave_behind(state: DemoState) -> dict[str, Any]:
    decision = interrupt(
        {
            "gate": "leave_behind",
            "title": "Gate 3 — Lock leave-behind before demo-day artifacts finalize",
            "role_hint": "SE final approval required to lock",
            "actions": ["approve", "edit", "reject"],
            "payload": {"leave_behind_md": state.get("leave_behind_md") or ""},
        }
    )
    action = str(decision.get("action", "approve"))
    history = _record_approval(state, "leave_behind", decision)

    if action == "reject":
        return {
            "status": "rejected",
            "pending_gate": None,
            "locked": False,
            "approval_history": history,
            "timeline": _append_timeline(
                state, "gate_leave_behind", "Rejected at Gate 3"
            ),
        }

    leave_behind = state.get("leave_behind_md") or ""
    edits = decision.get("edits") or {}
    if action == "edit" and edits.get("leave_behind_md") is not None:
        leave_behind = str(edits["leave_behind_md"])

    return {
        "leave_behind_md": leave_behind,
        "status": "leavebehind_approved",
        "pending_gate": None,
        "approval_history": history,
        "timeline": _append_timeline(
            state, "gate_leave_behind", f"{action.title()} leave-behind"
        ),
    }


def export_artifacts(state: DemoState) -> dict[str, Any]:
    export_md = fixtures.build_export_markdown({**state, "locked": True})
    return {
        "export_markdown": export_md,
        "locked": True,
        "status": "locked",
        "pending_gate": None,
        "timeline": _append_timeline(
            state, "export_artifacts", "Locked demo-day package + markdown export"
        ),
    }


def route_after_capability(
    state: DemoState,
) -> Literal["draft_script", "__end__"]:
    if state.get("status") == "rejected":
        return END
    return "draft_script"


def route_after_leavebehind(
    state: DemoState,
) -> Literal["export_artifacts", "__end__"]:
    if state.get("status") == "rejected":
        return END
    return "export_artifacts"


def build_graph(checkpointer: Optional[SqliteSaver] = None):
    graph = StateGraph(DemoState)
    graph.add_node("map_capabilities", map_capabilities)
    graph.add_node("gate_capability_map", gate_capability_map)
    graph.add_node("draft_script", draft_script)
    graph.add_node("gate_script_plan", gate_script_plan)
    graph.add_node("draft_leavebehind", draft_leavebehind)
    graph.add_node("gate_leave_behind", gate_leave_behind)
    graph.add_node("export_artifacts", export_artifacts)

    graph.add_edge(START, "map_capabilities")
    graph.add_edge("map_capabilities", "gate_capability_map")
    graph.add_conditional_edges(
        "gate_capability_map",
        route_after_capability,
        {"draft_script": "draft_script", END: END},
    )
    graph.add_edge("draft_script", "gate_script_plan")
    graph.add_conditional_edges(
        "gate_script_plan",
        route_after_script_gate,
        {
            "draft_script": "draft_script",
            "draft_leavebehind": "draft_leavebehind",
            END: END,
        },
    )
    graph.add_edge("draft_leavebehind", "gate_leave_behind")
    graph.add_conditional_edges(
        "gate_leave_behind",
        route_after_leavebehind,
        {"export_artifacts": "export_artifacts", END: END},
    )
    graph.add_edge("export_artifacts", END)

    return graph.compile(checkpointer=checkpointer)


def make_checkpointer(db_path: Path | str = DB_PATH) -> tuple[SqliteSaver, sqlite3.Connection]:
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), check_same_thread=False)
    return SqliteSaver(conn), conn


def create_app_graph(db_path: Path | str = DB_PATH):
    checkpointer, conn = make_checkpointer(db_path)
    return build_graph(checkpointer), conn
