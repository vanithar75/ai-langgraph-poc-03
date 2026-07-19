from __future__ import annotations

from typing import Any, Literal, Optional

from typing_extensions import TypedDict


class LocationInfo(TypedDict, total=False):
    address: str
    city: str
    confidence: str
    notes: str


class ApprovalEvent(TypedDict, total=False):
    gate: str
    action: str
    role: str
    feedback: str
    note: str


class UnitSuggestion(TypedDict, total=False):
    id: str
    type: str
    agency: str
    status: str
    station: str
    reason: str


class IncidentState(TypedDict, total=False):
    incident_id: str
    sample_id: str
    narrative: str
    protocol_path: str
    mode: Literal["demo", "llm"]

    location: LocationInfo
    chief_complaint: str
    incident_type: str
    people: list[str]
    vehicles: list[str]
    hazards: list[str]
    urgency_cues: list[str]

    protocol_name: str
    protocol_questions: list[dict[str, Any]]
    protocol_answers: dict[str, str]
    protocol_disclaimer: str

    priority: str
    response_plan: list[str]
    recommended_units: list[UnitSuggestion]
    supervisor_escalate: bool
    revision_notes: str
    revision_count: int

    cad_incident_number: str
    cad_narrative: str
    cad_payload: dict[str, Any]
    export_markdown: str

    status: str
    pending_gate: Optional[str]
    approval_history: list[ApprovalEvent]
    timeline: list[dict[str, Any]]
    locked: bool
    psers_tags: list[str]
