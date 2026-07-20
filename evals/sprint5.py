"""Sprint 5 eval suite — config-driven packs, optional LLM, amend/reopen.

Scores the assist-quality and extensibility capabilities added in Sprint 5.
"""

from __future__ import annotations

import os

from app import fixtures

from .harness import Check, Suite, expect, expect_eq
from .scenarios import fresh_graph, resume, start, state_of, walk_gates


def case_config_rules_existing_types() -> list[Check]:
    # cardiac critical answers escalate; calm mvc answers do not.
    pr_c, plan_c, units_c, esc_c = fixtures.suggest_priority_and_plan(
        "cardiac", {"breathing": "no", "conscious": "no"}, []
    )
    pr_m, _plan_m, _units_m, esc_m = fixtures.suggest_priority_and_plan(
        "mvc", {"trapped": "no", "hazmat": "no", "injuries": "yes"}, []
    )
    return [
        expect("cardiac critical escalates", esc_c is True),
        expect("cardiac critical priority set", "Critical" in pr_c),
        expect("cardiac adds engine E3", any(u["id"] == "E3" for u in units_c)),
        expect("calm mvc does not escalate", esc_m is False),
        expect("calm mvc base priority", pr_m == "Priority 2 — MVC"),
    ]


def case_config_only_new_type_overdose() -> list[Check]:
    # 'overdose' exists ONLY via data/*.json (no Python rule code was added).
    graph, _ = fresh_graph()
    result, config = start(graph, "overdose")  # conscious=no, breathing=no
    walk_gates(graph, config, result)
    st = state_of(graph, config)
    return [
        expect_eq("incident type from config", st.get("incident_type"), "Medical — Overdose/Behavioral"),
        expect("overdose escalates on critical answers", st.get("supervisor_escalate") is True),
        expect("overdose priority reflects critical", "Overdose Critical" in (st.get("priority") or "")),
        expect("overdose dispatches ALS M11", any(u["id"] == "M11" for u in st.get("recommended_units") or [])),
        expect("locked end-to-end", st.get("locked") is True),
    ]


def case_config_only_dynamic_pack() -> list[Check]:
    """Inject a brand-new protocol pack at runtime and prove the evaluator
    honors it with zero code changes."""
    orig = fixtures.protocols
    injected = {
        **orig(),
        "flood_rescue": {
            "name": "Injected pack (eval)",
            "disclaimer": "test only",
            "incident_type": "Water Rescue",
            "questions": [],
            "rules": {
                "base_priority": "Priority 2 — Water",
                "base_plan": ["Stage swift-water team"],
                "base_units": [{"id": "E3", "reason": "Rescue"}],
                "escalations": [
                    {
                        "conditions": [{"answer": {"water": "rising"}}],
                        "priority": "Priority 1 — Swift Water",
                        "add_plan": ["Notify supervisor"],
                        "add_units": [{"id": "BC1", "reason": "Command"}],
                    }
                ],
            },
        },
    }
    fixtures.protocols = lambda: injected  # type: ignore[assignment]
    try:
        pr_hi, _p, units_hi, esc_hi = fixtures.suggest_priority_and_plan(
            "flood_rescue", {"water": "rising"}, []
        )
        pr_lo, _p2, units_lo, esc_lo = fixtures.suggest_priority_and_plan(
            "flood_rescue", {"water": "calm"}, []
        )
    finally:
        fixtures.protocols = orig  # type: ignore[assignment]
    return [
        expect_eq("escalated priority from injected pack", pr_hi, "Priority 1 — Swift Water"),
        expect("escalation adds command unit", any(u["id"] == "BC1" for u in units_hi)),
        expect("escalation flagged", esc_hi is True),
        expect_eq("base priority from injected pack", pr_lo, "Priority 2 — Water"),
        expect("no escalation on calm", esc_lo is False),
    ]


def case_llm_fallback_without_key() -> list[Check]:
    saved = os.environ.pop("OPENAI_API_KEY", None)
    try:
        data, source = fixtures.extract_facts_with_mode(
            narrative="cardiac chest pain collapse", sample_id="cardiac", mode="llm"
        )
    finally:
        if saved is not None:
            os.environ["OPENAI_API_KEY"] = saved
    return [
        expect_eq("falls back to demo without key", source, "demo"),
        expect("still returns usable facts", bool(data.get("incident_type"))),
    ]


