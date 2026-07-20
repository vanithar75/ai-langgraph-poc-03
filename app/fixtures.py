from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_json(name: str) -> Any:
    return json.loads((DATA / name).read_text(encoding="utf-8"))


def sample_incidents() -> list[dict[str, Any]]:
    return load_json("incidents.json")


def protocols() -> dict[str, Any]:
    return load_json("protocols.json")


def unit_roster() -> list[dict[str, Any]]:
    return load_json("units.json")


def get_sample(sample_id: str) -> dict[str, Any] | None:
    return next((s for s in sample_incidents() if s["id"] == sample_id), None)


def _match_sample_by_keywords(narrative: str) -> dict[str, Any]:
    """Data-driven free-text routing: each incident pack may declare `keywords`."""
    lower = narrative.lower()
    best: tuple[int, dict[str, Any] | None] = (0, None)
    fallback: dict[str, Any] | None = None
    for sample in sample_incidents():
        if sample.get("default_route"):
            fallback = sample
        hits = sum(1 for kw in sample.get("keywords") or [] if kw.lower() in lower)
        if hits > best[0]:
            best = (hits, sample)
    if best[1] is not None:
        return best[1]
    return fallback or get_sample("cardiac") or sample_incidents()[0]


def extract_from_narrative(
    narrative: str,
    sample_id: str | None = None,
    revision_notes: str = "",
) -> dict[str, Any]:
    """Deterministic demo extractor. Uses canned packs when sample_id matches."""
    sample = get_sample(sample_id) if sample_id else None
    if sample is None:
        sample = _match_sample_by_keywords(narrative)

    assert sample is not None
    extract = dict(sample["extract"])
    location = dict(extract["location"])
    if revision_notes:
        location["notes"] = (
            f"{location.get('notes', '')} | Revision: {revision_notes}"
        ).strip(" |")
        extract["urgency_cues"] = list(extract.get("urgency_cues") or []) + [
            f"revision:{revision_notes[:80]}"
        ]
    return {
        "sample_id": sample["id"],
        "protocol_path": sample["protocol_path"],
        "location": location,
        "chief_complaint": extract["chief_complaint"],
        "incident_type": extract["incident_type"],
        "people": list(extract.get("people") or []),
        "vehicles": list(extract.get("vehicles") or []),
        "hazards": list(extract.get("hazards") or []),
        "urgency_cues": list(extract.get("urgency_cues") or []),
        "default_answers": dict(sample.get("default_answers") or {}),
    }


def llm_available() -> bool:
    return bool(os.getenv("OPENAI_API_KEY"))


def _llm_extract(narrative: str, revision_notes: str = "") -> dict[str, Any]:
    """Optional LLM extractor (Sprint 5).

    Produces the same schema as the deterministic extractor. Raises on any
    failure so the caller can fall back to demo fixtures. The prompt asks the
    model to route to one of the known protocol paths so downstream rule packs
    keep working. Never called unless OPENAI_API_KEY is set and mode=='llm'.
    """
    from langchain_core.messages import HumanMessage, SystemMessage
    from langchain_openai import ChatOpenAI

    known_paths = list(protocols().keys())
    schema_hint = {
        "protocol_path": f"one of {known_paths}",
        "location": {"address": "", "city": "", "confidence": "high|medium|low", "notes": ""},
        "chief_complaint": "",
        "incident_type": "",
        "people": [],
        "vehicles": [],
        "hazards": [],
        "urgency_cues": [],
        "default_answers": {},
    }
    model = ChatOpenAI(model=os.getenv("CAD_LLM_MODEL", "gpt-4o-mini"), temperature=0)
    messages = [
        SystemMessage(
            content=(
                "You extract structured call-take facts for a TRAINING/DEMO CAD assist. "
                "Return ONLY minified JSON matching the provided schema. "
                f"protocol_path MUST be one of {known_paths}."
            )
        ),
        HumanMessage(
            content=(
                f"Schema: {json.dumps(schema_hint)}\n"
                f"Revision notes (optional): {revision_notes}\n"
                f"Caller narrative: {narrative}"
            )
        ),
    ]
    raw = model.invoke(messages).content
    if isinstance(raw, list):
        raw = "".join(str(p) for p in raw)
    data = json.loads(raw)
    path = data.get("protocol_path")
    if path not in known_paths:
        data["protocol_path"] = _match_sample_by_keywords(narrative)["protocol_path"]
    data.setdefault("sample_id", "")
    for key in ("people", "vehicles", "hazards", "urgency_cues"):
        data.setdefault(key, [])
    data.setdefault("default_answers", {})
    data.setdefault("location", {})
    data.setdefault("chief_complaint", "")
    data.setdefault("incident_type", data["protocol_path"])
    return data


