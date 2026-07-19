# CAD Assist Control Loop (LangGraph HITL)

**Training/demo only — not operational PSAP software.**

Stateful LangGraph workflow: caller narrative → fact extract → mock protocol path → CAD draft, with human gates before anything locks.

Built as the next sprint after Demo Director in `ai-langgraph-poc-03`.

## Demo line

> Watch the agent stop before anything hits CAD.

## Quick start

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Open http://127.0.0.1:8000

## Workflow

1. Intake canned incident (cardiac / structure fire / MVC) or paste narrative  
2. **Gate 1 — Call Taker** confirms location + chief complaint  
3. Mock protocol questions → priority + unit suggestions  
4. **Gate 2 — Call Taker** approves plan (or **Revise** to re-extract)  
5. CAD draft JSON (still unlocked)  
6. **Gate 3 — Dispatcher** must approve before lock + export  

## API

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/api/samples` | Canned incidents |
| POST | `/api/incidents` | Start run |
| GET | `/api/incidents/{id}` | State + pending interrupt |
| POST | `/api/incidents/{id}/resume` | approve / edit / revise / reject |
| GET | `/api/incidents/{id}/cad.json` | CAD payload download |
| GET | `/api/incidents/{id}/export.md` | Summary markdown |

## Tests

```bash
pytest -q
```

## Budget / scope guardrails

- Demo fixtures by default (no LLM)  
- No telephony, GIS, AVL, radio, or certified protocol content  
- Mock protocol cards are explicitly labeled training fixtures  
- Optional PSERS tags on timeline steps for presales storytelling only  

## Notion

Plan: [Emergency Call Handling & CAD Assist](https://app.notion.com/p/3a2818bdc73d81c0979ecba92d537b52)