def case_llm_path_with_mock() -> list[Check]:
    orig = fixtures._llm_extract
    fake = {
        "sample_id": "",
        "protocol_path": "cardiac",
        "location": {"address": "1 Test St", "city": "Harborview", "confidence": "high"},
        "chief_complaint": "LLM-extracted complaint",
        "incident_type": "Medical — Cardiac",
        "people": [],
        "vehicles": [],
        "hazards": [],
        "urgency_cues": [],
        "default_answers": {"breathing": "no"},
    }
    fixtures._llm_extract = lambda narrative, revision_notes="": dict(fake)  # type: ignore[assignment]
    os.environ["OPENAI_API_KEY"] = "test-key-eval"
    try:
        data, source = fixtures.extract_facts_with_mode(
            narrative="anything", mode="llm"
        )
        # Now make the LLM raise and confirm graceful fallback with a key present.
        def boom(narrative, revision_notes=""):
            raise RuntimeError("simulated LLM failure")

        fixtures._llm_extract = boom  # type: ignore[assignment]
        _data2, source2 = fixtures.extract_facts_with_mode(
            narrative="cardiac", sample_id="cardiac", mode="llm"
        )
    finally:
        fixtures._llm_extract = orig  # type: ignore[assignment]
        os.environ.pop("OPENAI_API_KEY", None)
    return [
        expect_eq("uses llm source when available", source, "llm"),
        expect_eq("llm data flows through", data.get("chief_complaint"), "LLM-extracted complaint"),
        expect_eq("falls back to demo on llm error", source2, "demo"),
    ]


def case_amend_preserves_lock_and_versions() -> list[Check]:
    graph, _ = fresh_graph()
    result, config = start(graph, "cardiac")
    walk_gates(graph, config, result)
    st = state_of(graph, config)
    baseline_payload = dict(st["cad_payload"])

    upd1 = fixtures.apply_amendment(
        st, "dispatcher", "Add BLS backup", {"response_plan": st["response_plan"] + ["Add BLS"]}
    )
    graph.update_state(config, upd1)
    st1 = state_of(graph, config)

    upd2 = fixtures.apply_amendment(st1, "dispatcher", "Note road closure")
    graph.update_state(config, upd2)
    st2 = state_of(graph, config)

    amendments = st2.get("amendments") or []
    baseline_snapshot = next((a for a in amendments if a.get("version") == 1), None)
    return [
        expect("stays locked after amend", st2.get("locked") is True),
        expect_eq("cad_version incremented to 3", st2.get("cad_version"), 3),
        expect("baseline snapshot retained as v1", baseline_snapshot is not None),
        expect(
            "baseline snapshot is immutable original",
            bool(baseline_snapshot) and baseline_snapshot["cad_payload"]["status"] == baseline_payload["status"],
        ),
        expect("two addenda recorded", len([a for a in amendments if a.get("role") != "system"]) == 2),
        expect("amendment reason captured", amendments[-1].get("reason") == "Note road closure"),
    ]


def case_eido_stub_labeled() -> list[Check]:
    graph, _ = fresh_graph()
    result, config = start(graph, "cardiac")
    walk_gates(graph, config, result)
    st = state_of(graph, config)
    stub = st["cad_payload"].get("eido_stub") or {}
    return [
        expect("eido stub present", bool(stub)),
        expect("stub carries non-certified disclaimer", "NON-CERTIFIED" in (stub.get("$note") or "")),
        expect("stub has dispatch components", isinstance(stub.get("dispatchComponents"), list)),
    ]


SUITE = Suite(
    name="Sprint 5 — config packs, optional LLM, amend/reopen",
    cases=[
        ("Config rules drive existing types", case_config_rules_existing_types),
        ("Config-only new type (overdose) works end-to-end", case_config_only_new_type_overdose),
        ("Config-only dynamic pack needs no code", case_config_only_dynamic_pack),
        ("LLM mode falls back without key", case_llm_fallback_without_key),
        ("LLM mode uses mock + fails safe", case_llm_path_with_mock),
        ("Amend preserves lock and versions", case_amend_preserves_lock_and_versions),
        ("EIDO/IDX export stub is labeled", case_eido_stub_labeled),
    ],
)