def extract_facts_with_mode(
    narrative: str,
    sample_id: str | None = None,
    revision_notes: str = "",
    mode: str = "demo",
) -> tuple[dict[str, Any], str]:
    """Dispatch extractor by mode, with graceful fallback to deterministic demo.

    Returns (extracted, source) where source is 'demo' or 'llm'. The LLM path is
    only attempted when mode=='llm' AND a key is configured; any error falls back
    to the deterministic extractor so the app always works offline.
    """
    if mode == "llm" and llm_available():
        try:
            return _llm_extract(narrative, revision_notes), "llm"
        except Exception:
            # Fall through to deterministic extractor — demo must never break.
            pass
    return (
        extract_from_narrative(narrative, sample_id, revision_notes),
        "demo",
    )


def load_protocol(protocol_path: str) -> dict[str, Any]:
    proto = protocols().get(protocol_path) or protocols()["cardiac"]
    return {
        "protocol_name": proto["name"],
        "protocol_disclaimer": proto["disclaimer"],
        "protocol_questions": list(proto["questions"]),
    }


def _condition_matches(condition: dict[str, Any], answers: dict[str, str]) -> bool:
    """A rule condition matches when every declared answer key/value is present.

    `answer` maps question id -> a value or list of accepted values.
    """
    for qid, expected in (condition.get("answer") or {}).items():
        accepted = expected if isinstance(expected, list) else [expected]
        if answers.get(qid) not in accepted:
            return False
    return True


def suggest_priority_and_plan(
    protocol_path: str,
    answers: dict[str, str],
    urgency_cues: list[str],
) -> tuple[str, list[str], list[dict[str, Any]], bool]:
    """Config-driven priority + unit suggestions.

    Rules live in ``data/protocols.json`` under each protocol's ``rules`` key, so
    new incident types can be added with zero code changes. Falls back to a safe
    default if a pack has no rules block.
    """
    roster = {u["id"]: u for u in unit_roster()}
    proto = protocols().get(protocol_path) or {}
    rules = proto.get("rules") or {}

    def resolve_units(specs: list[dict[str, Any]]) -> list[dict[str, Any]]:
        resolved: list[dict[str, Any]] = []
        seen: set[str] = set()
        for spec in specs or []:
            uid = spec.get("id")
            if uid in roster and uid not in seen:
                resolved.append({**roster[uid], "reason": spec.get("reason", "")})
                seen.add(uid)
        return resolved

    priority = rules.get("base_priority", "Priority 2 — Standard")
    plan = list(rules.get("base_plan") or [])
    unit_specs = list(rules.get("base_units") or [])
    escalate = False

    for esc in rules.get("escalations") or []:
        conditions = esc.get("conditions") or []
        if conditions and any(_condition_matches(c, answers) for c in conditions):
            escalate = True
            if esc.get("priority"):
                priority = esc["priority"]
            plan.extend(esc.get("add_plan") or [])
            unit_specs.extend(esc.get("add_units") or [])

    units = resolve_units(unit_specs)

    if any("critical" in c.lower() or "flames" in c.lower() for c in urgency_cues):
        escalate = True

    return priority, plan, units, escalate


