from __future__ import annotations

import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"


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


def extract_from_narrative(
    narrative: str,
    sample_id: str | None = None,
    revision_notes: str = "",
) -> dict[str, Any]:
    """Deterministic demo extractor. Uses canned packs when sample_id matches."""
    sample = get_sample(sample_id) if sample_id else None
    if sample is None:
        # Fuzzy match on keywords for free-text demo mode
        lower = narrative.lower()
        if "fire" in lower or "smoke" in lower or "flames" in lower:
            sample = get_sample("structure_fire")
        elif "crash" in lower or "mvc" in lower or "collision" in lower:
            sample = get_sample("mvc")
        else:
            sample = get_sample("cardiac")

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


def load_protocol(protocol_path: str) -> dict[str, Any]:
    proto = protocols().get(protocol_path) or protocols()["cardiac"]
    return {
        "protocol_name": proto["name"],
        "protocol_disclaimer": proto["disclaimer"],
        "protocol_questions": list(proto["questions"]),
    }


def suggest_priority_and_plan(
    protocol_path: str,
    answers: dict[str, str],
    urgency_cues: list[str],
) -> tuple[str, list[str], list[dict[str, Any]], bool]:
    """Rule-based priority + unit suggestions from mock answers."""
    roster = {u["id"]: u for u in unit_roster()}
    escalate = False
    units: list[dict[str, Any]] = []
    plan: list[str] = []

    def add(unit_id: str, reason: str) -> None:
        if unit_id in roster:
            units.append({**roster[unit_id], "reason": reason})

    if protocol_path == "cardiac":
        priority = "Echo / Priority 1 Medical"
        plan = [
            "Keep caller on line; coach to patient",
            "Confirm address and apartment access",
            "Dispatch ALS; advise AED search if available",
        ]
        add("M11", "ALS for cardiac presentation")
        if answers.get("breathing") == "no" or answers.get("conscious") == "no":
            priority = "Echo / Priority 1 Medical — Critical"
            escalate = True
            plan.append("Supervisor notify: critical medical")
            add("E3", "Engine for first response / CPR support")
    elif protocol_path == "structure_fire":
        priority = "Fire Priority 1 — Structure"
        plan = [
            "Confirm address and occupant status",
            "Stage water supply / attack path",
            "Evacuate exposures if threatened",
        ]
        add("E3", "First-due engine")
        add("L7", "Truck for search/ventilation")
        add("BC1", "Command")
        if answers.get("occupants") == "yes" or answers.get("exposures") == "yes":
            escalate = True
            priority = "Fire Priority 1 — Structure (Possible rescue)"
            plan.append("Possible rescue — expedite assignment")
            add("M11", "EMS staging for occupant care")
    else:  # mvc
        priority = "Priority 2 — MVC"
        plan = [
            "Confirm injury / entrapment",
            "Traffic control and scene safety",
            "Check fuel leak / ignition sources",
        ]
        add("P12", "Scene security / traffic")
        add("M11", "EMS for injuries")
        if answers.get("trapped") == "yes" or answers.get("hazmat") == "yes":
            escalate = True
            priority = "Priority 1 — MVC with entrapment/hazards"
            plan.append("Request extrication / hazmat awareness")
            add("E3", "Extrication / fire protection")
            add("P18", "Additional traffic control")

    if any("critical" in c.lower() or "flames" in c.lower() for c in urgency_cues):
        escalate = escalate or True

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
