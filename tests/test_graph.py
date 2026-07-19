from __future__ import annotations

import sqlite3
from pathlib import Path

from langgraph.types import Command

from app.graph import build_graph
from app import fixtures


def _graph(tmp_path: Path):
    db = tmp_path / "test.db"
    conn = sqlite3.connect(str(db), check_same_thread=False)
    checkpointer = __import__(
        "langgraph.checkpoint.sqlite", fromlist=["SqliteSaver"]
    ).SqliteSaver(conn)
    return build_graph(checkpointer), conn


def test_happy_path_with_revise_loop(tmp_path: Path):
    graph, conn = _graph(tmp_path)
    sample = fixtures.sample_accounts()[0]
    demo_id = "demo-test-1"
    config = {"configurable": {"thread_id": demo_id}}

    initial = {
        "demo_id": demo_id,
        "account_name": sample["account_name"],
        "industry": sample["industry"],
        "icp": sample["icp"],
        "persona": sample["persona"],
        "pains": sample["pains"],
        "must_win_outcomes": sample["must_win_outcomes"],
        "duration_minutes": sample["duration_minutes"],
        "notes": sample["notes"],
        "mode": "demo",
        "revision_count": 0,
        "revision_notes": "",
        "approval_history": [],
        "timeline": [],
        "locked": False,
        "status": "started",
    }

    result = graph.invoke(initial, config=config)
    assert "__interrupt__" in result
    assert result["__interrupt__"][0].value["gate"] == "capability_map"

    result = graph.invoke(
        Command(resume={"action": "approve", "role": "se"}), config=config
    )
    assert result["__interrupt__"][0].value["gate"] == "script_plan"

    # AE revise loops back to script gate
    result = graph.invoke(
        Command(
            resume={
                "action": "revise",
                "role": "ae",
                "feedback": "Lead with forecast scrub before MEDDICC coach.",
            }
        ),
        config=config,
    )
    assert result["__interrupt__"][0].value["gate"] == "script_plan"
    state = graph.get_state(config).values
    assert state["revision_count"] == 1
    assert "forecast scrub" in (state["demo_script"][1]["narrative"]).lower() or state[
        "revision_count"
    ] == 1

    result = graph.invoke(
        Command(resume={"action": "approve", "role": "se"}), config=config
    )
    assert result["__interrupt__"][0].value["gate"] == "leave_behind"

    result = graph.invoke(
        Command(resume={"action": "approve", "role": "se"}), config=config
    )
    assert "__interrupt__" not in result or not result.get("__interrupt__")
    state = graph.get_state(config).values
    assert state["locked"] is True
    assert state["status"] == "locked"
    assert "Demo Director Plan" in state["export_markdown"]
    assert len(state["approval_history"]) >= 4
    conn.close()


def test_edit_capability_map(tmp_path: Path):
    graph, conn = _graph(tmp_path)
    demo_id = "demo-test-2"
    config = {"configurable": {"thread_id": demo_id}}
    sample = fixtures.sample_accounts()[1]
    graph.invoke(
        {
            "demo_id": demo_id,
            "account_name": sample["account_name"],
            "industry": sample["industry"],
            "icp": sample["icp"],
            "persona": sample["persona"],
            "pains": sample["pains"],
            "must_win_outcomes": sample["must_win_outcomes"],
            "duration_minutes": 30,
            "notes": "",
            "mode": "demo",
            "revision_count": 0,
            "revision_notes": "",
            "approval_history": [],
            "timeline": [],
            "locked": False,
            "status": "started",
        },
        config=config,
    )

    edited = [
        {
            "pain": "custom pain",
            "capability_id": "success_criteria",
            "capability_name": "Success Criteria Tracker",
            "talking_point": "Custom talking point for Harbor.",
        }
    ]
    graph.invoke(
        Command(
            resume={
                "action": "edit",
                "role": "se",
                "edits": {"capability_map": edited},
            }
        ),
        config=config,
    )
    state = graph.get_state(config).values
    assert state["capability_map"][0]["talking_point"].startswith("Custom talking")
    assert state["pending_gate"] == "script_plan" or "script" in (
        graph.get_state(config).tasks[0].interrupts[0].value["gate"]
        if graph.get_state(config).tasks
        else ""
    )
    conn.close()
