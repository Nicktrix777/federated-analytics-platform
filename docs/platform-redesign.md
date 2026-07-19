# Platform Redesign — Metadata Flow + Conversational Query Studio

Status: proposal (design, not yet implemented)
Author: design session, 2026-07-17

## 1. Why

Features have accreted faster than the flow was re-drawn, and three problems now
compound each other:

1. **Two metadata channels for one database.** Core API ships the *entire*
   catalog in every `PlanRequest` ([ai_client.go](../core-api/services/ai_client.go)),
   while the AI Engine *also* reads the same postgres-meta directly for
   relationships, few-shots and embeddings. The pushed payload is then mostly
   discarded — schema RAG trims it back to the top-`schema_rag_top_k` datasets.
   Keeping the two views coherent is the only reason the best-effort
   `InvalidateCache()` HTTP pokes exist (fired from three call sites).
2. **Stated boundaries are no longer true.** [main.py](../ai-engine/main.py)
   declares the AI Engine "NEVER connects to Trino" and "has NO database write
   access", but `sampling.py` runs Trino queries and both sampling and
   embeddings write to postgres-meta. Each was added as a "documented
   exception" — which is what boundary erosion always looks like.
3. **One-shot, brittle UX.** A query is a single request→response. When the
   planner can't resolve something the user gets a raw red "Query Failed" banner
   with a Trino error string. Multi-turn "memory" is a text block Core API
   replays into the prompt; the frontend keeps a single `result`, not a
   transcript. The agent framework's own conversation/interrupt features are
   unused.

This document proposes one coherent flow that fixes all three, and turns Query
Studio into a stateful, conversational, clarify-instead-of-erroring agent.

## 2. Organizing principle

**Metadata flows one direction — discovered → enriched → assembled → consumed —
and each plane has exactly one writer.** Conversation and clarification are
layered on top of the *consume* plane, using the agent framework's native
thread/interrupt machinery instead of a parallel hand-rolled mechanism.

```
1. REGISTRY        Core API writes structure (datasets, columns, types)
       │           on change: bump metadata_version (no HTTP pokes)
       ▼
2. SEMANTIC LAYER  curated (humans): descriptions, semantic_type, sensitivity,
       │                             relationships, value_lookups
       │           derived (worker): column_profiles (patterns/cardinality/
       │                             gated samples), embeddings
       ▼
3. CONTEXT ASSEMBLY  AI Engine owns ALL metadata reads (version-stamped cache).
       │             ONE builder → ContextBundle → feeds BOTH fast path and graph
       ▼
4. CONVERSE + EXECUTE + FEEDBACK
                   stateless transcript replay (Claude-style), clarify on
                   ambiguity as a normal turn outcome, validate → execute →
                   repair on error OR zero rows, outcomes feed few-shots +
                   a "needs curation" queue
```

## 3. Plane 1 — Registry (unchanged in spirit, simplified in mechanism)

Core API's `SyncCatalogsFromTrino` remains the sole discoverer of structure.
Two changes:

- **Drop the catalog from `PlanRequest`.** The request shrinks to
  `{ question, conversation_id }`. The AI Engine reads metadata itself (it
  already does for everything else).
- **Replace cache pokes with a version.** A `metadata_version` (monotonic
  counter or content hash) is bumped whenever registry/semantic rows change. The
  AI Engine's metadata cache checks the version instead of waiting for an
  `InvalidateCache()` HTTP call. **Delete every `InvalidateCache()` call site**
  (datasource sync + both upload handlers).

## 4. Plane 2 — Semantic layer (split the junk drawer, one writer per class)

Today `dataset_columns.sample_values` is simultaneously the enum proxy, the
prompt content, and embedding input — and every enrichment idea piles onto it.
Split by *who writes it*:

| Table | Writer | Holds |
|---|---|---|
| `dataset_columns` | Core API sync (structure) + humans (curated) | name, type, `description`, `semantic_type`, `sensitivity` |
| `column_profiles` | enrichment worker (derived) | `pattern`, cardinality, null rate, **gated** sample values, `content_hash` |
| `table_relationships` | humans | join keys + `cast_expression` (exists today) |
| `value_lookups` | humans | coded-column → reference-table mapping (new; see §8) |
| `dataset_embeddings`, `query_example_embeddings` | enrichment worker | vectors (exist today) |

