# UX Overhaul + AI Workflow Streamlining Plan

Date: 2026-07-20
Status: Wave 1 + Wave 2 backend landed 2026-07-21 — **PR-A1 done, PR-B1 done (its deferred
batched-repair driver was completed by PR-B2), PR-A2 done, PR-B2 done**. All of Track C
(frontend) — including PR-C1, which was originally scoped into Wave 1 — is still unbuilt.

Goals (from product feedback):

1. **Professional drag-and-drop dashboard builder** — full-width responsive tiles, no dead space on the right, real drag/resize affordances.
2. **Immersive, polished UI** — better colors/consistency, hover/press/focus states, loaders on every async button, animations on navigation/generation/reloads.
3. **User-friendly generation progress** — hide agent/LLM/tool internals behind 4-5 plain-language stages; technical detail on demand.
4. **No more `GraphRecursionError`** — report/dashboard generation must be structurally incapable of iterating 20+ times.
5. **Faster + more deterministic generation** — report ~100s → target ≤35s; dashboard ~70-80s → target ≤30s. Prefer deterministic code over LLM round-trips everywhere possible; improve output quality with existing context.

**Confirmed decisions:** no component library, no new frontend deps (CSS-only motion; `WidthProvider`/`Responsive` already ship in the installed `react-grid-layout@1.5.3`); react-grid-layout stays; report/dashboard designers stop being deepagents ReAct loops (chat query planner keeps deepagents); grid layout and chart-type selection become deterministic code; **all Trino execution stays in Core API** (the AI Engine's "never executes SQL" boundary holds — validation is a Core-API probe, the AI Engine only does the repair LLM call); SSE changes are additive (one optional field removal, `tool.args`, is low-priority); `0001_baseline_schema.py` amended directly if schema changes are needed (squash convention) — none are expected.

This plan supersedes the status headers in `docs/platform-redesign.md` (all 8 redesign PRs have landed; §13 should be ticked) and picks up the unfinished Phase 4 items from `docs/ai-workflow-upgrade-plan.md` (parallel verification, few-shots for designers).

---

## Current state — what the audit found

### AI workflows (report ~100s, dashboard ~70-80s, recursion errors)

- **Recursion limit is 20, explicitly**: `config = {"recursion_limit": settings.agent_recursion_limit}` at `orchestrator.py:236`, default 20 at `config.py:131-138`. The report/dashboard designers are deepagents ReAct loops (`report_planner.py:124-136`, `dashboard_planner.py:142-150`) whose only loop gate is "did the model emit tool_calls". Each model turn + tool execution costs 2 supersteps, so 20 ≈ 10 turns — burned by `write_todos` bookkeeping, repeated `task→schema-analyst` delegations (prompt only *suggests* skipping, `report_planner.py:78-82`), and re-reading large tool results that deepagents evicted to its virtual FS. **No retry counter, no per-node cap, no `GraphRecursionError` handler** — a blown run discards all partial work and returns 422 (`report_planner.py:281-283`, `main.py:843-844`).
- **Call budget**: a 5-sheet report makes 5-7 sequential frontier LLM calls + 1 embed (designer turns → structured-output step → batched sheet SQL); dashboards minimum 3 frontier calls, all serial. The schema-analyst subagent adds ≥2 LLM turns to re-summarize catalog data `build_context_bundle` already loaded (`context_bundle.py:71-114` vs `schema_tools.py:77-98`).
- **Quality bug**: schema-analyst findings never reach the SQL writer — `_generate_sheet_sql_batch` reuses only the original `extra_context` (`report_planner.py:269-276`), so SQL can be written against tables the designer saw but the SQL pass didn't.
- **Verification is sequential and heavyweight**: core-api runs each sheet/widget's *full* query serially (`report.go:446`, `dashboard.go:368-418`; sheets carry `LIMIT 10000` from `main.py:856`), each failure triggers per-item `/api/repair-widget` calls that rebuild a full-catalog context bundle (`main.py:991-997`).
- **Nondeterminism left on the table**: deepagents models run at provider-default temperature ~1.0 (`make_langchain_model` passes no temperature, `providers.py:168-197`) while single-shot paths pin 0.0-0.1; batch-SQL responses align by array position, not title (`dashboard_planner.py:321-329`); grid coordinates come from LLM prose conventions (`dashboard_planner.py:106-110`) and are never re-flowed after widgets drop; chart type is LLM-chosen and never checked against the actual result shape.
- **Gemini free tier multiplies everything**: every extra LLM call risks 429 backoff sleeps (`providers.py:200-209`, `openai_provider.py:40-45`) — fewer calls is also the rate-limit fix.

### Dashboard builder

- `<GridLayout width={1200}>` with the non-measuring export and no `WidthProvider` (`DashboardBuilderPage.tsx:3, 411-422`) — tiles occupy the leftmost 1200px regardless of viewport (~656px dead space at 1920px; silent clipping below 1264px because `body{overflow-x:hidden}`, `App.css:94`).
- Zero custom RGL CSS: the drag placeholder is the library-default **red box**, the lone SE resize handle is a near-invisible 5×5px dark chevron.
- Manual widgets always spawn at `{x:0, y:len*4}` (`DashboardBuilderPage.tsx:213-218`) — left-column bias. Layout PUT failures are silently swallowed (`:160-163`). Every widget edit/delete triggers `loadDashboard()` which re-runs *every* widget query (`:223, :300`).
- `DashboardWidgetCard` uses the **previous indigo/slate theme** (`#6366f1`, `#94a3b8`… `DashboardWidgetCard.tsx:62-106`) inside the coral app; hardcodes col0=label/col1=value; `gauge` has no render branch (falls through to bar); `chart_config` is persisted but never read; auto-refresh blanks the chart to a spinner every cycle (`:25, :114-120`).

### Design system

- Real token block exists (`App.css:11-81`) but the v2 layer (`App.css:1099-1501`) hardcodes radii/transitions/status hexes past it; six badge variants, five error looks, four empty-state patterns, two button dialects; 9 copy-pasted modal shells with no Esc/focus-trap/scroll-lock; five native `confirm()` dialogs; no toast system.
- Bugs: `.spinner` defined twice (12px button spinner clobbered by 28px page spinner — Run Query renders a 28px spinner inside a 30px button, `App.css:715` vs `1183`); `.modal-overlay` animates `fadeIn` but only `fade-in` is defined (`App.css:1233`) so modals pop with no animation; `.top-nav`/`.db-builder-header` sticky `top:0` slides under the sticky 48px header (`App.css:1108, 1430`); `.rd-sheets`/`.rd-sheet-card` et al. used in `ReportDetailPage.tsx:424-466` have **no CSS rules**; `App.tsx:156` uses `btn-ghost` without `.btn`; fonts double-loaded (`index.html:11-16` + `App.css:8`).
- Zero `:focus-visible` anywhere while `outline:none` is set on buttons/inputs — keyboard focus is invisible app-wide. No `prefers-reduced-motion`. Skeleton CSS exists but is dead (`App.css:745-762`).
- 8+ async actions have no loader (see census in PR-C2); `.btn-secondary/ghost/danger` have no `:disabled` style yet get disabled during runs.

### Generation progress (SSE)

- `AIProgressTimeline.tsx` renders **every** event as a row: "LLM call — query-planner (gemini-2.0-flash)", token counts, "sql-generator ← delegated by query-planner", raw Trino errors in repair rows, raw `str(e)` in error states. Dozens of rows per run.
- Modal flows unmount the timeline the instant the run ends (`{generating && …}`), so errors appear with no context; no cancel (the `AbortSignal` param in `sse.ts:152-158` is never passed); non-streaming fallback shows only a button spinner for up to 100s.
- Everything needed for a friendly view already arrives; gaps are additive: no `index/total` on `widget`/`sheet` events, no event carrying planned widget/sheet titles after the design lands, no `persisting` stage, `tool.args` ships raw tool inputs to the browser (`events.py:157`) though never rendered.

---

## Track A — AI Engine: kill the recursion class, halve the calls (Python)

### PR-A1 — Structured two-call pipeline for report + dashboard design ✅ DONE (2026-07-21)

Landed as described below, plus two hardening items not in the original bullet list (added after
a design review of the complexity tradeoff): the design call's `max_tokens` was bumped 4000→8000
(matching the batched-SQL call's headroom — the default was truncating large designs mid-JSON),
and a confidence-gated retry was added — a design scoring below
`settings.design_low_confidence_retry_threshold` (default 0.4) gets ONE retry against the full,
untrimmed catalog (bypassing the schema-RAG top-k selection), keeping the retry only if it scores
higher. See `main.py:_retry_low_confidence_design` and memory `backend-overhaul-pr-a1-b1-landed`.
Verified: ai-engine `pytest` 126/126 passing, `docker compose build ai-engine` clean.


**The change that removes `GraphRecursionError` structurally: no graph, no loop, no tools on these paths.**

- `generate_report_plan` (`report_planner.py:203`) and `generate_dashboard_plan` (`dashboard_planner.py:209`) stop calling `run_agent`. New shape, both flows:
  1. **One structured design call** — system = existing designer prompt minus Step-1 delegation text, minus grid-position instructions (Step 4, `dashboard_planner.py:106-110`); user = brief + `render_extra_context(bundle)`; call the existing `generate_json` (`openai_provider.py:253`) then construct `ReportDesign(**data)` / `DashboardDesign(**data)` in the caller for validation. **Note:** `generate_json` is `json_object` mode with signature `(system_prompt, user_prompt, max_tokens)` — it takes no schema/model param and does not validate; the Pydantic construction is what enforces the shape, and a `ValidationError` there is the retry trigger (reuse the existing `_structure_*_design` reformat helper as the retry). Schemas keep everything except `grid_position`, which becomes optional-ignored.
  2. **The existing batched SQL call**, unchanged in role (`_generate_sheet_sql_batch` `report_planner.py:286`, `_generate_widget_sql_batch` `dashboard_planner.py:294`).
- Schema discovery becomes deterministic: the context bundle already loads catalog + relationships + lookups; delete the schema-analyst delegation and `list_available_sources` from these two flows (subagent stays for the chat planner). This also fixes the "analyst findings never reach the SQL writer" bug by construction. After this, `orchestrator.py:141` (chat query planner) is the *only* remaining `create_deep_agent` caller.
- **Chat query planner keeps deepagents** but gets guarded: `run_agent` takes a per-caller `recursion_limit` (chat ≤ 12). Catch `langgraph.errors.GraphRecursionError` and return a clean, user-friendly failure instead of a raw 422. **Correction (verifier):** there is *no* existing query-plan reformat pass to fall back to — `_structure_report_design`/`_structure_widget_design` are hard-wired to the design schemas and take a `raw_output` string, and `run_agent` doesn't capture partial messages when `astream_events` raises (`orchestrator.py:247-248` only fires on `on_chain_end`). A true reformat-on-recursion fallback (a `QueryPlan` structuring prompt + streaming message accumulation) is **net-new work** — treat it as a stretch, not a wire-up; the baseline fix is the lower cap + graceful error.
- Determinism hardening: align batch-SQL responses by `title` with position fallback (`dashboard_planner.py:321-329`, `report_planner.py:313-328`) — the batch prompts already request a `title` field, today's parser just discards it. The report/dashboard design + batch-SQL calls run through `generate_json`/`generate_widgets_sql`, which **already** hardcode `temperature=0.1` (`openai_provider.py:272, 176`), so no change is needed there. Separately set `temperature=0.1` in `make_langchain_model` (`providers.py:180`, currently unset → provider default ~1.0) — but note this only affects the *remaining* deepagents user (the chat planner), not the design paths.
- Quality: to actually surface semantic few-shots to the designers, flipping `include_examples=False`→`True` (`main.py:696, 829`) is **not enough** — `render_extra_context` (`context_bundle.py:600-648`) renders schema/relationships/lookups only and never touches `bundle.examples` (only `render_fast_path_system_prompt` renders them, via `_render_examples` `context_bundle.py:406-437`). PR-A1 must also add an examples section to `render_extra_context`; otherwise flipping the flag just burns a wasted `retrieve_similar_examples` embed per generation. Keep sample values/lookups as-is (already strong).

Acceptance: no code path on report/dashboard generation can invoke LangGraph; a 6-widget dashboard = exactly 2 LLM calls + 1 embed; repeated identical briefs produce *near-identical* widget sets (temp 0.1 reduces but does not eliminate variance — Gemini is not deterministic even at 0.0).

### PR-A2 — Batched repair endpoint + faster context (respecting the SQL-execution boundary) ✅ DONE (2026-07-21)

Landed as described below. New endpoint `POST /api/repair-widgets-batch` (`main.py:1197`,
request/response models `models.py:294-338`) batches every failing widget/sheet's repair into
ONE context-bundle build + ONE `generate_json` call — title-keyed alignment with positional
fallback, mirroring `_generate_widget_sql_batch`'s existing convention; items carry a per-item
`mode` (`error`/`zero_rows`) so a single batch can mix both. A per-item validation failure
returns that item's original SQL unchanged (`changed: false`) rather than failing the whole
batch, so Core API's re-probe naturally falls back to the untouched single-item
`/api/repair-widget` for just that straggler. `build_context_bundle` (`context_bundle.py:184-216`)
now resolves datasets/relationships/lookups/examples concurrently via a 4-way `asyncio.gather`
instead of sequentially. The `run_trino_query` FAILED/CANCELED-before-`nextUri` fix was already
live from the PR-A1 pass; this PR added regression coverage (`ai-engine/tests/test_common.py`,
6 new tests). The "Check OPENAI_API_KEY" 503 messages were re-verified and are already generic
(fixed incidentally during PR-A1) — confirmed, no further change needed.

Verified: ai-engine `pytest` 132/132 passing (126 baseline + 6 new), hand-tested against the
live dev stack with a genuine hallucinated-column item and a genuine zero-rows item (both
returned correctly repaired SQL), `docker compose build ai-engine` clean, fresh-container boot
confirmed healthy. Boundary check passed: the new endpoint only calls `build_context_bundle`
and the LLM provider — no Trino execution added to ai-engine.


**Boundary correction (verifier):** the AI Engine must *not* execute SQL — `ai_client.go:16-19` and `main.py:980-981` document "the AI Engine never executes queries or touches infrastructure; Core API re-verifies against Trino." So the `EXPLAIN (TYPE VALIDATE)` / probe validation stays **in Core API** (PR-B1), which already owns the Trino boundary. The AI Engine's role here is purely the *repair* LLM call.

- **Prerequisite bug fix (blocker):** `run_trino_query` (`_common.py:119-158`) silently swallows Trino failures — the poll loop hits `if not next_uri: break` (`:145-146`) *before* the `state in ("FAILED","CANCELED")` check (`:148-151`), and a terminal error response carries `state=FAILED` with no `nextUri`, so it returns `[]` instead of raising. Any Trino-backed validation would treat invalid SQL as "passed". Reorder the checks so a `FAILED`/`CANCELED` state raises. (This client is used by the schema tools too, so the fix is broadly beneficial — but it is *required* before any validation trusts its result, wherever that validation runs.)
- **Batched repair**: add an AI-Engine endpoint (or extend `/api/repair-widget`) that accepts *all* failed items + their error text in one request and returns fixed SQL for all, using the same JSON-batch pattern as `generate_widgets_sql`/`generate_sheets_sql`. Core API (PR-B1) collects every probe failure and calls this **once** instead of per-item round-trips (each of which today rebuilds a full-catalog bundle, `main.py:991-997`). Keep the single-item `/api/repair-widget` for Core API's residual per-item path.
- `asyncio.gather` the context-bundle loads (relationships + lookups + examples alongside the RAG trim, `context_bundle.py:189-197`).
- Wire the existing `zero_rows` review mode (`main.py:948-966`) into the report path. **Correction (verifier):** today the *chat/query* path uses `zero_rows` (`query.go:580`, the `zero_rows_retry` loop `:580-609`) — dashboard widgets and report sheets both call with `mode="error"` (`dashboard.go:443`, `report.go:513`). Copy the `query.go` loop as the reference, not the widget path.
- Fix the 503 message that says "Check OPENAI_API_KEY" on a Gemini-default stack (`main.py:655, 794`).

Acceptance: a plan with one invented column repairs in a single extra LLM call driven by Core API; report pipeline emits ≤3 LLM calls worst case (design + batch SQL + one batched repair). `run_trino_query` raises on a `FAILED` query (add a test).

---

## Track B — Core API: parallel verification, deterministic layout + chart type (Go)

### PR-B1 — Parallel verify with cheap probes + batched repair driver ✅ DONE (partial, 2026-07-21)

**Landed:** bounded parallel verify (dashboard.go/report.go, `maxParallelVerify = 6`) and the
LIMIT-25 probe (`probeSQL` in `handlers/query.go`). Implemented with a stdlib
`sync.WaitGroup` + channel semaphore instead of `golang.org/x/sync/errgroup` — same bounded-
concurrency behavior, zero new go.mod dependency. Each goroutine writes only its own result-slice
index (no lock needed there); `sseStream` gained a `writeMu` mutex since progress-event writes
can now arrive from multiple goroutines concurrently.

**Batched-repair driver — ✅ landed 2026-07-21, as part of PR-B2** (it was blocked on PR-A2's
endpoint existing; once PR-A2 landed, the driver was built alongside PR-B2 since both touch the
same `validatePlanWidgets`/`validatePlanSheets` verify/repair code). See PR-B2's section below
for the two-phase probe → batch-repair → re-probe restructuring.

**NOT landed (deferred):**
- **SSE additions** (`index`/`total` on widget/sheet events, `plan_summary`, `persisting` stage)
  — skipped for now since they exist to feed PR-C4's friendly-progress UI, which isn't built;
  adding them earlier would be unconsumed surface area.
- **Concurrent/transactional widget-sheet persistence** — skipped; single-row INSERTs are ~5ms
  each and aren't the latency bottleneck this PR targets.

Verified: `go build ./... && go vet ./... && go test ./...` clean (via a `golang:1.22-alpine`
container, no go.mod/go.sum changes), `docker compose build core-api` clean.


- Replace the sequential per-item loops (`validatePlanWidgets` `dashboard.go:368-418`, `validatePlanSheets` `report.go:446-470`) with a bounded `errgroup` (≤6 concurrent). Verification wall time: sum → max. (Confirmed no `errgroup`/goroutine usage exists in either handler today.)
- Verify with a probe: execute `SELECT * FROM (<sql>) LIMIT 25` instead of the full query. Today `verifyWidgetSQL`/`verifySheetSQL` run the complete query and **discard** the result (`_, err := h.queryClient.Execute(...)`, `dashboard.go:470`, `report.go:540`); sheets carry a `LIMIT 10000` injected upstream at `main.py:856` (not in the Go verifier). Capture the probe's `(columns, rows)` instead of discarding — PR-B2 needs the shape. This is where the Trino execution boundary lives (per PR-A2's correction).
- **Batched-repair driver**: collect *all* probe failures across the errgroup, then make **one** call to the AI-Engine batched-repair endpoint (PR-A2) instead of the current per-item `/api/repair-widget` round-trips (`dashboard.go:438-461`, `report.go:495-534`), re-probe the returned SQL, drop what still fails.
- Persist widget inserts concurrently or in one transaction (`dashboard.go:486-489`).
- SSE additions (additive per the `docs/sse-events.md:82` "unknown events ignored" rule): `index`/`total` fields on `widget`/`sheet` events (`dashboard.go:324-332`, `report.go:399-407`); a `plan_summary {titles:[...], total}` event right after the terminal `dashboard_plan`/`report_plan` unmarshal (`dashboard.go:308-322`) so the UI can pre-render the checklist; a `stage: persisting` before the final insert. Optionally stop emitting `tool.args` (`events.py:157`, already a 200-char summary via `_summarize`, never rendered) — note this is a *field removal*, mildly contra the "additive only" stance, but harmless since the field is optional and unconsumed; low priority.

### PR-B2 — Deterministic layout + shape-checked chart types ✅ DONE (2026-07-21)

Landed as described below, plus the PR-B1 batched-repair driver it was bundled with (same
files, same verify/repair code path — see PR-B1's note above). `validatePlanWidgets`/
`validatePlanSheets` (`dashboard.go`/`report.go`) now run three phases: (1) parallel probe of
every candidate, unchanged concurrency style; (2) barrier — every Phase-1 failure becomes one
item in a single `RepairWidgetsBatch` call instead of N per-item round trips; (3) parallel
re-probe of whatever the batch fixed, falling back to the existing single-item
`verifyAndRepairWidget`/`verifyAndRepairSheet` retry loop only for a straggler the batch didn't
fix. `report.go` additionally runs a Phase-4 `repairZeroRowSheets` — one batched `mode:
"zero_rows"` round for sheets whose probe succeeded with 0 rows, mirroring `query.go`'s
`handleZeroRow` semantics (never drops a sheet over a genuinely-empty result).
`computeGridLayout`/`gridBand`/`gridPosition` (`dashboard.go`) replace the LLM's grid
coordinates entirely, banding KPI/chart/table widgets exactly as specified below;
`existingWidgetLayout` threads `current *models.Dashboard` through `planToWidgetRequests` so a
refine keeps surviving widgets' `grid_position` byte-identical and only computes positions for
genuinely new widgets, appended below the prior layout's bottom edge. `resolveChartType` mirrors
`ResultsChart.tsx`'s numeric/time/boolean classifier in Go; per the CHECK-constraint note below,
both the "horizontal bar" and "multi-series" cases collapse to plain `"bar"` rather than
inventing an unsupported `chart_type` value.

**Known follow-up (flagged during implementation, not a bug against this spec):** rule 2 (a
time-flavored column + ≥1 numeric column → `"line"`) is unconditional, so a designer-proposed
`"table"` for a multi-column detail listing that happens to include a `*_date` column gets
reclassified to `"line"` even when other non-numeric label columns are also present — observed
live in the verification smoke test. Matches the shape table exactly as specified; worth a
product look before it's tightened (e.g. gating rule 2 on a small total column count).


- **Layout algorithm** (~25 lines) runs after the final drop point (`planToWidgetRequests`, `dashboard.go:532`), replacing LLM grid coordinates entirely:
  - sort: `number`/`gauge` → charts (`line`/`area`/`bar`/`pie`/`scatter`) → `table`, preserving designer order within each band
  - KPIs `w=3 h=2`, 4 per row; charts `w=6 h=4`, 2 per row, odd last chart widens to `w=12`; tables `w=12 h=4` at bottom; `y` advances per band — overlap impossible by construction, survives drops.
  - Refine mode: preserve positions of widgets whose `title` survives; place only new ones (refine payload already carries `grid_position`, `ai_client.go:160-173`).
- **Chart-type confirmation**: capture `(columns, rows)` from the PR-B1 probe (today discarded — `_, err :=` at `dashboard.go:470`) and apply a shape table, keeping the LLM's type only when shape-compatible: 1×1 numeric → `number`; time col + numeric → `line`; 1 cat + 1 numeric ≤10 rows all ≥0 → `pie` allowed; ≤12 rows → `bar`; >12 → horizontal bar; ≥2 numeric → multi-series; else `table`. (Mirror of `ResultsChart.tsx:29-104`'s classifier — see PR-C3 for the frontend twin.)

Acceptance: generate a dashboard, drop one widget mid-verification → no holes in the layout; a "number" widget whose SQL returns 5 rows lands as a bar/table, not a broken KPI. Build via `docker compose` (no local Go toolchain).

---

## Track C — Frontend (ordered; C1 → C2 → C3/C4 in parallel → C5)

### PR-C1 — Design-system foundation (tokens, bugs, a11y)

- Split `App.css` (1,888 lines, three conflicting eras) into `styles/tokens.css`, `styles/primitives.css`, `styles/features.css` (or keep one file but reorganized — executor's choice; tokens first).
- Migrate the v2 layer (`App.css:1099-1501`) onto existing tokens: `--radius-*` (raw 6/8/10/12px), `var(--transition)` (raw `all 0.15s`), `--red/--green/--amber` (raw `#ef4444/#22c55e/#eab308`), `--font-mono`. Delete the dead rainbow source-badge rules (`:1309-1313`, overridden at `:1853-1857`) and the off-brand purple `ai-summary-banner` (`:1399` → coral `--accent-dim`).
- Fix concrete bugs: rename the button spinner (`.spinner` collision `App.css:715` vs `1183`); define the `fadeIn` keyframe or rename usage to `fade-in` (`:1233`); **sticky offsets — two stacked bars, not one**: `.header` (48px, `top:0`, z:100) and the global `.top-nav` (~44px, `top:0`, z:50, `App.css:1104-1110`, rendered every route at `App.tsx:203`) both stick at 0 today. Set `.top-nav{top:48px}` (`:1108`) so it sits below the header, and `.db-builder-header{top:~92px}` (`:1430`, i.e. header+nav) — **not** 48px, or it hides behind the nav (z:40 < z:50); style the orphan `.rd-sheets/.rd-sheet-card/.rd-sheet-desc/.rd-sheet-sql` (`ReportDetailPage.tsx:424-466`) **and** `.rd-desc-edit` (used at `:348`, outside that range) with page max-width/padding; `className="btn btn-ghost"` at `App.tsx:156`; remove the CSS `@import` font duplication (`App.css:8`, keep `index.html` links).
- A11y/motion baseline: global `:focus-visible` ring (coral, 2px offset); `:disabled` styles for `.btn-secondary/.btn-ghost/.btn-danger` + `cursor:not-allowed`; `@media (prefers-reduced-motion: reduce)` kill-switch for all animation; a small type scale (`--text-xs/sm/base/lg/xl…`) replacing the 20-size free-for-all incrementally.
- Dead code sweep: unused `QueryHistory.tsx`, propless-`Header` dead branches + their CSS, `.welcome-section`/`.upload-panel` orphans.

### PR-C2 — Interaction primitives (React components over existing CSS)

New `frontend/src/components/ui/`: **Button** (variants + built-in busy spinner; swaps label to spinner+text automatically), **Modal** (Esc, focus trap, scroll-lock, overlay/box enter-exit animation; replaces the 9 copy-pasted shells in `DataSourcesPage.tsx:345-484`, `ReportsListPage.tsx:253-349`, `DashboardsListPage.tsx:217-312`, `ReportDetailPage.tsx:475-594`, `DashboardBuilderPage.tsx:437-566`), **Toast** (context + portal; success/error/info, auto-dismiss; replaces never-dismissing inline banners and gives silent failures a voice), **ConfirmDialog** (replaces the 5 native `confirm()`s), **Skeleton** (resurrects the dead shimmer CSS `App.css:745-762`), **EmptyState**, **Banner** (one error/success look instead of five).

Wire the loader-less async buttons through Button's busy state — census from the audit: Connect & Fetch Schema, Refresh Schema, Delete source, Create report/dashboard, Save name/desc/sheet/widget, LLM settings save, all delete flows.

### PR-C3 — Dashboard builder: professional tiles

- **Responsive grid**: `const ResponsiveGridLayout = WidthProvider(Responsive)` at module scope (`DashboardBuilderPage.tsx:3`; confirmed installed `react-grid-layout@1.5.3` exports both). Replace `width={1200}` with `breakpoints={{lg:1200, md:996, sm:768, xs:480}}` `cols={{lg:12, md:12, sm:6, xs:2}}`. **Two rewiring changes the naive swap misses (verifier — blocker):** (1) `Responsive` consumes `layouts={{lg:[...]}}` (plural), not `layout={layout}` (singular, `:413`) — reshape the controlled prop and state to `{lg: layout}`. (2) **Persistence must be gated to the `lg` breakpoint or it corrupts saved positions.** `persistLayout` is bound to `onDragStop`/`onResizeStop` (`:418-419`), which under `Responsive` deliver the *active breakpoint's* layout; on any viewport <1200px it would write md/sm/xs coordinates into the single `grid_position` column (`:149-159`). Either persist from `onLayoutChange`'s 2nd arg `allLayouts.lg`, or early-return in `persistLayout` unless the current breakpoint is `lg` (track it via `onBreakpointChange`). With that gate the `grid_position` schema is genuinely untouched; without it, "persist only lg" is false. `rowHeight={90}`, `margin={[16,16]}`; center with `.db-canvas{max-width:1600px; margin:0 auto}`.
- **RGL chrome** (zero overrides exist today): `.react-grid-placeholder { background: var(--accent-dim); border: 1px dashed var(--accent-border); border-radius: var(--radius-md); opacity: 1 }`; visible-on-hover accent resize chevrons; `resizeHandles={["se","e","s"]}`; `.react-draggable-dragging .widget-card { box-shadow: …lift; }`; subtle dot-grid background on `.db-canvas` while dragging.
- **Affordances**: whole `.widget-header` becomes the drag handle (stopPropagation on `.widget-actions`); hover elevation on `.widget-card`; new manual widgets placed at the first free slot or `y: Infinity` (kills the left-column bias, `:213-218`).
- **Widget rendering**: extract `ResultsChart`'s column **classifier** (`:29-104`, pure — column roles + numeric/time detection + multi-series, no hooks) into shared `frontend/src/lib/chartTheme.ts` and have `DashboardWidgetCard` consume it — kills the col0/col1 assumption and adds multi-series. **Two scope notes (verifier):** (a) `ResultsChart`'s palette is *hardcoded hex* (`baseColors`, `:109-112`), not CSS-var reads — making `chartTheme.ts` read computed `--ramp-*` vars is *new* behavior that also fixes `DashboardWidgetCard`'s stale indigo/slate hexes (`:62-106`); (b) `ResultsChart.autoType` only yields `bar`/`line`/`pie`, so the widget still needs its own mapping on top for `number`/`area`/`gauge`/`table` (the missing `gauge` branch, the `chart_config` override). Also: honor persisted `chart_config`; keep last data during interval refresh (drop `setLoading(true)` at `:25` → no 30s blink); add per-widget error retry button.
- **Data flow**: replace whole-page `loadDashboard()` after widget edit/delete with surgical state updates; batch layout persistence (single debounced call or Promise.all with one toast on failure — no more silent drops); widget delete via ConfirmDialog + toast.
- Stretch (only if time allows): view/edit mode toggle; duplicate-widget action; fullscreen widget.

### PR-C4 — Friendly generation experience

New `GenerationProgress` component (wraps the same `AIProgressEvent[]` array; `AIProgressTimeline` becomes the "technical details" renderer *inside* it, behind a default-off disclosure).

**Five consumers, not four (verifier):** `AIProgressTimeline` is rendered by the 4 modal flows **and** by the chat query flow at `Transcript.tsx:90` (gated by `turn.progress.length > 0`, `active={turn.status === "streaming"}`). Do **not** globally flip `AIProgressTimeline`'s expand default (today `expanded = active`, `:252-257`) or the chat live-progress collapses to a technical view. Instead wire `GenerationProgress` into **all five** sites (it renders the query-flow stage mapping below for `Transcript`); `AIProgressTimeline` keeps its current behavior as the disclosure body. The chat flow already stays mounted post-completion, so the mount-fix below applies only to the 4 modals.

Stage mapping (dashboard/report flows):

| # | Label | Enter on | Complete on |
|---|---|---|---|
| 1 | "Understanding your data" | first event | `pipeline_started` |
| 2 | "Designing your dashboard/report" | `pipeline_started` | `widget_sql_started`/`sheet_sql_started` |
| 3 | "Writing the queries" | `*_sql_started` (N from detail) | `*_sql_done` |
| 4 | "Checking everything against your data" | `validating` / `plan_summary` | last `widget`/`sheet` reaches `ok`/`dropped` |
| 5 | "Saving…" | `persisting` (PR-B1) or synthesized | terminal event |

Query flow: "Understanding your question" → "Planning the query" → "Checking the SQL" → "Running your query"; `fast_path_rejected` renders as "Thinking harder about this one…", `repairing_sql` as "Fixing a query issue (attempt N)" — **never** raw Trino/exception text in friendly mode.

Rules: stage rows reuse the `spinner/✓/⚠` vocabulary; during stage 2 (no checkpoints) animate a rotating sub-caption off the `llm`/`tool` activity so it never looks stalled; per-widget sub-rows under stage 4 pre-rendered from `plan_summary` ("Checking 3 of 5"); one total-elapsed chip, no per-row timing in friendly view; friendly error copy from a client-side map keyed on stage/`status_code` (raw detail only in technical view).

Fixes bundled in: keep the progress UI mounted after completion/error in all four modal flows (today `{generating && …}` unmounts it instantly); pass an `AbortSignal` through `streamAIOperation` and add a Cancel button to generation modals; non-streaming fallback shows the staged view in indeterminate mode with "usually takes about a minute".

### PR-C5 — Motion & immersion pass

- Route transitions: fade/slide on page swap (`App.tsx:205-213`), CSS only.
- Skeletons (from PR-C2) replace all six centered page spinners (lists, builder, detail, settings); staggered card entrance on `ds-grid`/`dl-grid`/dashboard tiles (~30ms delay steps).
- Micro-interactions: KPI number count-up on load; chart entrance via echarts' built-in animation config; button press scale (`:active` on all interactive classes — audit lists 9 missing); toast slide-in/out; modal enter/exit (needs mount-aware close in the PR-C2 Modal).
- Everything behind the `prefers-reduced-motion` guard from PR-C1.

---

## Latency budget (typical 6-widget dashboard / 5-sheet report)

| Stage | Today | After |
|---|---|---|
| Context bundle | 1-2s serial | ~1s (gathered) |
| Design | 15-30s (ReAct turns + structured-output step, temp 1.0) | 5-8s (one JSON call, temp 0.1) |
| SQL generation | 10-25s (batched — unchanged) | 10-20s |
| Schema-analyst detour | +10-20s when triggered | eliminated |
| Validation | 6-18s sequential full queries | 1-3s parallel probes (Core API) |
| Repairs | 10-25s serial per-item, full-catalog rebuilds | ≤8s one batched call |
| Rate-limit backoff | multiplies all of the above | ~halved (½ the calls) |
| **Total** | **70-100s** | **~20-35s** |

Measure, don't trust: per-call `duration_ms` already lands in SSE events and the depth profile (`orchestrator.py:250`); assert after PR-A1/B1 that a report run stays ≤3 LLM calls and log the stage timings.

---

## Suggested build order (parallel subagents)

- **Wave 1 (parallel)**: PR-A1 (ai-engine) ✅ done, PR-B1 (core-api) ✅ done (its deferred
  batched-repair driver was completed by PR-B2), PR-C1 (frontend foundation) — not started.
  Independent services, no file overlap.
- **Wave 2 (parallel)**: PR-A2 ✅ done (2026-07-21), PR-B2 ✅ done (2026-07-21), PR-C2 — not
  started (blocked behind PR-C1, which is itself not started — see Wave 1).
- **Wave 3**: PR-C3 and PR-C4 **cannot run in parallel** — both rewrite `DashboardBuilderPage.tsx` (C3: the `GridLayout`/`persistLayout`/widget-CRUD blocks; C4: the AI-refine modal in the same file, `:437-487`). Run **C3 then C4** sequentially, or split by file: give C4 the list/detail pages + `Transcript` + the new `GenerationProgress`, and fold C4's `DashboardBuilderPage` refine-modal edits into C3's scope (C3 already owns that file). Everything else across services in earlier waves is genuinely collision-free.
- **Wave 4**: PR-C5 + docs housekeeping (tick `platform-redesign.md` §13, update its status header, add new SSE events to `sse-events.md`).

**Must-land set (corrected):** the minimum that resolves the core complaints (b)/(c)/(d)/(f) is **A1 + B1 + C1 + C2 + C3 + C4** — C1 and C2 are *not* droppable, because C3 hard-depends on C2 primitives (widget-delete `ConfirmDialog`, layout-failure `Toast`, per-widget error `Button`/retry) and C1 is C2's token/bug base. The droppable-for-time set is **C5** (pure motion polish) and the C3/C4 stretch items (view/edit toggle, duplicate widget, fullscreen, count-up). If time is *very* short, a legitimately reduced C1 is "bug fixes + focus-visible only, skip the CSS reorg."

Verification per PR: `docker compose up --build` smoke (no local `go`/toolchains); generate one dashboard + one report end-to-end; confirm SSE stream renders friendly stages; drag/resize/persist a widget; keyboard-tab across a page and see focus rings.

## Risks

| Risk | Mitigation |
|---|---|
| Single-call design quality < ReAct design quality on complex briefs | Few-shots (once `render_extra_context` actually renders them, PR-A1); confidence field already returned — gate: below threshold, retry once with the full catalog appended; keep prompts' reasoning scaffold ("think through sections first") inside the one call |
| Core-API probe uses a full `SELECT` rather than a plan-only validate | `SELECT * FROM (<sql>) LIMIT 25` is cheap and also yields the shape PR-B2 needs; a plan-only `EXPLAIN` would be lighter but the AI Engine can't run it (SQL-execution boundary), and connector support for `EXPLAIN (TYPE VALIDATE)` is unproven in this stack — the probe is the safe portable choice |
| Responsive breakpoints reshuffle existing saved layouts | Feed `layouts={{lg: layout}}` and gate persistence to the `lg` breakpoint (PR-C3) so md/sm/xs edits never write `grid_position`; existing data reads unchanged only *with* that gate |
| Batched repair fixes one item, breaks alignment | Title-keyed alignment (PR-A1); the batch prompts already emit `title`. Strict `json_schema` exact-count would be stronger but is **net-new and unproven on the default Gemini OpenAI-compat endpoint** (no `json_schema` call exists anywhere in the codebase today) — treat as a follow-up, not a dependency. **✅ Implemented as designed in PR-A2/B2**: title-keyed with positional fallback on both the ai-engine parser and the Core API matcher; live-tested with real batches. |
| CSS reorganization regresses pages untouched by this plan | Keep class names stable; reorganization moves rules, doesn't rename; visual spot-check all 7 routes in the smoke pass |
| `run_trino_query` silently returns `[]` on Trino failures | Prerequisite fix in PR-A2 (reorder the terminal-state check); add a test that a `FAILED` query raises. **✅ Fix landed in PR-A1; test coverage added in PR-A2** (`ai-engine/tests/test_common.py`). |

## Open items (defaults chosen, flag if wrong)

1. Dashboard/report generation keeps sharing `_plan_semaphore` (cap 4) with chat — acceptable for now; revisit if chat load visibly delays generations.
2. Deepagents escalation path for dashboards/reports is **deleted**, not kept as fallback — the structured pipeline + one batched repair is strictly more predictable; the code stays in git history.
3. AI provenance (`explanation`, `confidence`, `dropped_*`) remains transient (shown once post-generate) — persisting it needs a schema change; deferred.
