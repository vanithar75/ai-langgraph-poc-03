"""Sprint 4 eval suite — Supervisor gate, audit trail, incident queue.

Scores the operational-depth capabilities added in Sprint 4 by driving the
graph end-to-end and inspecting resulting state / audit output.
"""

from __future__ import annotations

import os

from app import fixtures

from .harness import Check, Suite, expect, expect_eq
from .scenarios import (
    fresh_graph,
    pending_gate,
    resume,
    start,
    state_of,
    walk_gates,
)


def case_escalated_inserts_supervisor_gate() -> list[Check]:
    graph, _ = fresh_graph()
    result, config = start(graph, "cardiac")  # breathing=no -> escalates
    result = resume(graph, config, "approve", "call_taker")  # facts
    result = resume(graph, config, "approve", "call_taker")  # priority
    st = state_of(graph, config)
    return [
        expect("cardiac escalation flagged", st.get("supervisor_escalate") is True),
        expect_eq("supervisor gate is pending", pending_gate(result), "supervisor"),
        expect("not locked at supervisor gate", st.get("locked") is not True),
    ]


def case_non_escalated_skips_supervisor_gate() -> list[Check]:
    graph, _ = fresh_graph()
    # 'unknown' with weapons=unknown does not meet any escalation condition.
    result, config = start(graph, "unknown")
    result = resume(graph, config, "approve", "call_taker")  # facts
    result = resume(graph, config, "approve", "call_taker")  # priority
    st = state_of(graph, config)
    return [
        expect("no escalation flagged", st.get("supervisor_escalate") is False),
        expect_eq("routes straight to dispatch", pending_gate(result), "dispatch"),
    ]


def case_supervisor_gate_env_fallback() -> list[Check]:
    os.environ["CAD_DISABLE_SUPERVISOR_GATE"] = "1"
    try:
        graph, _ = fresh_graph()
        result, config = start(graph, "cardiac")
        result = resume(graph, config, "approve", "call_taker")  # facts
        result = resume(graph, config, "approve", "call_taker")  # priority
        gate = pending_gate(result)
    finally:
        os.environ.pop("CAD_DISABLE_SUPERVISOR_GATE", None)
    return [
        expect(
            "escalated incident still flags escalate",
            state_of(graph, config).get("supervisor_escalate") is True,
        ),
        expect_eq("supervisor gate skipped when disabled", gate, "dispatch"),
    ]


def case_supervisor_can_send_back() -> list[Check]:
    graph, _ = fresh_graph()
    result, config = start(graph, "cardiac")
    result = resume(graph, config, "approve", "call_taker")  # facts
    result = resume(graph, config, "approve", "call_taker")  # priority -> supervisor
    result = resume(
        graph, config, "revise", "supervisor", feedback="Confirm airway status"
    )
    st = state_of(graph, config)
    return [
        expect_eq("revise returns to facts gate", pending_gate(result), "facts"),
        expect("revision counter incremented", int(st.get("revision_count") or 0) >= 1),
    ]


def case_lock_only_after_dispatch() -> list[Check]:
    graph, _ = fresh_graph()
    result, config = start(graph, "cardiac")
    checks: list[Check] = []
    # Walk to (but not through) the dispatch gate.
    result = walk_gates(graph, config, result, stop_at="dispatch")
    checks.append(expect("not locked before dispatch", state_of(graph, config).get("locked") is not True))
    result = resume(graph, config, "approve", "dispatcher")
    st = state_of(graph, config)
    checks.append(expect("locked after dispatch", st.get("locked") is True))
    checks.append(expect_eq("cad payload LOCKED", st["cad_payload"]["status"], "LOCKED"))
    return checks


def case_audit_log_complete() -> list[Check]:
    graph, _ = fresh_graph()
    result, config = start(graph, "cardiac")
    walk_gates(graph, config, result)  # run all the way to locked
    st = state_of(graph, config)
    audit = fixtures.build_audit(st)
    gates_seen = {a.get("gate") for a in audit["approval_history"]}
    all_ts = all(e.get("ts") for e in audit["timeline"])
    all_appr_meta = all(a.get("ts") and a.get("actor") for a in audit["approval_history"])
    return [
        expect("audit records facts gate", "facts" in gates_seen),
        expect("audit records supervisor gate", "supervisor" in gates_seen),
        expect("audit records dispatch gate", "dispatch" in gates_seen),
        expect("every timeline entry timestamped", all_ts),
        expect("every approval has ts + actor", all_appr_meta),
        expect("total_seconds computed", audit.get("total_seconds") is not None),
        expect("gate_seconds populated", len(audit.get("gate_seconds") or {}) > 0),
        expect_eq("facts_source recorded", audit.get("facts_source"), "demo"),
    ]


def case_multi_incident_isolation() -> list[Check]:
    graph, _ = fresh_graph()
    r1, c1 = start(graph, "cardiac", incident_id="inc-A")
    r2, c2 = start(graph, "unknown", incident_id="inc-B")
    # Advance only incident A past facts.
    resume(graph, c1, "approve", "call_taker")
    a = state_of(graph, c1)
    b = state_of(graph, c2)
    return [
        expect("distinct incident types", a.get("incident_type") != b.get("incident_type")),
        expect("A advanced past facts", a.get("status") != "awaiting_facts_approval"),
        expect("B untouched at facts", b.get("status") == "awaiting_facts_approval"),
    ]


SUITE = Suite(
    name="Sprint 4 — Supervisor gate, audit trail, queue",
    cases=[
        ("Escalated incident inserts supervisor gate", case_escalated_inserts_supervisor_gate),
        ("Non-escalated incident skips supervisor gate", case_non_escalated_skips_supervisor_gate),
        ("Supervisor gate env stop-rule fallback", case_supervisor_gate_env_fallback),
        ("Supervisor can send incident back", case_supervisor_can_send_back),
        ("Lock only after dispatcher gate", case_lock_only_after_dispatch),
        ("Audit log is complete and timed", case_audit_log_complete),
        ("Multiple incidents stay isolated", case_multi_incident_isolation),
    ],
)