Rules that fall out of the split:
- Re-sync can **never** clobber curation; re-profiling can **never** touch
  curated columns. The current "preserve on re-sync" special-casing goes away
  because the two live in different tables.
- `semantic_type` (small taxonomy: `country_code_alpha3`, `currency_code`,
  `internal_enum`, `uuid`, `free_text`, …) is the machine-readable hook that
  drives prompt rendering, sensitivity handling, and auto-binding to
  `value_lookups`.
- `sensitivity` gates what the profiler persists: patterns always; literal
  sample values only where classification allows; nothing for forbidden
  columns. This is what makes it safe to keep the sample-value feature where
  it genuinely helps and drop it where it's a breach.

### Enrichment worker (honest boundary)

Rather than pretend the AI Engine doesn't touch Trino, name the two roles:

- **Planner** — never reads user data, never touches Trino. (Stateful now; see §5.)
- **Enrichment worker** — may read sampled rows to compute profiles; may write
  ONLY `column_profiles` + embedding tables; never executes user queries.

The worker runs one ordered pipeline — **profile → classify → embed** — on a
`metadata_version` change, replacing today's three ad-hoc triggers (startup +
cache-poke + manual `/api/reindex-schemas`). Profiling is the only stage that
sees data and is where sensitivity gating lives.

## 5. Plane 4a — Conversational agent (stateless, transcript replay — the Claude model)

This is how Claude itself works, and we follow it: **the AI Engine stays
stateless; the conversation lives in a replayed transcript.** There is no
langgraph checkpointer, no `thread_id`-bound server state, no paused graph. Each
turn the caller resends the conversation's message history; the agent
re-derives its understanding from those messages, exactly as it does today —
only the history is now *structured*, not a lossy text blob.