def build_cad_draft(state: dict[str, Any]) -> dict[str, Any]:
    number = state.get("cad_incident_number") or f"HV-{str(state.get('incident_id', 'X'))[:8].upper()}"
    loc = state.get("location") or {}
    units = state.get("recommended_units") or []
    narrative = (
        f"{state.get('incident_type', 'Incident')} at {loc.get('address', 'unknown location')}. "
        f"Chief complaint: {state.get('chief_complaint', '')}. "
        f"Priority: {state.get('priority', '')}. "
        f"Units proposed: {', '.join(u.get('id', '') for u in units)}. "
        f"Hazards: {', '.join(state.get('hazards') or []) or 'none noted'}."
    )
    payload = {
        "incident_number": number,
        "status": "DRAFT_PENDING_DISPATCHER",
        "cad_version": int(state.get("cad_version") or 1),
        "incident_type": state.get("incident_type"),
        "chief_complaint": state.get("chief_complaint"),
        "location": loc,
        "priority": state.get("priority"),
        "response_plan": state.get("response_plan") or [],
        "protocol_path": state.get("protocol_path"),
        "protocol_answers": state.get("protocol_answers") or {},
        "people": state.get("people") or [],
        "vehicles": state.get("vehicles") or [],
        "hazards": state.get("hazards") or [],
        "recommended_units": units,
        "supervisor_escalate": bool(state.get("supervisor_escalate")),
        "narrative": narrative,
        "eido_stub": build_eido_stub(state, number, units),
        "psers_tags": state.get("psers_tags")
        or [
            "PSERS.PLAT.NG911",
            "PSERS.PLAT.CAD.INCIDENT_CREATE",
            "PSERS.PLAT.CAD.UNIT_RECOMMEND",
        ],
        "disclaimer": "Training/demo CAD draft only. Not operational PSAP software.",
    }
    return {
        "cad_incident_number": number,
        "cad_narrative": narrative,
        "cad_payload": payload,
    }


def build_eido_stub(
    state: dict[str, Any], number: str, units: list[dict[str, Any]]
) -> dict[str, Any]:
    """NON-CERTIFIED, presales-storytelling stub loosely shaped after NENA EIDO/IDX.

    This is intentionally a minimal, clearly-labeled stub. It is NOT a conformant
    EIDO document and must not be treated as interoperable production output.
    """
    loc = state.get("location") or {}
    return {
        "$note": "NON-CERTIFIED EIDO/IDX-style stub — training/demo only, not conformant.",
        "eidoVersion": "stub-0",
        "incidentId": number,
        "incidentType": state.get("incident_type"),
        "priority": state.get("priority"),
        "location": {
            "civicAddressText": loc.get("address"),
            "city": loc.get("city"),
            "confidence": loc.get("confidence"),
        },
        "dispatchComponents": [
            {"resourceId": u.get("id"), "resourceType": u.get("type"), "agency": u.get("agency")}
            for u in units
        ],
    }


def build_audit(state: dict[str, Any]) -> dict[str, Any]:
    """Structured, ordered audit view with timing derived from timestamps."""
    timeline = state.get("timeline") or []
    created_at = state.get("created_at")
    total_seconds = None
    gate_seconds: dict[str, float] = {}
    parsed = []
    for entry in timeline:
        ts = entry.get("ts")
        try:
            parsed.append((entry, datetime.fromisoformat(ts) if ts else None))
        except ValueError:
            parsed.append((entry, None))
    prev_dt = None
    if created_at:
        try:
            prev_dt = datetime.fromisoformat(created_at)
        except ValueError:
            prev_dt = None
    for entry, dt in parsed:
        if dt and prev_dt:
            gate_seconds[entry.get("step", "?")] = round(
                (dt - prev_dt).total_seconds(), 3
            )
        if dt:
            prev_dt = dt
    stamps = [dt for _, dt in parsed if dt]
    if stamps and created_at and prev_dt:
        try:
            start_dt = datetime.fromisoformat(created_at)
            total_seconds = round((stamps[-1] - start_dt).total_seconds(), 3)
        except ValueError:
            total_seconds = None
    return {
        "incident_id": state.get("incident_id"),
        "created_at": created_at,
        "status": state.get("status"),
        "locked": bool(state.get("locked")),
        "cad_version": int(state.get("cad_version") or 1),
        "facts_source": state.get("facts_source"),
        "approval_history": state.get("approval_history") or [],
        "timeline": timeline,
        "amendments": state.get("amendments") or [],
        "gate_seconds": gate_seconds,
        "total_seconds": total_seconds,
    }


