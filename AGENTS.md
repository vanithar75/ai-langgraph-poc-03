# AGENTS.md

## Cursor Cloud specific instructions

CAD Assist Control Loop — a single-process Python FastAPI + LangGraph human-in-the-loop (HITL) training/demo app. One service to run; no external databases or auxiliary services.

### Environment
- Python venv at `.venv/` (gitignored). Activate with `source .venv/bin/activate`. The update script recreates/refreshes it.
- State persists to an embedded SQLite checkpoint at `.checkpoints/cad_assist.db` (gitignored), auto-created on first run. Delete `.checkpoints/` to reset all incidents. Override the path with `CAD_DB_PATH` (used by the test suite to stay isolated).
- `OPENAI_API_KEY` is optional. Without it the app runs fully offline in `demo` mode. With it, `mode="llm"` uses `langchain-openai` for extraction but always falls back to the deterministic extractor on any error, so the demo never breaks.

### Run / test / lint (see `README.md` for canonical run commands)
- Dev server (hot reload): `uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload`, then open http://127.0.0.1:8000.
- Tests: `pytest -q` (`tests/test_graph.py` graph flow, `tests/test_api.py` API via `TestClient`).
- Eval harness (behavioral, per sprint): `python -m evals` (all), `python -m evals sprint4`, `python -m evals sprint5`, add `--json` for machine output. Exits non-zero on any failure.
- No dedicated linter/formatter is configured.

### Workflow gotchas (non-obvious)
- The flow gates through `interrupt()` HITL approvals. Gate roles: facts=`call_taker`, priority=`call_taker`, supervisor=`supervisor`, dispatch=`dispatcher`.
- **Conditional supervisor gate:** after Gate 2, if `supervisor_escalate` is true the graph inserts a Supervisor gate (Gate 2.5) before the CAD draft. Escalation is data-driven (see `data/protocols.json` rules). Disable the gate with `CAD_DISABLE_SUPERVISOR_GATE=1` (stop-rule fallback). Because default cardiac/overdose answers escalate, those samples stop at the supervisor gate before dispatch.
- **Lock invariant:** `locked=true` is only ever set by `lock_cad` after the Dispatcher gate. Post-lock `POST /api/incidents/{id}/amend` appends immutable versioned addenda (bumps `cad_version`) and never clears the lock; the v1 baseline is snapshotted in `amendments`.
- **Adding incident types is config-only:** add a pack to `data/protocols.json` (with a `rules` block) and a canned sample to `data/incidents.json`. No Python rule changes needed — `fixtures.suggest_priority_and_plan` evaluates the config.
- **UI caching:** the single-file `static/index.html` is served fresh, but browsers may serve a stale cached page after edits. Hard-reload (Ctrl+Shift+R) or append a query string when verifying UI changes.
