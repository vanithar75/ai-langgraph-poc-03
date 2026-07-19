from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .state import CapabilityMapping, ChecklistItem, ScriptSegment

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"


def load_json(name: str) -> Any:
    return json.loads((DATA / name).read_text(encoding="utf-8"))


def sample_accounts() -> list[dict[str, Any]]:
    return load_json("sample_accounts.json")


def product_catalog() -> dict[str, Any]:
    return load_json("product_capabilities.json")


def map_pains_to_capabilities(
    pains: list[str], must_win_outcomes: list[str]
) -> list[CapabilityMapping]:
    catalog = product_catalog()["capabilities"]
    mappings: list[CapabilityMapping] = []
    used: set[str] = set()

    for pain in pains:
        match = next(
            (c for c in catalog if pain in c["pain_tags"] and c["id"] not in used),
            None,
        )
        if match is None:
            match = next((c for c in catalog if c["id"] not in used), catalog[0])
        used.add(match["id"])
        outcome_hint = must_win_outcomes[0] if must_win_outcomes else "the must-win outcome"
        mappings.append(
            {
                "pain": pain.replace("_", " "),
                "capability_id": match["id"],
                "capability_name": match["name"],
                "talking_point": (
                    f"Show {match['name']} to address {pain.replace('_', ' ')} "
                    f"and prove progress toward: {outcome_hint}."
                ),
            }
        )
    return mappings


def build_demo_script(
    *,
    account_name: str,
    persona: str,
    duration_minutes: int,
    capability_map: list[CapabilityMapping],
    must_win_outcomes: list[str],
    revision_notes: str = "",
) -> list[ScriptSegment]:
    caps = capability_map or map_pains_to_capabilities([], must_win_outcomes)
    slots = max(3, min(5, len(caps) + 1))
    # Reserve open + close; fill middle with capabilities.
    open_mins = 5
    close_mins = 8 if duration_minutes >= 45 else 5
    body_mins = max(duration_minutes - open_mins - close_mins, slots * 5)
    segment_len = max(5, body_mins // max(len(caps), 1))

    segments: list[ScriptSegment] = [
        {
            "minute_start": 0,
            "minute_end": open_mins,
            "title": "Frame the must-win outcomes",
            "narrative": (
                f"Open with {persona} at {account_name}. Confirm outcomes: "
                + "; ".join(must_win_outcomes[:3])
                + "."
            ),
            "features": ["Success Criteria Tracker"],
            "success_check": "Buyer restates 1–2 must-win outcomes in their own words.",
        }
    ]

    cursor = open_mins
    for i, mapping in enumerate(caps):
        end = cursor + segment_len
        if i == len(caps) - 1:
            end = duration_minutes - close_mins
        segments.append(
            {
                "minute_start": cursor,
                "minute_end": end,
                "title": f"Path: {mapping['capability_name']}",
                "narrative": mapping["talking_point"]
                + (
                    f" Incorporate AE revision: {revision_notes}"
                    if revision_notes and i == 0
                    else ""
                ),
                "features": [mapping["capability_name"]],
                "success_check": (
                    f"Stakeholder agrees {mapping['capability_name']} "
                    f"addresses {mapping['pain']}."
                ),
            }
        )
        cursor = end

    segments.append(
        {
            "minute_start": duration_minutes - close_mins,
            "minute_end": duration_minutes,
            "title": "Lock success criteria + next step",
            "narrative": (
                "Recap proof points, capture open questions, and propose a technical "
                "validation workshop with named owners and dates."
            ),
            "features": ["Success Criteria Tracker", "Meeting Memory"],
            "success_check": "Mutual next step and owner confirmed before leaving the room.",
        }
    )
    return segments


def build_environment_checklist(duration_minutes: int) -> list[ChecklistItem]:
    return [
        {
            "category": "Environment",
            "item": "Seed demo tenant with account-named sample deals",
            "owner": "SE",
            "required": True,
        },
        {
            "category": "Environment",
            "item": "Verify SSO-free presenter login + backup local session",
            "owner": "SE",
            "required": True,
        },
        {
            "category": "Narrative",
            "item": f"Timebox path for {duration_minutes}-minute agenda with buffer",
            "owner": "Shared",
            "required": True,
        },
        {
            "category": "Narrative",
            "item": "AE owns business framing; SE owns product path",
            "owner": "AE",
            "required": True,
        },
        {
            "category": "Proof",
            "item": "Success criteria checklist projected in final 8 minutes",
            "owner": "SE",
            "required": True,
        },
        {
            "category": "Backup",
            "item": "Offline screenshots for top 3 capability moments",
            "owner": "SE",
            "required": False,
        },
    ]


def build_success_criteria(
    must_win_outcomes: list[str], capability_map: list[CapabilityMapping]
) -> list[str]:
    criteria = [f"Buyer confirms: {o}" for o in must_win_outcomes]
    for mapping in capability_map[:3]:
        criteria.append(
            f"Live proof that {mapping['capability_name']} addresses {mapping['pain']}"
        )
    criteria.append("Named next step, owner, and date captured before close")
    return criteria


def build_leave_behind(
    *,
    account_name: str,
    persona: str,
    capability_map: list[CapabilityMapping],
    success_criteria: list[str],
    must_win_outcomes: list[str],
) -> str:
    product = product_catalog()["product_name"]
    lines = [
        f"# {product} Demo Leave-Behind — {account_name}",
        "",
        f"**Audience:** {persona}",
        "",
        "## Must-win outcomes",
    ]
    lines.extend(f"- {o}" for o in must_win_outcomes)
    lines.extend(["", "## Capability path we showed", ""])
    for m in capability_map:
        lines.append(f"- **{m['capability_name']}** → {m['pain']}: {m['talking_point']}")
    lines.extend(["", "## Success criteria checklist", ""])
    lines.extend(f"- [ ] {c}" for c in success_criteria)
    lines.extend(
        [
            "",
            "## Recommended next step",
            "",
            f"Book a technical validation workshop for {account_name} within 5 business days.",
            "CTA: reply with attendees + preferred window; SE will send environment prep sheet.",
            "",
        ]
    )
    return "\n".join(lines)


def build_export_markdown(state: dict[str, Any]) -> str:
    parts = [
        f"# Demo Director Plan — {state.get('account_name', 'Account')}",
        "",
        f"- Persona: {state.get('persona', '')}",
        f"- ICP: {state.get('icp', '')}",
        f"- Duration: {state.get('duration_minutes', 45)} minutes",
        f"- Status: {'LOCKED' if state.get('locked') else state.get('status', '')}",
        "",
        "## Capability map",
        "",
    ]
    for m in state.get("capability_map") or []:
        parts.append(
            f"- {m['pain']} → **{m['capability_name']}**: {m['talking_point']}"
        )

    parts.extend(["", "## Demo script", ""])
    for seg in state.get("demo_script") or []:
        parts.append(
            f"### {seg['minute_start']}–{seg['minute_end']} min — {seg['title']}"
        )
        parts.append(seg["narrative"])
        parts.append(f"- Features: {', '.join(seg.get('features') or [])}")
        parts.append(f"- Success check: {seg['success_check']}")
        parts.append("")

    parts.extend(["## Environment checklist", ""])
    for item in state.get("environment_checklist") or []:
        req = "required" if item.get("required") else "optional"
        parts.append(
            f"- [{req}] ({item['owner']}) {item['category']}: {item['item']}"
        )

    parts.extend(["", "## Leave-behind", "", state.get("leave_behind_md") or ""])
    return "\n".join(parts)