def apply_amendment(
    state: dict[str, Any], role: str, reason: str, edits: dict[str, Any] | None = None
) -> dict[str, Any]:
    """Sprint 5: controlled post-lock addendum.

    Appends an immutable, versioned amendment WITHOUT clearing the lock. The
    original locked baseline (version 1) is preserved inside ``amendments``.
    Returns the state updates to persist via ``graph.update_state``.
    """
    edits = edits or {}
    prev_version = int(state.get("cad_version") or 1)
    new_version = prev_version + 1
    baseline = dict(state.get("cad_payload") or {})

    amendments = list(state.get("amendments") or [])
    if not amendments:
        # Snapshot the locked baseline as version 1 the first time we amend.
        amendments.append(
            {
                "version": prev_version,
                "role": "system",
                "reason": "Locked baseline snapshot",
                "ts": _now(),
                "cad_payload": baseline,
            }
        )

    merged_state = {**state, **edits, "cad_version": new_version}
    draft = build_cad_draft(merged_state)
    new_payload = dict(draft["cad_payload"])
    new_payload["status"] = "LOCKED"
    new_payload["locked"] = True
    new_payload["cad_version"] = new_version

    amendments.append(
        {
            "version": new_version,
            "role": role,
            "reason": reason,
            "ts": _now(),
            "cad_payload": new_payload,
        }
    )

    timeline = list(state.get("timeline") or [])
    timeline.append(
        {
            "step": "amend_cad",
            "detail": f"{role} added addendum v{new_version}: {reason[:80]}",
            "ts": _now(),
            "psers": "PSERS.PLAT.CAD.INCIDENT_UPDATE",
        }
    )
    history = list(state.get("approval_history") or [])
    history.append(
        {
            "gate": "amend",
            "action": "amend",
            "role": role,
            "actor": role,
            "feedback": reason,
            "note": f"v{new_version}",
            "ts": _now(),
        }
    )

    return {
        "cad_version": new_version,
        "cad_payload": new_payload,
        "cad_narrative": new_payload.get("narrative", state.get("cad_narrative")),
        "amendments": amendments,
        "timeline": timeline,
        "approval_history": history,
        "locked": True,
        "status": "locked",
    }


def build_export_markdown(state: dict[str, Any]) -> str:
    loc = state.get("location") or {}
    lines = [
        f"# CAD Assist Summary — {state.get('cad_incident_number', 'PENDING')}",
        "",
        f"**Status:** {'LOCKED' if state.get('locked') else state.get('status', '')}",
        f"**Type:** {state.get('incident_type', '')}",
        f"**Priority:** {state.get('priority', '')}",
        f"**Location:** {loc.get('address', '')}, {loc.get('city', '')}",
        f"**Chief complaint:** {state.get('chief_complaint', '')}",
        "",
        "## Caller narrative",
        "",
        state.get("narrative") or "",
        "",
        "## Protocol",
        "",
        f"{state.get('protocol_name', '')}",
        f"_{state.get('protocol_disclaimer', '')}_",
        "",
    ]
    answers = state.get("protocol_answers") or {}
    for q in state.get("protocol_questions") or []:
        lines.append(f"- {q.get('prompt')}: **{answers.get(q.get('id'), '—')}**")
    lines.extend(["", "## Response plan", ""])
    lines.extend(f"- {p}" for p in state.get("response_plan") or [])
    lines.extend(["", "## Recommended units", ""])
    for u in state.get("recommended_units") or []:
        lines.append(
            f"- {u.get('id')} ({u.get('type')}, {u.get('agency')}) — {u.get('reason', '')}"
        )
    audit = build_audit(state)
    if audit.get("total_seconds") is not None:
        lines.extend(
            [
                "",
                "## Call-take timing",
                "",
                f"- Created: {audit.get('created_at', '')}",
                f"- Total elapsed: {audit.get('total_seconds')}s",
                f"- Facts source: {audit.get('facts_source', 'demo')}",
            ]
        )
    amendments = [a for a in (state.get("amendments") or []) if a.get("role") != "system"]
    if amendments:
        lines.extend(["", "## Amendments (post-lock addenda)", ""])
        for a in amendments:
            lines.append(
                f"- v{a.get('version')} by {a.get('role')} @ {a.get('ts')}: {a.get('reason', '')}"
            )
    lines.extend(
        [
            "",
            "## CAD payload",
            "",
            "```json",
            json.dumps(state.get("cad_payload") or {}, indent=2),
            "```",
            "",
            "_Training/demo assist only. Humans own all life-safety decisions._",
            "",
        ]
    )
    return "\n".join(lines)
