# AI Workflow Upgrade Plan

Streamline widget/query generation, persist responses for context, and add
pgvector RAG over linked datasource schemas.

Goals (from scoping): **accuracy** (fewer hallucinated columns / dropped
widgets), **latency** (fewer LLM round-trips, smaller prompts), and
**multi-turn continuity** (session refinement). Response storage = **both**
semantic few-shots + session memory. Vector store = **pgvector** in the
existing `analytics_meta` Postgres.

---

## Current state (what we build on)

- **Query path**: fast single-shot → escalate to deepagents pipeline
  (schema-analyst + sql-generator) with draft hand-off. `ai-engine/main.py`.
- **Dashboard path**: designer picks widgets → **one batched SQL call** for all
  widgets → per-widget verify/repair in core-api. `ai-engine/agents/dashboard_planner.py`.
- **Context today**: core-api `GetAllDatasets()` sends the **full catalog** every
  request (`services/metadata.go:24`); ai-engine injects curated join
  relationships + the **last 10 successful queries by recency** as few-shots
  (`prompt_builder.py`, `metadata_tools.py:107`).
- **No user/session/conversation** anywhere. Auth is a stub token.
- **DB**: `postgres:16-alpine` (no pgvector), shared read/write by core-api,
  read-only by ai-engine. ai-engine is the only OpenAI caller.

## Key architectural decisions

1. **pgvector image swap** — `postgres-meta` → `pgvector/pgvector:pg16`. Data
   volume is compatible (same PG16 data dir); just a `CREATE EXTENSION vector`.
2. **Who writes embeddings?** ai-engine is the only service that can call the
   OpenAI embeddings API, but its boundary says "read-only metadata."
   **Recommendation:** grant ai-engine a *narrow* write scope limited to the new
   derived-embedding tables (`dataset_embeddings`, `example_embeddings`) — these
   are caches of derived vectors, not user/business data. Conversation memory
   stays owned by core-api (it already coordinates requests + writes audit
   logs/dashboards). *Alternative if the boundary must stay pristine:* ai-engine
   computes and **returns** embeddings; core-api persists them. Costs one extra
   round-trip on reindex only — pick this if read-only is sacred.
3. **Embedding model**: `text-embedding-3-small` (1536-dim, cheap, ~$0.02/1M
   tokens). Configurable.
