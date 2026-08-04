# QA harness — headless UX + AI-quality testing

Two reusable, headless test suites so the platform can be regression-tested without a
manual QA team. Both run against a **live stack** (`make up` + seeded data + a catalog
sync). Neither needs anything installed on the host beyond Docker (UX) / Python 3 (AI eval).

```
qa/
  ux/                 Playwright (headless Chromium) end-to-end UX suite
  ai-eval/            NL-scenario evaluation harness for the AI engine
  Dockerfile          self-contained Playwright runner (browser + deps baked in)
  run.sh              build + run the UX suite on the compose network
  artifacts/          screenshots, HTML report, JSON results (gitignored)
```

## Prerequisites

```bash
make up          # bring the full stack up (prod compose)
make seed-all    # seed Postgres + Mongo + ES contracts
curl -X POST http://localhost:8081/api/datasources/sync \
     -H "Authorization: Bearer poc-demo-token-2024"    # register datasets so the AI has schema
```

## 1. UX suite (headless Chromium via Playwright)

We don't rely on a host browser or on a published `mcr.microsoft.com/playwright` tag —
`qa/Dockerfile` builds a runner image with Chromium + all system libs baked in, and
`run.sh` joins it to the compose network so it reaches the app by service name
(`http://frontend`). This also means tests run in a **non-secure browser context**, which
is deliberate — it caught a real `crypto.randomUUID` white-screen bug.

```bash
./qa/run.sh                 # run every UX spec
./qa/run.sh 03-query        # only specs whose title/file matches "03-query"

# Run against a different target (e.g. the host-published port, or a dev server):
BASE_URL=http://localhost:3000 npx playwright test    # needs `npm install` in qa/ first
```

Outputs land in `qa/artifacts/`:
- `screenshots/*.png` — a named screenshot at each meaningful step
- `html-report/` — the full Playwright report (`npx playwright show-report qa/artifacts/html-report`)
- `results.json` — machine-readable results
- `test-results/**` — trace/video/screenshot for any failure

What the specs cover (`qa/ux/tests/`):
| Spec | Checks |
|---|---|
| `01-smoke-nav` | every route renders, no console errors, no horizontal overflow, theme toggle |
| `02-datasources` | seeded sources listed, "Sync Catalogs" gives a visible toast (not silent) |
| `03-query-ai` | AI query returns a result; friendly progress present; **no raw engine internals leak**; nested contracts question works |
| `04-dashboard` | generate-with-AI → builder renders widgets with data; grid **fills the canvas** (dead-space guard); responsive at 996/768/480 |
| `05-reports` | generate-with-AI → sheets render; Excel download is a valid non-empty `.xlsx` |
| `06-a11y-motion` | visible keyboard focus ring; modal closes on Esc; **no horizontal overflow at 1280/768/390** |

## 2. AI-quality eval (NL scenarios → graded scorecard)

Fires a battery of natural-language scenarios (`qa/ai-eval/scenarios.json`) at Core API's
`/api/query` (the full plan → Trino execution path) and grades each response.

```bash
python3 qa/ai-eval/run_eval.py                 # deterministic checks only (no API cost)
python3 qa/ai-eval/run_eval.py --judge         # + LLM-as-judge (reads OPENAI_API_KEY from .env)
python3 qa/ai-eval/run_eval.py --grep es-      # only scenarios whose id matches "es-"
python3 qa/ai-eval/run_eval.py --judge --concurrency 2   # gentler on rate limits
```

Grading per scenario:
- **plan returned** / **executed without error** / **non-empty rows**
- **right source/table** referenced in the SQL
- **UNNEST used** where the question requires traversing a nested ES array
- **LLM-as-judge** (gpt-4o-mini, independent of the gpt-4o planner) scores 0–5 whether the
  SQL + result actually answers the question

Scenarios span every source (Postgres, Mongo, ES **nested contracts**, cross-source joins)
plus robustness cases (vague / out-of-scope). Outputs → `qa/ai-eval/results/`
(`results.json`, `scorecard.md`).

**Add your own scenarios**: append objects to `scenarios.json`
(`{id, source, category, question, expect_rows, expect_sql_contains, nested}`).

## Notes / gotchas
- Run the two suites **sequentially**, not together — running them concurrently makes them
  fight the single LLM backend (semaphore cap 4), which causes dashboard-generation
  timeouts and judge 429s.
- Health paths differ: core-api `/api/health`; ai-engine & query-service `/health`.
- The static bearer token `poc-demo-token-2024` is baked into the frontend build; direct
  API/eval calls must send `Authorization: Bearer poc-demo-token-2024`.
