# Federated Analytics Platform — Phase 1 POC

> **Demo**: Natural language → AI query plan → federated Trino query → visualization

[![Architecture](https://img.shields.io/badge/Architecture-Microservices-blue)]() [![Go](https://img.shields.io/badge/Core%20API-Go%20%2F%20Gin-00ADD8)]() [![Python](https://img.shields.io/badge/AI%20Engine-Python%20%2F%20FastAPI-3776AB)]() [![React](https://img.shields.io/badge/Frontend-React%20%2B%20TypeScript-61DAFB)]()

---

## Quick Start

### 1. Prerequisites

- Docker Desktop (with Docker Compose v2)
- An OpenAI or Anthropic API key (only needed for AI mode)

### 2. Configure Environment

```bash
cd federated-analytics-platform
cp .env.example .env
```

Edit `.env` and set:
```
LLM_PROVIDER=openai          # or anthropic
OPENAI_API_KEY=sk-...        # your OpenAI key
# OR
ANTHROPIC_API_KEY=sk-ant-...  # your Anthropic key
```

**New machine or coming back after a while?** `make setup` does the above for
you — creates `.env` from `.env.example` if it doesn't exist yet and generates
`DATASOURCE_ENCRYPTION_KEY`, without ever overwriting an `.env` you already have.

Before pushing, run `make env-check` — it's the same drift check
(`scripts/check-env-sync.py`) that runs in CI on every PR, so a `.env.example`/
`docker-compose.yml` mismatch (a var one of them references that the other
doesn't know about) shows up locally instead of as a failed check.

### 3. Start the Stack

```bash
docker compose up --build
```

Wait ~2 minutes for all services to start (Trino takes the longest).

### 4. Add Your Data

The platform **does not seed data** — you bring your own.

**Connect to the source databases:**

| Database | Host | Port | Credentials |
|---|---|---|---|
| PostgreSQL Source | localhost | 5433 | source_user / source_pass_2024 |
| MongoDB Source | localhost | 27017 | (no auth) |
| PostgreSQL Metadata | localhost | 5434 | meta_user / meta_pass_2024 |

Create tables in `postgres-source`, collections in `mongo-source`.

**Register your datasets in the metadata DB** (so the AI knows what exists):

```sql
-- Connect to postgres-meta (port 5434)
INSERT INTO datasets (name, description, source_type, trino_catalog, trino_schema, trino_table)
VALUES (
  'my_table',
  'Description of what this table contains',
  'postgresql',
  'postgres_source',  -- Trino catalog name
  'public',           -- PostgreSQL schema
  'my_table'          -- Table name
);

-- Add column metadata (helps AI generate accurate queries)
INSERT INTO dataset_columns (dataset_id, column_name, data_type, description, is_joinable)
VALUES
  (1, 'id', 'INTEGER', 'Primary key', true),
  (1, 'name', 'VARCHAR', 'Name field', false),
  -- add more columns...
```

### 5. Open the UI

Visit **http://localhost:3000**

---

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│  Frontend (React + TS)  :3000                                   │
│  Talks ONLY to Core API — never to databases or AI Engine      │
└──────────────────────┬──────────────────────────────────────────┘
                       │ REST (all traffic)
┌──────────────────────▼──────────────────────────────────────────┐
│  Core API (Go / Gin)  :8081                                     │
│  • Stub auth (Bearer token)                                     │
│  • Audit logging to postgres-meta                               │
│  • AI feature flag toggle                                       │
│  • Plan validation BEFORE execution (SELECT-only guard)         │
└──────┬──────────────────────────────────┬───────────────────────┘
       │ POST /api/plan                   │ POST /api/execute
       │ (question + schema context)      │ (validated SQL)
┌──────▼──────────┐              ┌────────▼───────────────────────┐
│  AI Engine      │              │  Query Service (Go)  :8083     │
│  (Python/       │              │  • Second-layer SQL validation  │
│   FastAPI) :8082│              │  • Executes via Trino driver    │
│                 │              └────────┬───────────────────────┘
│  • OpenAI / Claude              │ JDBC / HTTP
│  • Structured output    ┌───────▼───────┐
│  • Reads metadata       │  Trino  :8080 │
│  • NEVER executes SQL   └──┬────────────┘
│  • NEVER touches infra     │ Federated queries
└─────────────────┘    ┌─────┴──────┐  ┌─────────────────┐
                        │ Postgres   │  │  MongoDB        │
                        │ Source     │  │  Source         │
                        │ :5433      │  │  :27017         │
                        └────────────┘  └─────────────────┘
```

### Architecture Boundaries (Phase 2 Contract)

| Boundary | Rule | Why |
|---|---|---|
| Frontend → API | Only talks to Core API | Single entry point, audit everything |
| AI isolation | AI Engine only produces JSON plans | AI is non-deterministic; keep it sandboxed |
| Plan validation | Core API validates plan before forwarding | Defense in depth — never trust LLM output directly |
| Query isolation | Only Query Service talks to Trino | Keeps the query execution surface minimal |
| SELECT-only enforcement | Both Core API AND Query Service validate | Two independent layers of SQL safety |

---

## Service URLs

| Service | URL | Purpose |
|---|---|---|
| Frontend | http://localhost:3000 | Main UI |
| Core API | http://localhost:8081 | All frontend traffic goes here |
| Core API Health | http://localhost:8081/api/health | Health check (no auth required) |
| AI Engine | http://localhost:8082 | Internal only (not called from browser) |
| Query Service | http://localhost:8083 | Internal only |
| Trino UI | http://localhost:8080/ui | Trino web interface |
| PostgreSQL Source | localhost:5433 | Connect with your SQL client |
| MongoDB Source | localhost:27017 | Connect with MongoDB Compass etc. |
| PostgreSQL Metadata | localhost:5434 | Contains dataset registry + audit logs |

---

## API Usage

### Submit a Query (AI Mode)

```bash
curl -X POST http://localhost:8081/api/query \
  -H "Authorization: Bearer poc-demo-token-2024" \
  -H "Content-Type: application/json" \
  -d '{
    "question": "What are the top 5 products by total sales?",
    "mode": "ai"
  }'
```

### Submit a Query (SQL Mode — no API key needed)

```bash
curl -X POST http://localhost:8081/api/query \
  -H "Authorization: Bearer poc-demo-token-2024" \
  -H "Content-Type: application/json" \
  -d '{
    "question": "SELECT * FROM postgres_source.public.orders LIMIT 10",
    "mode": "sql"
  }'
```

### Get Query History

```bash
curl http://localhost:8081/api/history \
  -H "Authorization: Bearer poc-demo-token-2024"
```

### Get Available Datasets

```bash
curl http://localhost:8081/api/metadata/datasets \
  -H "Authorization: Bearer poc-demo-token-2024"
```

---

## MongoDB + Trino Schema Notes

Trino infers MongoDB schemas by sampling documents. For best results:
- Keep field types consistent within a collection
- If fields have mixed types, they'll be typed as `VARCHAR`
- To define explicit schemas, insert a document into the `_schema` collection:

```javascript
db._schema.insertOne({
  table: "products",
  fields: [
    { name: "product_id", type: "varchar" },
    { name: "name", type: "varchar" },
    { name: "price", type: "double" },
    { name: "stock", type: "integer" }
  ]
})
```

This tells Trino exactly what types to use, regardless of what it samples.

---

## Demo Query (Cross-Source Federation)

Once you have orders in Postgres and products in MongoDB, try this in SQL mode:

```sql
SELECT
  p.category,
  COUNT(o.order_id) as total_orders,
  SUM(o.amount) as total_revenue
FROM postgres_source.public.orders o
JOIN mongodb.default.products p
  ON o.product = p.name
GROUP BY p.category
ORDER BY total_revenue DESC
LIMIT 10
```

This JOIN spans PostgreSQL and MongoDB — federated by Trino. This is the platform's core value proposition.

---

## Project Structure

```
federated-analytics-platform/
├── docker-compose.yml       # All 8 services
├── .env.example             # Copy to .env
├── core-api/                # Go / Gin — main orchestrator
├── ai-engine/               # Python / FastAPI — LLM query planner
├── query-service/           # Go — Trino execution layer
├── frontend/                # React + TypeScript + ECharts
├── trino/                   # Trino catalog configs
│   └── catalog/
│       ├── postgres_source.properties
│       └── mongodb.properties
└── ai-engine/alembic/       # postgres-meta schema (Alembic migrations)
    └── versions/
        └── 0001_baseline_schema.py   # full metadata schema, applied by db-migrate
```

---

## Development & Testing

### Getting access

This repo is private. Ask the owner to add you as a collaborator (GitHub username or
the email tied to your GitHub account) before cloning.

### First-time setup

```bash
git clone <this-repo>
cd federated-analytics-platform
make setup        # creates .env from .env.example + generates DATASOURCE_ENCRYPTION_KEY
```

Edit `.env` and set at least one LLM key (`GOOGLE_API_KEY`, `OPENAI_API_KEY`, or
`ANTHROPIC_API_KEY` — Gemini is the default provider, see `.env.example`).

### Running the stack

| Command | What it does |
|---|---|
| `make up` | Prod-like stack (`docker-compose.yml` only) — matches what CI/a real deploy runs |
| `make dev` | Dev stack with hot reload — `air` recompiles Go on save, Vite HMR for the frontend, no rebuild needed per edit |
| `make down` / `make dev-down` | Stop the respective stack |
| `make ps` / `make logs` | Status / follow logs for whichever stack is running |
| `make fresh-start` / `make dev-fresh-start` | Wipe all data, rebuild, start, and seed — for testing the full flow from empty |

Use `make dev` day-to-day; `make up` if you want to test exactly what CI/production builds.

### Before you push

```bash
make env-check     # catches .env.example / docker-compose.yml drift — same check CI runs
```

### Running the QA suites

Both need a running, seeded stack (`make up && make seed-all`, then sync catalogs — see
`qa/README.md`). **Run them one at a time**, not together — they share one LLM backend's
rate limit.

```bash
./qa/run.sh                        # Playwright UX suite (headless Chromium, no host deps)
python3 qa/ai-eval/run_eval.py      # AI-quality eval (deterministic checks)
python3 qa/ai-eval/run_eval.py --judge   # + LLM-as-judge scoring (needs OPENAI_API_KEY)
```

See `qa/README.md` for what each suite covers and how to run a single spec/scenario.

### Continuous Integration

Every PR against `main` runs `.github/workflows/pr-checks.yml`: Go vet/build/test
(`core-api`, `query-service`), `ai-engine` pytest, the frontend build, and the `env-check`
drift check — all of it must pass before merging. The full Playwright UX suite (`e2e-ux`)
also runs on every PR, but only on the PR itself: it's skipped on the subsequent push to
`main` once merged, since it's already been verified and re-running it there would just
burn another round of real LLM quota for no new signal.

`.github/workflows/ai-eval.yml` runs the AI-quality eval nightly (and on-demand via
`workflow_dispatch`), publishing the scorecard as a job summary — a signal to watch for
accuracy regressions, not a per-PR or per-merge gate (`run_eval.py` doesn't have a
pass/fail threshold today, and running it on every merge would cost more LLM quota than
it's worth).

### Contributing

1. **Never push directly to `main`** — always branch and open a PR, even for small
   changes. (This is a team convention, not a GitHub-enforced rule: classic branch
   protection/rulesets require GitHub Pro on a private repo, so nothing technically
   stops a direct push today — treat this as the actual rule anyway.)
2. Push your branch **to this repo, not a fork** — collaborators have write access
   here, and GitHub withholds Actions secrets (`GOOGLE_API_KEY`, etc.) from PRs
   opened from forks, so a fork-based PR's `e2e-ux`/AI checks would fail for reasons
   unrelated to your change.
3. Make your change; run the relevant QA suite(s) and `make env-check` locally.
4. Open a PR against `main` — `pr-checks.yml` runs automatically (Go vet/build/test,
   ai-engine pytest, frontend build, env-check, full Playwright e2e). All of it needs
   to pass.
5. Get the repo owner to review and merge. Don't self-merge.

---

## Phase 2 Roadmap (Not in POC)

- Real OIDC/LDAP authentication (replace stub token)
- Kubernetes / Helm deployment charts
- Vector DB + embedding-based schema search
- ClickHouse materialization layer
- OpenSearch integration
- Streaming query results via SSE
- Multi-tenant isolation
- HA / production hardening
