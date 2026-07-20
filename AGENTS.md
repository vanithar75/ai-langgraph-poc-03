# AGENTS.md

## Cursor Cloud specific instructions

CAD Assist Control Loop — a single-process Python FastAPI + LangGraph human-in-the-loop (HITL) training/demo app. There is only one service to run; no external databases or auxiliary services are required.

### Environment
- Python venv lives at `.venv/` (gitignored). Activate with `source .venv/bin/activate` before running anything. The update script recreates/refreshes it on startup.
- State is persisted to an embedded SQLite checkpoint at `.checkpoints/cad_assist.db` (gitignored), auto-created on first run. Delete `.checkpoints/` to reset all incident state.
- `OPENAI_API_KEY` is optional and only flips `mode` reporting to `llm`; the extractor is deterministic regardless, so the app runs fully offline in `demo` mode.

### Run / test / lint (see `README.md` for canonical commands)
- Tests: `pytest -q` (config in `pytest.ini`; 3 graph tests, no server/network needed).
- Dev server (with hot reload): `uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload`, then open http://127.0.0.1:8000.
- No dedicated linter/formatter is configured in this repo.

### Workflow gotcha
- The core flow gates through three `interrupt()` HITL approvals: start incident (`POST /api/incidents`) pauses at Gate 1 (facts, `call_taker`), then `POST /api/incidents/{id}/resume` advances Gate 2 (priority, `call_taker`) and Gate 3 (dispatch lock, `dispatcher`). Only after Gate 3 does `locked` become true and `cad.json`/`export.md` become available. Approving without walking all three gates leaves the incident unlocked.