4. **Retrieval lives in ai-engine**, not core-api (core-api can't embed). Core-api
   can keep sending the full catalog initially; ai-engine filters it to the
   relevant subset. Later, core-api can stop sending it entirely.

---

## Phase 0 — Enable pgvector (prereq for Phases 1–2)

- `docker-compose.yml`: `postgres-meta` image → `pgvector/pgvector:pg16`.
- New migration `scripts/migrations/005_pgvector_and_memory.sql`:
  - `CREATE EXTENSION IF NOT EXISTS vector;`
  - Tables from Phases 1–3 (below).
  - Mirror into `init/postgres-meta-init.sql` so fresh volumes match.
- ai-engine: add `embed()` to `OpenAIProvider`; add `embedding_model` +
  vector-search settings to `config.py`. New deps: none for retrieval (raw SQL
  via asyncpg + pgvector's `<=>` operator); register the vector type with
  asyncpg on connect.

**Risk**: existing named volume `postgres-meta-data` must survive the image
swap (it will — same PG16). Document a `CREATE EXTENSION` for existing volumes
(the init script only runs on first init).

## Phase 1 — Schema RAG (accuracy + latency)

Retrieve only the datasets relevant to a question instead of stuffing the whole
catalog.

- **Table** `dataset_embeddings(dataset_id PK/FK, embedding vector(1536),
  embed_text text, content_hash text, updated_at)`. One row per active dataset;
  `embed_text` = name + description + column names/descriptions/sample values.
- **Reindex**: new ai-engine endpoint `POST /api/reindex-schemas` (or fold into
  the existing `/api/invalidate-cache` that core-api already calls after
  upload/registration). Upserts embeddings for datasets whose `content_hash`
  changed. Idempotent, cheap.
- **Retrieve**: new `retrieve_relevant_datasets(question, k)` in ai-engine —
  embed the question, `ORDER BY embedding <=> $1 LIMIT k`, return dataset ids.
  Wire into `_build_extra_context` and `_try_fast_path`: filter the incoming
  `request.datasets` to the retrieved set (union with any explicitly referenced),
  and trim `table_relationships` to those touching the selected datasets.
- **Fallback**: if embeddings are missing/empty (fresh deploy) or k covers
  everything, behave exactly as today (send all). No regression on small catalogs.

**Payoff**: shorter prompts → faster + cheaper + fewer chances to hallucinate a
column from an unrelated table.

## Phase 2 — Semantic few-shot retrieval (accuracy) — DONE

Upgraded "last 10 by recency" → "top-k by semantic similarity to this question."

- **Table** `query_example_embeddings(audit_log_id PK/FK, question, sql_executed,
  embedding vector(1536), model, created_at)` — migration
  `006_query_example_embeddings.sql`, mirrored into the init SQL.
- **Populate**: `embeddings.reindex_examples` embeds successful `audit_logs`
  rows (`status='success' AND mode='ai'`) lacking an embedding (incremental
  anti-join, capped by `few_shot_reindex_limit`). Runs on startup, on the force
  endpoint, and **opportunistically at the start of each plan request**
  (fire-and-forget, lock-guarded) — since the AI Engine never learns of a
  query's success directly (Core API writes `audit_logs` after execution).
- **Retrieve**: `main._load_examples(question)` now calls
  `embeddings.retrieve_similar_examples` first (cosine top-k), feeding
  `prompt_builder._render_examples` via the fast-path `build_system_prompt`;
  **falls back to the recency query** (`_async_get_patterns`) when the example
  index is empty/unavailable.
- **Not changed (follow-up)**: the `get_query_history_patterns` tool used by the
  sql-generator subagent is still recency-based (it's argument-less and rarely
  called per its prompt). Making it question-aware is a small future tweak.

## Phase 3 — Session / multi-turn memory (continuity) — DONE (query flow)

Owned by core-api (relational, no vectors).

- **Tables** (migration `007_conversations.sql`, mirrored into init SQL):
  `conversations(id uuid PK, title, created_at, last_active_at)` and
  `conversation_turns(id, conversation_id FK, question, sql, row_count,
  created_at)` — one row per completed NL→SQL exchange.
- **Contract**: `models.QueryRequest.conversation_id` (Go, optional UUID),
  `ai_client.PlanRequest.conversation_context` (Go) →
  `models.PlanRequest.conversation_context` (Python).
- **core-api** (`services/conversation_service.go`): on an AI query with a valid
  `conversation_id`, `EnsureConversation` (upsert), load the last
  `convoTurnLimit` (6) turns, render a "prior turns" block, pass it to
  `GeneratePlan`/`StreamPlan`; append the new turn after successful execution.
  Best-effort throughout — a conversation-store error never fails the query.
- **ai-engine**: `PlanRequest.conversation_context` is injected into the
  fast-path user prompt (`prompt_builder.build_user_prompt`) and prepended to
  the full-pipeline `extra_context`. Enables follow-ups like "now break that
  down by region".
- **frontend**: `useQuery` mints one `conversation_id` (UUID) per hook instance,
  sends it on both the streaming and fallback query calls, and exposes
  `newConversation()` to start a fresh thread.
- **Scope note**: wired for the **query** flow (both `/api/query` and
  `/api/query/stream`). Dashboards already carry multi-turn state via
  `current_dashboard` on refine; extending conversation turn-recording to the
  dashboard generate/refine flow is a small follow-up (thread the same
  `conversation_id`, record a prompt→dashboard turn).

## Phase 4 — Streamline the widget/query workflow (ties it together)

- **Widgets**: feed Phase-1 relevant-only schema + Phase-2 few-shots into the
  designer and the batched-SQL call → fewer bad columns → fewer verify/repair
  round-trips. **Parallelize** core-api's per-widget verify/repair loop
  (`dashboard.go:386-484`) instead of sequential.
- **Queries**: relevant schema + semantic few-shots raise the fast-path hit rate
  → fewer escalations to the full pipeline → lower p50 latency.
- **Frontend**: thread `conversation_id` through `DashboardBuilderPage` and the
  query page; surface prior turns. Reuse `AIProgressTimeline` + `sse.ts`.

---

## Suggested sequencing

`Phase 0` → `Phase 1` (biggest accuracy+latency win) → `Phase 2` → `Phase 3`
(independent; can run in parallel with 1/2 since it's pure relational) →
`Phase 4` (integration + polish). Each phase ships independently and degrades
gracefully to current behavior if its data is absent.

## Effort / risk snapshot

| Phase | Effort | Risk | Notes |
|-------|--------|------|-------|
| 0 pgvector | S | Low-Med | image swap; verify volume + extension |
| 1 schema RAG | M | Low | graceful fallback to full catalog |
| 2 few-shots | M | Low | reuses audit_logs |
| 3 sessions | M | Low | new tables + contract fields; additive |
| 4 streamline | S-M | Low | prompt wiring + parallelism |

## Open decision to confirm before coding

- **Embedding write ownership** (decision #2 above): narrow ai-engine write scope
  (recommended, simpler) vs. keep ai-engine read-only and have core-api persist
  returned vectors. This is the only architectural-boundary question.
</content>
</invoke>
