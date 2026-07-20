from __future__ import annotations

import sqlite3
from pathlib import Path

from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.types import Command

from app import fixtures
from app.graph import build_graph


def _graph(tmp_path: Path):
    conn = sqlite3.connect(str(tmp_path / "t.db"), check_same_thread=False)
    return build_graph(SqliteSaver(conn)), conn


def _start(graph, incident_id: str, sample_id: str):
    sample = fixtures.get_sample(sample_id)
    assert sample
    config = {"configurable": {"thread_id": incident_id}}
    result = graph.invoke(
        {
            "incident_id": incident_id,
            "sample_id": sample_id,
            "narrative": sample["narrative"],
            "mode": "demo",
            "revision_count": 0,
            "revision_notes": "",
            "approval_history": [],
            "timeline": [],
            "locked": False,
            "status": "started",
            "psers_tags": [],
            "supervisor_escalate": False,
            "protocol_answers": {},
        },
        config=config,
    )
    return result, config


def test_happy_path_locks_only_after_dispatcher(tmp_path: Path):
    graph, conn = _graph(tmp_path)
    result, config = _start(graph, "inc-1", "cardiac")
    assert result["__interrupt__"][0].value["gate"] == "facts"

    result = graph.invoke(
        Command(resume={"action": "approve", "role": "call_taker"}), config=config
    )
    assert result["__interrupt__"][0].value["gate"] == "priority"
    assert graph.get_state(config).values.get("locked") is not True

    result = graph.invoke(
        Command(resume={"action": "approve", "role": "call_taker"}), config=config
    )
    # Cardiac default answers (breathing=no) escalate -> conditional supervisor gate
    assert result["__interrupt__"][0].value["gate"] == "supervisor"
    assert graph.get_state(config).values.get("locked") is not True

    result = graph.invoke(
        Command(resume={"action": "approve", "role": "supervisor"}), config=config
    )
    assert result["__interrupt__"][0].value["gate"] == "dispatch"
    state = graph.get_state(config).values
    assert state["cad_payload"]["status"] == "DRAFT_PENDING_DISPATCHER"
    assert state.get("locked") is not True

    result = graph.invoke(
        Command(resume={"action": "approve", "role": "dispatcher"}), config=config
    )
    assert not result.get("__interrupt__")
    state = graph.get_state(config).values
    assert state["locked"] is True
    assert state["cad_payload"]["status"] == "LOCKED"
    assert "CAD Assist Summary" in state["export_markdown"]
    assert any(a["gate"] == "dispatch" for a in state["approval_history"])
    conn.close()


def test_revise_loop_and_fact_edit(tmp_path: Path):
    graph, conn = _graph(tmp_path)
    result, config = _start(graph, "inc-2", "mvc")
    assert result["__interrupt__"][0].value["gate"] == "facts"

    graph.invoke(
        Command(
            resume={
                "action": "edit",
                "role": "call_taker",
                "edits": {
                    "chief_complaint": "MVC with confirmed entrapment — edited",
                    "location": {
                        "address": "Oak & 5th (NW corner)",
                        "city": "Harborview",
                        "confidence": "high",
                        "notes": "Caller correction",
                    },
                },
            }
        ),
        config=config,
    )
    state = graph.get_state(config).values
    assert "edited" in state["chief_complaint"]
    assert state["location"]["address"].startswith("Oak")

    # At priority gate — revise back
    assert graph.get_state(config).tasks
    result = graph.invoke(
        Command(
            resume={
                "action": "revise",
                "role": "call_taker",
                "feedback": "Address is NW corner only",
            }
        ),
        config=config,
    )
    assert result["__interrupt__"][0].value["gate"] == "facts"
    state = graph.get_state(config).values
    assert state["revision_count"] == 1
    conn.close()


def test_fire_path_escalates(tmp_path: Path):
    graph, conn = _graph(tmp_path)
    _, config = _start(graph, "inc-3", "structure_fire")
    graph.invoke(Command(resume={"action": "approve", "role": "call_taker"}), config=config)
    state = graph.get_state(config).values
    assert state["pending_gate"] == "priority" or graph.get_state(config).tasks
    assert state["supervisor_escalate"] is True
    assert any(u["id"] == "E3" for u in state["recommended_units"])
    conn.close()