- **Replace the crude text block with a structured transcript.** Today Core API
  re-renders prior turns as a `Q: ... SQL: ...` string
  ([query.go:64](../core-api/handlers/query.go#L64)) and injects it into the
  prompt ([prompt_builder.py:231](../ai-engine/prompt_builder.py#L231), plus the
  `main.py` extra-context fold). Instead persist and replay real messages —
  user turns, the plans/SQL the agent produced, any clarifying questions and
  their answers, and (optionally) tool results worth retaining. The request
  carries `messages: [...]` instead of a flattened `conversation_context`
  string. Better memory, same stateless model.
- **`conversation_turns` becomes the transcript store.** It already holds
  `question`/`sql`/`row_count` per turn; extend it to record the message
  role/kind (user, plan, clarification, answer) so it can be replayed faithfully
  rather than summarized. `conversations` stays the user-facing thread list and
  finally gets its unused `title` written (name the thread from the first turn).
- **Boundary preserved.** The planner stays "stateless per request"
  ([main.py:29](../ai-engine/main.py#L29)) — that comment stays true. No
  checkpoint tables, no per-thread write access, no retention policy. State is
  the transcript the caller replays.

### Fast path vs. full pipeline (unchanged in spirit)

The fast path remains a per-turn latency/cost optimization: try to one-shot the
question with the cheap single call; escalate to the full pipeline on low
confidence, validation failure, or detected ambiguity. Because everything is
stateless transcript replay, *any* turn — first or follow-up — can take either
path; the transcript comes along either way. The fast path is not "turn 1 only"
anymore; it's just "the cheap attempt for this turn."

## 6. Plane 4b — Clarify instead of error (turn-wise, no interrupt)

Clarification is a **first-class outcome of a normal turn**, exactly how Claude
asks a question: the agent's response for the turn is *either* a plan *or* a
question — never a paused graph. When the agent cannot safely proceed it ends
the turn by returning a structured clarification instead of SQL, e.g.:

- ambiguous entity — "By 'contracts' do you mean `contracts_db.contracts` or
  `legacy.contract_archive`?"
- unresolved value — "I couldn't find a nationality matching 'Indian'; the
  column stores codes. Did you mean IND (India)?" (pairs with `value_lookups`)
- missing data — "No registered dataset has salary information. Want me to use
  compensation from `hr.payroll` instead?"
- repair exhausted — instead of surfacing a raw Trino error, ask a targeted
  question about the part that failed.

There is **nothing to resume.** The turn simply concluded with a question. The
user's reply is the next ordinary turn; the replayed transcript (§5) carries the
question and answer, so the agent continues naturally from full context — no
checkpoint, no `Command(resume=...)`, no server-side pause.

```
turn 1  POST /api/query/stream {messages:[...], conversation_id=C}
        → agent decides it can't disambiguate "contracts"
        → SSE terminal event: clarification {question, options?}
        → turn ends. No state held server-side.
turn 2  POST /api/query/stream {messages:[...prev turn + Q + user's answer], conversation_id=C}
        → agent reads the transcript, now unambiguous → normal plan/result
```

The only new backend logic is that the pipeline may *emit a clarification as a
terminal result* rather than always emitting a plan — a branch in the response
shape, not a new execution mode.

## 7. SSE contract changes

Additive — see [sse-events.md](sse-events.md) for the current contract.

- New terminal event **`clarification`** `{ question, options?, kind }` — a
  successful, expected outcome, NOT an `error`. Core API proxies it verbatim
  (`proxyAIStream` treats it as terminal like `plan`).
- Optional progress event **`thinking`** `{ text }` so the transcript can show
  the agent's reasoning steps as they stream (makes it *feel* like Claude).
- No resume semantics on the wire — a follow-up is just the next `POST` on the
  same `conversation_id` carrying the updated `messages`; no new endpoint, no
  paused state to reattach to.
- The **streaming path gets the repair loop** the blocking path already has
  (`executeWithRepair`), so a Trino failure mid-stream self-heals or escalates
  to a clarification instead of being a hard terminal `error`
  ([query.go:334](../core-api/handlers/query.go#L334)).

## 8. Value lookups (the coded-column fix, now on a clean semantic layer)

`value_lookups` maps a coded column to a Trino-reachable reference table so the
model writes a subquery instead of guessing a literal — no stored value ever
enters the prompt:

```sql
WHERE driver.nationality IN (
  SELECT code FROM postgresql.reference.countries
  WHERE lower(name) = :term OR lower(demonym) = :term)
```

It mirrors `table_relationships` exactly (curated rows the AI Engine loads
itself, rendered into context). `semantic_type = country_code_alpha3` can
auto-bind a column to the shared `countries` lookup. When a term doesn't
resolve, the agent *clarifies* (§6) rather than returning zero rows. Complements
the automatic pattern hints from `column_profiles` (shape) and curated
`description`/`semantic_type` (meaning).

## 9. Plane 3 — One context builder

Collapse the two renderers (`prompt_builder.py` for the fast path,
`_build_extra_context` in `main.py` for the orchestrator) into a single
`ContextBundle` builder: RAG-select top-k → assemble
`{schema+semantics, relationships, lookups, few-shots}` → fast path serializes
it to one prompt, graph hands it to subagents. Prompt improvements land once.
Pure refactor — the rendering logic already exists.

## 10. Frontend — Query Studio as a transcript

- Replace the single `result` in `QueryPage` ([App.tsx](../frontend/src/App.tsx))
  with a **message list**: user turns, agent reasoning (`thinking`), plans,
  results, and clarification prompts interleaved.
- A `clarification` event renders as an agent message with inline options /
  reply box; answering just sends the next turn on the same `conversation_id`.
- Keep the existing SSE transport (`fetch` + ReadableStream, `sse.ts`) — no
  websockets. `newConversation()` (already exported, currently unwired) becomes
  the real "new chat" button; `conversation_id` continues to thread turns.
- The left-sidebar list becomes conversations (from `conversations`), distinct
  from the global `audit_logs` history feed.

## 11. What gets deleted (the de-clutter payoff)

- Catalog payload in `PlanRequest` and all `InvalidateCache()` call sites.
- The lossy `conversation_context` **text blob** — `loadConversationContext`'s
  string rendering and its two prompt-injection sites — replaced by a structured
  `messages` transcript (not deleted memory, *better* memory; §5).
- One of the two context renderers.
- The "preserve curated fields on re-sync" special-casing (structural split
  makes it moot).
- The manual `/api/reindex-schemas` trigger (folds into version-driven worker).

## 12. Boundary restatement (honest, post-redesign)

- **Core API** — owns registry structure + curated semantic rows + execution +
  feedback. Bumps `metadata_version`. No longer pushes catalog or invalidates
  caches.
- **AI Engine / Planner** — owns all metadata *reads* + context assembly +
  conversational planning. **Stateless per request** (conversation state is the
  replayed transcript, not server-held). Never executes user SQL, never reads
  user data rows.
- **AI Engine / Enrichment worker** — reads sampled rows for profiling; writes
  only `column_profiles` + embeddings; version-triggered.

## 13. Sequencing (each step ships and de-clutters independently)

1. **Version-stamped metadata + drop catalog from `PlanRequest`.** Removes the
   dual channel and the poke web. AI Engine reads metadata itself.
   - **1a (DONE).** Dropped the catalog from the query path. The AI Engine now
     self-sources the catalog from postgres-meta (`_load_datasets`/
     `_ensure_datasets` in `main.py`, reusing the TTL-cached
     `_async_get_datasets`); `_async_get_datasets` now includes `id` (needed by
     schema-RAG). Core API's `HandleQuery`, `HandleQueryStream`, and query-repair
     no longer fetch or push `datasets` (nil + `omitempty`, so the field is
     omitted and the engine self-loads). Dashboard flow still pushes datasets and
     is handled by the same `_ensure_datasets` (no-op when present).
   - **1b (DEFERRED).** Version-stamping to replace `InvalidateCache()`. Kept for
     now because the same poke also triggers reindexing (sampling + embeddings);
     converting it to a `metadata_version` signal that also drives the enrichment
     worker is its own step (belongs with step 2's worker).
2. **Split `column_profiles` out; add `semantic_type` + `sensitivity`.** Sampler
   writes the new table with gating; curated fields protected structurally.
3. **One `ContextBundle` builder. (DONE)** Pure refactor; unifies the two
   renderers into `ai-engine/context_bundle.py` (`build_context_bundle` +
   `render_fast_path_system_prompt`/`render_fast_path_user_prompt`/
   `render_extra_context`). Absorbed the plan/dashboard/repair self-load +
   schema-RAG trim + relationships/examples loaders out of `main.py`; the
   dashboard pipeline now self-loads too (correction #2). Deleted
   `prompt_builder.py`; dropped the last dataset push from Core API
   (dashboard + widget repair). Column lines with absent/gated samples but a
   detected pattern now render a `[format: <pattern>]` shape hint. Migration
   `0004` drops the legacy `dataset_columns.sample_values`.
4. **Structured transcript replay.** Replace the `conversation_context` text
   blob with a `messages` array persisted in `conversation_turns` and resent per
   turn. Planner stays stateless.
5. **Clarification as a terminal outcome + `clarification` SSE event.** The
   pipeline may return a question instead of a plan; the reply is just the next
   turn. Clarify instead of error. No interrupt, no resume.
6. **Unify + extend repair (streaming path, zero-row trigger) + "needs
   curation" queue.** Failures become either self-heal, clarification, or a
   curation to-do — never a raw banner.
7. **Frontend transcript UX.** Query Studio becomes conversational.
8. **`value_lookups`** on top of the now-clean semantic layer.

## 14. Risks / open decisions

- **Transcript growth.** A long conversation's `messages` array grows every
  turn; unbounded replay inflates tokens and latency. Cap replayed history
  (last N turns) and/or summarize older turns into a compact preamble — the
  standard stateless-LLM approach. Decide the window; `convoTurnLimit=6` today is
  a reasonable starting point.
- **What to persist per turn.** Minimum is user question + agent plan/SQL +
  clarifications/answers. Retaining intermediate tool results (schema discovery)
  buys fidelity at token cost; since schema tools are TTL-cached, re-deriving is
  cheap, so default to NOT persisting tool results and revisit if fidelity
  suffers.
- **Clarification loops.** Deeply nested clarifications feel like a chatbot that
  won't commit. Cap clarification depth per conversation; prefer offering a
  best-effort plan *with* a caveat over asking a third question.
- **Optional later optimization — checkpointer.** If replaying tool calls ever
  proves expensive (it shouldn't, given TTL caching), langgraph's
  `AsyncPostgresSaver` could freeze/resume mid-pipeline. This is a deliberate
  *future* trade (it reintroduces server state + retention policy) — explicitly
  out of scope for this redesign.
```