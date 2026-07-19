# Implementation Plan — Platform Redesign (docs/platform-redesign.md)

## Context

`docs/platform-redesign.md` (authored 2026-07-17) redesigns the platform around one principle: **metadata flows one direction with one writer per plane, and Query Studio becomes a stateful-feeling, clarify-instead-of-erroring conversational agent** — while the AI Engine stays stateless (transcript replay, no checkpointer). It fixes three compounding problems: dual metadata channels (push + self-read), eroded service boundaries (AI Engine samples Trino and writes postgres-meta despite "read-only" claims), and brittle one-shot UX (raw "Query Failed" banners, lossy text-blob memory).

Step **1a is already done** (commit `2dd7978`): the query path no longer pushes the catalog; the AI Engine self-loads it. This plan implements the remaining steps **1b → 2 → 3 → 4 → 5 → 6 → 7 → 8 in doc order**, as 8 independently shippable PRs.

**Confirmed decisions:** doc-order sequencing (metadata planes first) · sample-value gating **default-allow** (`unclassified` behaves as `public`; tighten per column later) · **minimal test bootstrap** (pytest + go test for new pure logic; E2E compose smoke remains the main gate).

### Corrections to the redesign doc discovered during exploration (plan accounts for all)

1. **4 `InvalidateCache()` call sites, not 3**: `handlers/upload.go:75`, `:95`, `services/datasource_service.go:237` (`RefreshSchema`), `:338` (`SyncCatalogsFromTrino`). `HandleRefreshAll` loops RefreshSchema → N pokes.
2. **Dashboard pipeline never calls `_ensure_datasets`** (doc's 1a note is wrong — verified `main.py:615-698`). PR3 must add self-load to the dashboard pipeline **before** dropping the Core API push.
3. **Upload sample values never reach prompts today**: `registerMetadata` writes a comma-joined string (`upload_service.go:252`) but `parse_samples` (`schema_render.py:141`) requires JSON — silently discarded. Removing the upload write is a strict improvement.
4. **`RefreshSchema` writes nothing the planner reads** (only `data_sources.schema_cache`) — needs one explicit version bump; triggers won't fire for it.
5. **Few-shot example embeddings are audit-log-driven, not metadata-driven** — their triggers (startup + opportunistic per-plan) stay unchanged; only schema profiling/embedding becomes version-driven.
6. **No tests exist anywhere** in the repo; migrations live in **Alembic** (`ai-engine/alembic/versions/0001_baseline_schema.py`), run by the `db-migrate` compose service which gates service startup (so schema-before-code ordering is enforced).

---

## Canonical new schema objects (6 new Alembic revisions, raw-SQL style like 0001)

| Revision | Ships in | Contents |
|---|---|---|
| `0002_metadata_state` | PR1 | `metadata_state(id BOOLEAN PK DEFAULT TRUE CHECK(id), version BIGINT DEFAULT 1, updated_at)` single-row + seed row; `bump_metadata_version()` plpgsql fn; statement-level AFTER INSERT/UPDATE/DELETE triggers on `datasets`, `dataset_columns`, `table_relationships`. **No** triggers on `data_sources` (schema_cache churns every 300s) or worker-output tables (self-trigger loop). |
| `0003_column_profiles` | PR2 | `column_profiles(dataset_column_id INT PK REFERENCES dataset_columns(id) ON DELETE CASCADE, pattern TEXT, suggested_semantic_type VARCHAR(50), distinct_count INT, null_fraction REAL, sample_values TEXT /*JSON {leaf_path:[values]}, gated*/, stats JSONB DEFAULT '{}', content_hash TEXT NOT NULL, profiled_at)`. `ALTER dataset_columns ADD semantic_type VARCHAR(50)` (NULL = unknown), `ADD sensitivity VARCHAR(20) NOT NULL DEFAULT 'unclassified' CHECK IN ('unclassified','public','sensitive','forbidden')`. **Backfill** existing `sample_values` into `column_profiles` so prompts never blink. |
| `0004_drop_legacy_sample_values` | PR3 | `ALTER dataset_columns DROP COLUMN sample_values` (zero references remain after PR2). |
| `0005_transcript` | PR4 | `ALTER conversation_turns ADD kind VARCHAR(20) NOT NULL DEFAULT 'plan' CHECK (kind IN ('plan','clarification')), ADD payload JSONB`. Old rows replay via defaults — no backfill. |
| `0006_needs_curation` | PR6 | `needs_curation(id, kind VARCHAR(40), dataset_id INT NULL REFERENCES datasets ON DELETE SET NULL, column_name, question TEXT, detail TEXT, status new|resolved DEFAULT 'new', request_id UUID, conversation_id UUID, created_at)` + status index. |
| `0007_value_lookups` | PR8 | `value_lookups(id, column_trino_path, column_name, semantic_type, lookup_trino_path NOT NULL, key_column NOT NULL, match_columns TEXT[], description, created_at, CHECK(semantic_type IS NOT NULL OR (column_trino_path IS NOT NULL AND column_name IS NOT NULL)))` + bump trigger. **String-bound, not FK** — upload re-registration DELETEs+reinserts columns; a CASCADE would destroy curated bindings. |

> **Post-PR8 squash (2026-07-19):** with all 8 PRs landed and the platform still pre-launch
> (deployments start from a wiped `postgres-meta-data` volume), the whole chain — the
> revisions above plus the two unplanned fixes `0007_datasets_source_type_parity` /
> `0008_widen_dataset_column_type` and `value_lookups` (shipped as `0009`) — was folded back
> into the single `0001_baseline` revision. Transitional backfills were dropped (fresh-DB
> only). Squash verified by applying old chain vs. new baseline to scratch databases and
> diffing `pg_dump -s` output: byte-identical, including column physical order. The baseline
> also carries the `reports` / `report_sheets` tables (Excel reports feature, post-redesign).
> Until launch, schema changes amend `0001_baseline_schema.py` directly instead of adding
> revisions.

---

## PR1 — Step 1b: `metadata_version` replaces the poke web

**Mechanism:** DB **triggers** bump the version (the only mechanism covering SQL-only curation — there are no curation endpoints today) + **one explicit Go bump** in `RefreshSchema` (correction #4). AI Engine: keep the 60s TTL read-caches as-is; a **watcher loop** polls the version (~5s) and on change runs `clear_tool_cache()` + the enrichment pipeline. Staleness after any write drops from "whenever someone remembers to poke" to ≤ poll interval.

**AI Engine**
- NEW `ai-engine/enrichment.py`: `get_metadata_version()` (SELECT from `metadata_state`; tolerate missing table), `run_enrichment_pipeline(provider)` (v1 body = relocated `_reindex_schemas_safe` logic: `sampling.reindex_samples()` → `embeddings.reindex_datasets()`), `watch_metadata_version()` loop (first iteration always runs → preserves today's startup warm; `asyncio.Lock` + re-read after run coalesces bump storms; debounce floor).
- `ai-engine/config.py`: add `metadata_version_poll_seconds=5.0`, `enrichment_min_interval_seconds=30.0`.
- `ai-engine/main.py`: lifespan (~:205) spawns the watcher task (cancel on shutdown); delete `_reindex_schemas_safe` (:129-147) and the **`/api/invalidate-cache` endpoint** (:825-840). Keep `/api/reindex-schemas` until PR2.

**Core API**
- Delete `AIClient.InvalidateCache` (`services/ai_client.go:231-244`) + all 4 call sites; remove now-unused `aiClient` fields/params from `UploadHandler` and `DataSourceService` (+ constructor calls in `core-api/main.go:43-46,62`).
- `services/metadata.go`: add `BumpMetadataVersion(db)` (`UPDATE metadata_state SET version=version+1, updated_at=NOW()`); call it in `RefreshSchema` after caching the schema.

## PR2 — Step 2: `column_profiles` split + `semantic_type`/`sensitivity`

- NEW `ai-engine/profiler.py` replaces `sampling.py` (delete it). Carries over `_collect`/`_keep`/cardinality constants/Trino `SELECT … LIMIT 25` walk; adds null tallies, `_detect_pattern()` (deterministic: uuid, alpha3_code, alpha2_code, numeric_code, iso_date, email, url, internal_enum), `_suggest_semantic_type()` (pattern+name heuristics → doc taxonomy; **suggestion only**, persisted to `column_profiles.suggested_semantic_type` — humans promote to `dataset_columns.semantic_type` via SQL; one-writer rule preserved). **Gate** per `dataset_columns.sensitivity`: `forbidden` → DELETE profile row, skip; `sensitive` → stats/pattern only, `sample_values=NULL`; `public`/`unclassified` → everything (**default-allow**). Upsert keyed on `content_hash`.
- `enrichment.run_enrichment_pipeline` becomes profile → classify → embed (`profiler.reindex_profiles()` → `embeddings.reindex_datasets()`).
- Reader joins move samples to profiles: `embeddings._load_dataset_texts` (:69-103), `agents/tools/metadata_tools._async_get_datasets` (:35-77, also emit `semantic_type` + `pattern`), `agents/tools/schema_tools._async_get_columns` (:152-199) — all `LEFT JOIN column_profiles cp ON cp.dataset_column_id = dc.id`. Gated columns thereby drop out of prompts **and embeddings**.
- `ai-engine/models.py` `DatasetColumn`: add optional `semantic_type`, `pattern`.
- `ai-engine/main.py`: delete `/api/reindex-schemas` (:843-867; ops escape hatch = `UPDATE metadata_state SET version=version+1`, document in README); rewrite the module boundary docstring (:25-36) to the honest §12 statement (Planner vs Enrichment worker).
- Core API: `upload_service.registerMetadata` (:213-260) stops writing `sample_values`, delete `collectSamples` (:406-424); `services/metadata.go getColumns` (:62-83) joins `column_profiles` (keeps `DatasetMeta.SampleValues` flowing to SchemaPanel tooltip + dashboard push during transition); comment-only updates at `datasource_service.go:641-647,659-668` (the samples carve-out is now structural via FK CASCADE; description/is_primary_key/semantic_type/sensitivity remain curated-untouchable — reconcile already doesn't touch them).

## PR3 — Step 3: one `ContextBundle` builder (+ drop dashboard push + drop legacy column)

- NEW `ai-engine/context_bundle.py`: `@dataclass ContextBundle{datasets, relationships, lookups=[], examples=[]}`; `build_context_bundle(question, *, provider, datasets=None, include_examples=True, emitter=None)` absorbs `_load_datasets`/`_ensure_datasets`/`_select_relevant_datasets` (RAG trim + stage event)/`_load_relationships`/`_load_examples`. Renderers: `render_fast_path_system_prompt(bundle)` + `render_fast_path_user_prompt(request)` (absorb `prompt_builder.py`) and `render_extra_context(bundle)` (absorbs `main._build_extra_context` + `_render_relationships_lines`). Both keep using `schema_render.py` helpers. New shared behavior lands once: `pattern` hint (`format: alpha3_code`) on column lines whose samples are absent/gated.
- `ai-engine/main.py`: rewire `_run_plan_pipeline`, **`_run_dashboard_pipeline` (add self-load — correction #2)**, `repair_widget` (question=None → no RAG trim); delete the absorbed helpers. **Delete `ai-engine/prompt_builder.py`.**
- Core API: drop the last dataset push — `handlers/dashboard.go` remove `GetAllDatasets()` at :269/:315, pass nil; `ai_client.go` `DashboardPlanRequest.Datasets` gains `omitempty`, drop datasets params from `GenerateDashboardPlan`/`StreamDashboardPlan`/`RepairWidgetSQL`/`buildDashboardPlanRequest`; remove unused `metadataSvc` wiring.
- Migration `0004` drops `dataset_columns.sample_values`.
- Verification is golden-diff: rendered system prompt + extra_context for a fixed `DatasetMeta` fixture must be identical before/after (modulo the new pattern-hint line).

## PR4 — Step 4: structured transcript replay

**Canonical message shape (Go/Python/TS):** `{role: user|assistant, kind: question|answer|plan|clarification, content: string, payload?: object}`. `plan` payload = `{sql, row_count, confidence}`; `clarification` payload = `{options, clarification_kind, failed_sql?, error?}`. `answer` is **inferred at read time** (user turn following a clarification outcome). Turns stay the persistence unit (one row = one exchange); messages are exploded from turns at read.

**Assembly is server-side (Core API)** — frontend keeps sending `{question, mode, conversation_id}`; client-sent transcripts would be forgeable (static bearer auth) and would fork the source of truth. The doc's §6 sketch showing browser-sent `messages` is resolved this way: the "caller" that replays is Core API; the AI Engine stays stateless.

- Migration `0005_transcript` (above).
- `core-api/models/models.go`: `ChatMessage{Role, Kind, Content string; Payload json.RawMessage}`.
- `core-api/services/conversation_service.go`: `ConversationTurn` grows `Kind/Payload/CreatedAt`; `AppendTurn(id, question, kind, sql string, rowCount int, payload []byte)`; the parent-touch UPDATE also writes the title: `title = COALESCE(title, LEFT($2,80))` (first recorded turn names the thread — §5's unused `title` finally written). New `BuildMessages(id, turnLimit) []ChatMessage` — explodes turns to user+assistant message pairs, synthesizes payload for legacy rows from `question/sql/row_count`, truncates per-SQL (~2000 chars) and total (~12k chars, the successor to the 8k `conversation_context` cap). Keep the pure explosion logic in a free function over `[]ConversationTurn` for unit testing.
- `core-api/handlers/query.go`: replace `loadConversationContext` (:64-86) with `loadConversationMessages()`; `recordConversationTurn` (:89-94) → `recordPlanTurn` marshaling the plan payload.
- `core-api/services/ai_client.go`: `PlanRequest` — **delete `ConversationContext`, add `Messages []models.ChatMessage json:"messages,omitempty"`** (clean cutover; everything co-deploys via compose — no dual-field transition).
- `ai-engine/models.py`: `ChatMessage` pydantic model; `PlanRequest.messages: List[ChatMessage] = [] (max_length=24)` replacing `conversation_context`. Current turn stays in `question`.
- `ai-engine/prompt_builder.py` (pre-PR3) or `context_bundle.py` (post-PR3): new pure `render_transcript(messages, max_chars=8000)` — oldest-first `[user] …` / `[assistant → SQL] … (returned N rows)` / `[assistant asked] …` / `[user answered] …`. Swap into `build_user_prompt` (:230-237) and the `main.py:539-544` extra-context fold. Render-to-text, not native chat messages, in v1 — both consumption paths are single-message-shaped; native history is a later optimization.

## PR5 — Step 5: clarification as a terminal outcome

- `ai-engine/models.py`: `Clarification{question, options: list[str] = [], kind: str = "ambiguous"}` (kinds: ambiguous_entity | unresolved_value | missing_data | repair_exhausted | ambiguous); `PlanOutcome{plan?, clarification?, path}` as the blocking `/api/plan` envelope.
- Orchestrator (`agents/orchestrator.py`): **flat optional fields on `QueryPlanDesign`** (`sql` no longer required + `clarification_question/options/kind`) — NOT a Pydantic Union (response_format reliability through deepagents). Prompt gains "Clarify instead of guessing" section; empty-sql-and-no-clarification stays a `ValueError`. `generate_query_plan` returns `QueryPlan | Clarification`.
- Fast path: `build_system_prompt` documents the alternative `{"clarification": {...}}` JSON shape; `llm/openai_provider.generate_plan` parse branch returns `Clarification`. **A fast-path clarification does not end the turn — it escalates to the full pipeline** with a hint (analogous to `_build_fastpath_hint`), so the cheap model can't spam questions; only the full pipeline's clarification is terminal.
- **Depth cap:** count `kind=="clarification"` messages in the replayed window; at ≥2, append prompt instruction "do NOT ask another — best-effort plan + state assumptions". Prompt-enforced only (never fabricate SQL in code).
- `main.py`: `_run_plan_pipeline` returns the union (clarifications skip `_apply_validation`); `/api/plan` → `PlanOutcome`; `/api/plan/stream` → terminal `event: clarification`, flat payload `{question, options, kind}` per §7.
- Core API: `proxyAIStream` (`handlers/sse.go:102`) signature → `terminalEvents []string`, returns `(name, data, ok)`; call sites: query `["plan","clarification"]`, dashboards `["dashboard_plan"]`. `models.Clarification` + `QueryResponse.Clarification *Clarification omitempty`. `query.go` streaming: on clarification terminal → `recordClarificationTurn` (kind=clarification, payload=clarification JSON), re-emit `clarification` as Core API's own terminal, **do not execute**; blocking: `GeneratePlan` returns `(plan, clarification, err)`, clarification → HTTP 200 QueryResponse with empty rows. Audit stays `success` (avoids a CHECK-constraint migration; flagged for later).
- Frontend (minimal, ships in this PR — see atomicity note): `useQuery.ts` terminalTypes → `["result","clarification"]`, clarification state + card render (question + option buttons → `executeQuery(option,"ai")` on same conversation); `types/index.ts` `Clarification` + `QueryResponse.clarification?`.
- **Atomicity constraint:** Python + Go + minimal frontend must land in one release — an un-updated `proxyAIStream` forwards the unknown terminal then errors "ended without terminal"; same one hop up for the frontend. Fine under compose co-deploy.
- Update `docs/sse-events.md` (add `clarification` to both tables; reserve `thinking` — **deferred**: existing stage/llm/tool events already animate the timeline per turn; all layers forward unknown events, so it can be added anytime).

## PR6 — Step 6: repair unification + zero-row + curation queue

- Refactor `executeWithRepair` (`query.go:341-370`) → `executeWithRepair(reqID, sql, question string, deadline time.Time, progress execProgress)`; `HandleQueryStream` replaces the bare `Execute` (:309) with it, `progress` emitting `stage` events `repairing_sql` (detail `attempt 1/2: <first line of error>`) / `executing_sql` — renders in `AIProgressTimeline` today with zero frontend work.
- **Timeout budget:** deadline = handler start + 360s (WriteTimeout is 430s); past deadline → skip further repairs and the zero-row pass. Add a 15s heartbeat ticker on `sseStream` for the Core-API execution/repair phases (AI Engine heartbeats stop when its stream ends; a 120s silent execute is already at proxy-idle risk today).
- **Zero-row sanity pass (AI mode, bounded):** on success with `RowCount==0`, one repair-shaped call with `mode="zero_rows"` + hint ("if a filter literal likely mismatches stored values, correct it; if zero rows is genuinely right, return SQL UNCHANGED"). Unchanged → original result, no curation row. Changed → re-validate + re-execute; better → use it (stage `zero_rows_retry`, record repaired SQL); worse/still-zero → return **original** result + `needs_curation` row `zero_rows_unresolved`. `RepairWidgetRequest` gains `mode: Literal["error","zero_rows"]="error"` (Python + Go); `/api/repair-widget` picks a zero-rows prompt variant (current prompt asserts "FAILED to execute" — false here).
- **Repair-exhausted → templated clarification** (not a raw error, not a new LLM call): `kind="repair_exhausted"`, question template with first line of engine error; recorded as clarification turn with `failed_sql` + `error` in payload so the user's reply replays with full failure context (that carry is what makes it recoverable). Blocking path same shape via HTTP 200. `needs_curation` row `repair_exhausted`. Non-AI failures keep terminal `error`.
- Migration `0006_needs_curation`; new `core-api/services/curation_service.go` (best-effort `Add(...)`), `handlers/curation.go` `GET /api/curation?status=new&limit=50`, route in `main.go`. **v1: table + list endpoint only — no UI, no resolve endpoint** (SQL flips status). Single-token auth gap flagged for when real auth lands.
- Update `docs/sse-events.md` (repair stages; note the streaming path now has the repair loop).

## PR7 — Step 7: frontend transcript UX

- `frontend/src/hooks/useQuery.ts` (substantial rewrite): **turns-based state** mirroring `conversation_turns` — `TranscriptTurn{id, userText, userKind: question|answer, mode, progress: AIProgressEvent[] (per-turn), status: streaming|done|error, outcome?: {type: result|clarification|error, …}}`; hook state `{turns, status, history}` (drop top-level result/error/progress). `executeQuery` appends a turn and streams into it; marks prior clarification `answered`. Wire the existing `newConversation()` (:168-171). New `loadConversation(id)`.
- `App.tsx` `QueryPage` + NEW `components/Transcript.tsx`: scroll region + `QueryInput` pinned bottom (existing `hasResults` compact mode). Per turn: user bubble → `AIProgressTimeline` (auto-collapses when done) → outcome (`QueryPlanView` + results-meta + `ResultsChart` + `ResultsTable` reused per-turn; NEW `ClarificationMessage` card with option buttons, disabled once answered; error-banner styling inline as agent message). Auto-scroll with `stickToBottom` flag. CSS additions in `App.css` (`.transcript`, `.chat-turn`, `.chat-bubble--user`, `.clarification-card`, sticky composer). `STAGE_LABELS` gains `repairing_sql`, `zero_rows_retry`.
- Conversations endpoints: `conversation_service.go` `ListConversations(limit)` (title, times, turn_count; skip zero-turn) + `ListTurns(id)` (oldest-first incl. kind/payload); NEW `handlers/conversations.go` → `GET /api/conversations?limit=30`, `GET /api/conversations/:id/turns`; routes in `main.go`.
- `LeftSidebar.tsx`: third tab **Chats** (+ "New chat" button) distinct from the History (audit_logs) tab. Selecting one sets `conversationIdRef` + hydrates turns → plan turns render plan card + "N rows — run again to view" (result rows deliberately not persisted, §14 minimum-persistence); clarification turns hydrate `answered` from whether a later turn exists. `api/client.ts` gains both calls.

## PR8 — Step 8: `value_lookups`

- Migration `0007_value_lookups` (above; no seed rows — deployment-specific SQL documented with an example INSERT).
- `agents/tools/metadata_tools.py`: `_async_get_value_lookups()` (TTL-cached, mirrors `_async_get_relationships` :92-105) + tool.
- `context_bundle.py`: populate `bundle.lookups`; `_render_value_lookups(lookups, datasets)` — expands `semantic_type` auto-bindings against in-bundle columns (`dataset_columns.semantic_type` matches), renders the "Coded-Column Lookups" block (NEVER guess literals; subquery pattern `WHERE col IN (SELECT key FROM lookup WHERE lower(match_col)=lower('<term>'))`; unresolvable term → lower confidence / clarify, pairs with PR5's `unresolved_value` kind). A lookup-bound column **suppresses its inline sample literals** (samples invite the literal-guessing §8 forbids).
- Prompt lines: orchestrator forward-verbatim bullet (`orchestrator.py:80-91`); `sql_generator.py` subquery-not-literal rule (~:141). Both paths get the block automatically via the shared builder.
- Curation SQL-only in v1 (same precedent as `table_relationships` — no CRUD endpoint exists for those either); the 0007 bump trigger makes SQL inserts live within the poll interval.

---

## Key reuse (don't rebuild)

- `schema_render.py` helpers (`parse_samples`, `describe_column`, `column_sample_suffix`, `categorical_values_block`) — shared by both renderers, unchanged.
- `agents/tools/_cache.py` `async_ttl_cache` + `clear_all` — watcher reuses `clear_tool_cache()`.
- `proxyAIStream` forward-verbatim default branch (`handlers/sse.go:125-128`) + `dispatchFrame`/`streamAIOperation` unknown-event forwarding (`sse.ts`) — clarification/thinking need no transport changes beyond terminal registration.
- `AIProgressTimeline` (renders unknown event types already), `QueryPlanView`, `ResultsTable`, `ResultsChart`, `QueryInput` compact mode — compose the transcript from these as-is.
- `_build_fastpath_hint` pattern (`main.py:422-435`) — template for fast-path clarification escalation.
- Dashboard `verifyAndRepairWidget` progress-event pattern (`dashboard.go:446-484`) — template for streaming query repair stages.

## Risks

- **Bump storms** → watcher coalescing + `enrichment_min_interval_seconds` floor + content-hash-gated embeds; profiling bounded at 25 rows/table.
- **Transcript growth** → 6-turn window (`convoTurnLimit`), per-SQL truncation, 8k render cap, `max_length=24` validator. Summarization deferred (§14).
- **Clarification loops / eagerness** → depth cap 2 (prompt-enforced), fast-path clarifications escalate instead of terminating; monitor via turn `kind` counts.
- **Streaming time budget** → 360s deadline + Core-side heartbeat ticker; WriteTimeout stays 430s.
- **PR5 atomicity** → Python+Go+minimal frontend in one release (compose co-deploy makes this natural).
- **`0004` column drop is lossy on downgrade** → ships one PR after the backfill; profiles supersede the data.
- **Default-allow gating = no posture change until curation** → deliberate, one-line tightening in the profiler gate later.
- `audit_logs.status` CHECK lacks a `clarification` value → recorded as `success` in v1; one-line follow-up migration later.

## Verification (per PR; `make dev` hot-reload stack; no CI exists)

1. **Test bootstrap (minimal):** `ai-engine/requirements-dev.txt` with `pytest` + `pytest-asyncio`; go test files beside touched packages. Unit-test the pure surfaces: `profiler._detect_pattern`/`_suggest_semantic_type`/gating decisions, `render_transcript` (caps, answer/clarification rendering), provider plan-vs-clarification parse branch (factor the JSON-dict branch out of `generate_plan`), message explosion/answer inference/truncation (free function over `[]ConversationTurn`), `proxyAIStream` multi-terminal via stubbed stream func, repair/zero-row decision table via tiny `Execute`/`RepairWidgetSQL` interfaces.
2. **Migration check:** scripted `alembic upgrade head` + `downgrade -1` per revision against a scratch `pgvector/pgvector:pg16` container.
3. **Compose smoke per PR** (examples): PR1 — upload a CSV → `SELECT version FROM metadata_state` bumped → engine log shows one pipeline run ≤5s later, `/api/invalidate-cache` returns 404. PR2 — `column_profiles` rows exist; mark a column `sensitive` + bump → its literals vanish from `/api/metadata/datasets` and the rendered prompt. PR3 — golden prompt diff; dashboards still generate with no datasets pushed (verify request body in core-api logs). PR4 — two-turn follow-up ("now break that down by region") resolves; `conversations.title` populated. PR5 — ambiguous question → clarification card; clicking an option yields a plan next turn; dashboard streams unaffected. PR6 — question engineered to produce a broken table name → `repairing_sql` stages then result or repair-exhausted clarification + `needs_curation` row. PR7 — full chat walkthrough incl. sidebar Chats tab, hydration, new-chat. PR8 — seed a `countries` lookup via SQL; "Indian drivers" question produces subquery SQL, no literal `'IND'` guess.
4. **Docs:** update `docs/sse-events.md` in PR5/PR6; tick off steps in `docs/platform-redesign.md` §13 as each PR lands.
