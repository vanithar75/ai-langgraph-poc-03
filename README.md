# Demo Director (LangGraph POC Option D)

Presales **Demo Customizer with Success Criteria Gate**.

From account + persona + pains, the agent maps capabilities, drafts a timed demo script + environment checklist, then a leave-behind one-pager — pausing for human approve / edit / revise before demo-day artifacts lock.

## Why this POC

- Clear LangGraph story: typed state, SQLite checkpoints, `interrupt()` / resume
- HITL is the product: SE approval + AE revise loop
- Budget-safe: **demo mode by default** (deterministic fixtures, no LLM calls)

## Quick start

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000
```

Open [http://127.0.0.1:8000](http://127.0.0.1:8000).

## Workflow

1. Intake ICP / persona / must-win outcomes  
2. Map pains → product capabilities → **Gate 1**  
3. Generate 30/45/60-min script + checklist → **Gate 2** (SE approve / AE revise)  
4. Leave-behind + CTA → **Gate 3**  
5. Lock package + download markdown export  

## API

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/api/samples` | Sample accounts |
| POST | `/api/demos` | Start a run |
| GET | `/api/demos/{id}` | State + pending interrupt |
| POST | `/api/demos/{id}/resume` | Approve / edit / revise / reject |
| GET | `/api/demos/{id}/export.md` | Markdown download |

Resume body example:

```json
{
  "action": "revise",
  "role": "ae",
  "feedback": "Lead with forecast scrub."
}
```

## Tests

```bash
source .venv/bin/activate
pytest -q
```

## Budget notes

- Default `mode` is `demo` — no OpenAI calls  
- Optional LLM path is stubbed behind `OPENAI_API_KEY` + `mode=llm` (not required)  
- No auth, CRM, or PDF renderer in MVP scope  

## Notion

Implementation plan: [Demo Director (Option D)](https://app.notion.com/p/3a2818bdc73d8198a729f4481c90c52e)
