from __future__ import annotations

from typing import Any, Literal, Optional

from typing_extensions import TypedDict


class CapabilityMapping(TypedDict):
    pain: str
    capability_id: str
    capability_name: str
    talking_point: str


class ScriptSegment(TypedDict):
    minute_start: int
    minute_end: int
    title: str
    narrative: str
    features: list[str]
    success_check: str


class ChecklistItem(TypedDict):
    category: str
    item: str
    owner: Literal["SE", "AE", "Shared"]
    required: bool


class ApprovalEvent(TypedDict, total=False):
    gate: str
    action: str
    role: str
    feedback: str
    note: str


class DemoState(TypedDict, total=False):
    demo_id: str
    account_name: str
    industry: str
    icp: str
    persona: str
    pains: list[str]
    must_win_outcomes: list[str]
    duration_minutes: int
    notes: str
    mode: Literal["demo", "llm"]

    capability_map: list[CapabilityMapping]
    demo_script: list[ScriptSegment]
    environment_checklist: list[ChecklistItem]
    success_criteria: list[str]
    leave_behind_md: str
    export_markdown: str

    status: str
    pending_gate: Optional[str]
    revision_notes: str
    revision_count: int
    approval_history: list[ApprovalEvent]
    locked: bool
    timeline: list[dict[str, Any]]
