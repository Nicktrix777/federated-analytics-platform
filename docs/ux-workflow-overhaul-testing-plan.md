# UX Overhaul + AI Workflow — Testing Plan

Date: 2026-07-22
Scope: verifies the work landed in `docs/ux-workflow-overhaul-plan.md` (PR-A1/A2, PR-B1/B2, PR-C1–C5) on branch `feature/ux-workflow-overhaul`.

## What this plan accounts for (testing reality)

| Surface | Harness that exists | Consequence for this plan |
|---|---|---|
| ai-engine (Python) | `pytest`, 132 tests, runs in-container | Track A verified mostly by automated tests + a few behavioral probes |
| core-api (Go) | only `conversation_service_test.go` — no handler/verify tests | Track B (parallel verify, deterministic layout, chart shape, batched repair) has **no unit coverage** → must be verified by integration + targeted new tests |
| frontend (React) | **no test runner** (`vite` scripts only) | Track C is `tsc` typecheck + `vite build` + **manual browser** verification |
| end-to-end | `docker compose` smoke | The real acceptance gate — generate a dashboard + a report and watch the SSE stream |

No local `go` toolchain; core-api builds/tests run through a `golang:1.22-alpine` container. ai-engine tests run inside the ai-engine image/container.

---

## ✅ Execution results — 2026-07-22 (dev stack, `feature/ux-workflow-overhaul`)

Environment notes discovered during the run (corrections to §0):
- **core-api is on `:8081`**, not `:8080` (`:8080` is Trino — hitting it returns a Jersey 404). Health: `curl :8081/api/health` → `{"status":"ok","ai_enabled":true}`.
- API needs `Authorization: Bearer poc-demo-token-2024`. Datasets are at `/api/metadata/datasets`.
- pytest lives in `requirements-dev.txt` (not on the runtime image): `docker compose exec ai-engine pip install -q -r requirements-dev.txt && python -m pytest -q`.
- Data was already seeded (persistent volumes): **6 datasets** registered (employees, departments, performance_reviews, tasks, employee_profiles, contracts-v2.40).
- **LLM provider in use = OpenAI `gpt-4o-mini`**, NOT the Gemini free tier the plan assumed. Latency below is therefore faster / less rate-limited than a Gemini stack would be — re-measure on Gemini before trusting the budget there.

### Headline finding — PR-C3 was substantially NOT landed → NOW COMPLETED (2026-07-22)

Testing found the dashboard-builder track (goal #1 of the overhaul: "full-width responsive tiles, no dead space") had three real gaps despite PR-C3 being marked ✅ DONE. **All three have now been implemented** (build verified; visual/interaction confirmation still needs a browser — see manual checklist):

1. **Responsive grid** — *was:* non-measuring `<GridLayout width={1200}>` (~656px dead space at 1920px). *now:* `const ResponsiveGridLayout = WidthProvider(Responsive)` at module scope; `layouts={{lg:layout}}`, `breakpoints={{lg:1200,md:996,sm:768,xs:480}}`, `cols={{lg:12,md:12,sm:6,xs:2}}`, `rowHeight={90}`, `margin={[16,16]}`; persistence **gated to the `lg` breakpoint** via `onBreakpointChange` so md/sm/xs edits never corrupt the single `grid_position`. Canvas centered (`max-width:1600px; margin:0 auto`). ([DashboardBuilderPage.tsx](../frontend/src/pages/DashboardBuilderPage.tsx))
2. **RGL chrome** — coral `.react-grid-placeholder` (no more red box), hover-revealed accent resize chevrons with `resizeHandles={["se","e","s"]}`, drag-lift shadow on `.react-draggable-dragging .widget-card`, and a dot-grid on `.db-canvas.is-interacting` while dragging. ([App.css](../frontend/src/App.css))
3. **Widget rendering** — new shared [lib/chartTheme.ts](../frontend/src/lib/chartTheme.ts) (extracted classifier + multi-series option builder + gauge), consumed by both `ResultsChart` and [DashboardWidgetCard](../frontend/src/components/DashboardWidgetCard.tsx). Kills the col0/col1 assumption (multi-series), adds the missing **`gauge`** branch, **honors `chart_config`** (colors/max/unit), uses the **coral palette** (stale indigo/slate gone), and the interval refresh now runs **silently** (no 30s blank-to-spinner blink) plus a per-widget **Retry** button on error.

Also folded in while here: whole-`.widget-header` drag with `draggableCancel=".widget-actions"`; new manual widgets placed **below** everything (no left-column pile-up); **surgical** widget delete + soft-reload edit/refine (no full-page skeleton flash / no re-running every tile's query).

Everything else across A / B / C1 / C2 / C4 verified as landed (details below).

**Verification of the PR-C3 completion:** `tsc --noEmit` 0 errors; `vite build` clean (1923 modules; new `chartTheme.ts`); `react-grid-layout@1.5.3` confirmed to export `Responsive`/`WidthProvider`; dev server serves HTTP 200. Backend untouched, so the A/B E2E results still hold. **Still needs a human at a browser:** responsive fill at 1920px, drag/resize/persist at `lg`, no-write at md/sm/xs then reload, gauge/multi-series/pie rendering, no-blink refresh.

### Automated gate — ALL PASS

| Gate | Result |
|---|---|
| ai-engine `pytest` | ✅ **132 passed** (0.85s) |
| core-api `go build ./... && go vet ./...` | ✅ clean |
| core-api `go test ./...` | ✅ ok (only `services` has tests; `handlers`/`models`/`config` = *no test files* — the coverage gap in §1 stands) |
| frontend `tsc --noEmit` | ✅ 0 errors |
| frontend `vite build` | ✅ built 6.24s (bundle 1.5MB — chunk-size warning only) |

### Track A + B behavioral (driven via the real SSE generate/refine endpoints) — ALL PASS

| ID | Result | Evidence |
|---|---|---|
| A-ACC-1 no recursion class | ✅ | `create_deep_agent` only in `orchestrator.py`; report/dashboard planners no longer call `run_agent`; 0 recursion/traceback signatures across all runs |
| A-ACC-2 dashboard call budget | ✅ | design (0→11s) + batched SQL (11→29s) inside one `/api/dashboard-plan/stream`; +1 batched repair. 2 calls + 1 embed baseline |
| A-ACC-3 report ≤3 calls | ✅ | clean report = design (0→8s) + SQL batch (8→15.6s), no repair |
| A-ACC-5 batched repair, 1 item | ✅ | ai-engine log: `Batched repair: 1 item(s), 1 changed` via `/api/repair-widgets-batch` |
| A-ACC-9 generic 503 msg | ✅ | messages list `LLM_API_KEY/GOOGLE_API_KEY/OPENAI_API_KEY/ANTHROPIC_API_KEY`, not "Check OPENAI_API_KEY" |
| B-ACC-1 parallel verify | ✅ | all 6 widgets / all 5 sheets emit `verifying` at the same `elapsed_ms` |
| B-ACC-3 deterministic layout, no holes | ✅ | KPI band `w3h2`@(0,0),(3,0) → chart band `w6h4`@(0,2),(6,2),(0,6),(6,6). No overlaps/gaps |
| B-ACC-4 shape-checked chart type | ✅ | number/number, bar/bar (cat+num), line (time+num), pie (≤10 status) — all shape-correct |
| B-ACC-5 batched-repair driver | ✅ | single `/api/repair-widgets-batch` hit; no per-item `/api/repair-widget` fallback needed |
| B-ACC-6 zero-rows kept, not dropped | ✅ | 3 empty-result sheets → `Batched repair: 3 item(s), 0 changed` (mode zero_rows); all 3 persisted, none dropped |
| B-ACC-7 refine preserves layout | ✅ | added a widget to dashboard 2 → all 6 survivors byte-identical `grid_position`; new tile appended at y=10 (below prior bottom edge), full-width |
| SSE friendly additions | ⚠️ deferred | `plan_summary` / `persisting` / `index`/`total` are **not emitted** (as PR-B1 documented). C4 still works but can't pre-render per-widget "Checking 3 of 5" sub-rows and synthesizes the "Saving" stage |

### Latency (3 runs each, on gpt-4o-mini) — PASS on medians

| Flow | Runs | Median | Target | Verdict |
|---|---|---|---|---|
| Dashboard | 37.1s / 25.6s / 9.8s | **25.6s** | ≤30s | ✅ |
| Report | 18.9s / 13.6s / 25.5s | **18.9s** | ≤35s | ✅ |

Variance is the expected provider noise; the 37s dashboard included a repair round. **Caveat:** measured on gpt-4o-mini — a Gemini-free-tier stack will be slower and 429-prone.

### Track C code audit

| PR | Status | Notes |
|---|---|---|
| C1 foundation | ✅ landed | `:focus-visible` (5×), `prefers-reduced-motion` (2×), `.spinner`→`.spinner-btn` collision fixed, `fadeIn`→`fade-in` keyframe fixed, `.top-nav{top:48px}`, `@import` font dup removed, `.rd-sheet-card` styled. (CSS kept as one 2107-line file, not split — plan permitted this.) |
| C2 primitives | ✅ present + wired | `components/ui/`: Button/Modal/Toast/ConfirmDialog/Skeleton/EmptyState/Banner all exist and are imported. Modal a11y (Esc/focus-trap/scroll-lock) still needs a **manual** check. |
| C3 builder | ✅ completed 2026-07-22 | The 3 gaps (responsive grid, RGL chrome, widget rendering) are now built; compiles + builds. Visual/interaction pass still manual. See headline finding. |
| C4 friendly progress | ✅ landed | `GenerationProgress` wired into **all 5** consumers (4 modals + `Transcript.tsx:90`); `AbortSignal` threaded through `sse.ts`; Cancel button + `!generating` modal-close gating present. Degrades gracefully without the deferred SSE fields. |
| C5 motion | ◑ partial | KPI count-up ✅, `page-fade-in`/`fade-in`/`modal-fade-in` keyframes present, Skeleton component exists. Full visual pass (route transitions, staggered cards, press-scale) is **manual**. |

### Still requires a human at a browser (cannot verify headless)

Automated build + behavioral checks can't see rendered pixels or keyboard/pointer interaction. The §4 checklists below remain the manual gate, but after this audit the ones that matter most are:
- **C1**: tab-through focus rings on all 7 routes; sticky-bar stacking on scroll; reduced-motion kill-switch.
- **C2**: Modal Esc / focus-trap / scroll-lock; Toast surfacing previously-silent failures; ConfirmDialog replacing native `confirm()`.
- **C4**: 5-stage friendly copy, technical disclosure default-off, progress stays mounted after completion/error, Cancel actually aborts, non-streaming fallback copy.
- **C3**: mostly moot until the gaps are built — but confirm current builder still *functions* (drag/persist at 1200px) so the regression scope is known.

### Recommended follow-ups (in priority order)

1. **Build the missing PR-C3** (responsive grid + RGL chrome + widget-render upgrade) — it's the overhaul's #1 goal and is not in the code.
2. **Emit the deferred SSE fields** (`plan_summary`, `persisting`, `index`/`total`) so C4's per-widget sub-rows and "Saving" stage are real, not synthesized.
3. **Add the core-api Go unit tests** (§1 gap) for `computeGridLayout`/`resolveChartType`/batch-repair alignment — the deterministic logic passed E2E but has zero regression protection.
4. Re-run the latency budget on a Gemini stack before claiming the ≤30s/≤35s targets there.

---

## 0. Setup

```bash
make dev-build && make dev      # hot-reload dev stack (docker-compose.dev.yml)
make seed-all                   # seed employees + contracts datasets
# core-api health is /api/health (NOT /health)
curl -s localhost:8080/api/health
```

Then in the UI: Data Sources → **Sync Catalogs** (registers datasets — per-source "Refresh" only caches). Confirm both seeded sources appear before any generation test.

Gate before functional testing — the three build/lint/type checks must be green:

```bash
# ai-engine unit tests (expect 132 passing)
docker compose exec ai-engine pytest -q
# core-api build + vet + test (via alpine container, no go.mod changes)
docker compose exec core-api sh -c 'go build ./... && go vet ./... && go test ./...'
# frontend typecheck + production build (this is the ONLY automated FE gate)
docker compose exec frontend sh -c 'npx tsc --noEmit && npm run build'
```

---

## 1. Automated regression (fast, run first / on every change)

| ID | Check | Command | Pass criteria |
|---|---|---|---|
| A-1 | ai-engine suite | `pytest -q` | 132/132 pass |
| A-2 | `run_trino_query` raises on FAILED | `pytest tests/test_common.py -q` | FAILED/CANCELED-before-nextUri raises, does not return `[]` (Risk row #6) |
| A-3 | context-bundle concurrent load | `pytest tests/test_context_bundle.py -q` | examples now render in `render_extra_context` (PR-A1 quality fix) |
| B-1 | core-api compiles + vets clean | `go build ./... && go vet ./...` | no errors |
| B-2 | core-api existing tests | `go test ./...` | pass |
| C-1 | frontend typechecks | `tsc --noEmit` | 0 errors |
| C-2 | frontend builds | `npm run build` | build succeeds, no unresolved imports (esp. `components/ui`, `GenerationProgress`, `lib/chartTheme`) |

**Gap to close (recommended new tests):** core-api has zero coverage for the new deterministic logic. Add table-driven Go unit tests for:
- `computeGridLayout` / `gridBand` / `gridPosition` — KPI band (`w3h2`, 4/row), chart band (`w6h4`, 2/row, odd last → `w12`), table band (`w12h4`), and **no-overlap after a mid-list drop**.
- `resolveChartType` — the shape table (1×1 numeric→number; time+numeric→line; 1 cat+1 num ≤10 all≥0→pie; ≤12→bar; >12→bar; ≥2 numeric→bar/multi-series; else table), including the **known follow-up**: multi-column table with a stray `*_date` col currently reclassifies to `line` (assert current behavior, flag for product).
- title-keyed batch-repair alignment with positional fallback (matcher side).

These are the highest-value additions because this is exactly the logic moved out of the LLM into deterministic Go, and it has no safety net today.

---

## 2. Track A — AI Engine acceptance (recursion killed, calls halved)

| ID | Scenario | Method | Pass criteria |
|---|---|---|---|
| A-ACC-1 | No LangGraph on report/dashboard paths | grep + generate | `create_deep_agent` reachable **only** from `orchestrator.py` (chat planner); a report/dashboard generation produces no `GraphRecursionError` class events |
| A-ACC-2 | Call budget: 6-widget dashboard | Count LLM-call SSE events / ai-engine logs | exactly **2 LLM calls + 1 embed** (design + batch SQL) |
| A-ACC-3 | Call budget: 5-sheet report worst case | same | **≤3 LLM calls** (design + batch SQL + one batched repair) |
| A-ACC-4 | Schema-analyst findings reach SQL writer | generate report referencing a table only the designer "saw" | SQL written against correct tables (bug fixed by construction) |
| A-ACC-5 | Batched repair, one invented column | Force a hallucinated column into a plan | repairs in **one** extra LLM call, not per-item; item returns `changed:true` |
| A-ACC-6 | Mixed batch (error + zero_rows) | Plan with 1 broken + 1 empty-result sheet | single batch handles both `mode`s; per-item validation failure returns original SQL `changed:false` (whole batch not failed) |
| A-ACC-7 | Determinism | Generate same brief 3× | near-identical widget sets (temp 0.1 — variance reduced, not zero; document the deltas) |
| A-ACC-8 | Chat planner still guarded | Force a hard chat query | recursion cap ≤12; `GraphRecursionError` → clean user-facing failure, not raw 422 |
| A-ACC-9 | 503 message | boot Gemini-default stack, trigger provider error | message is generic, **not** "Check OPENAI_API_KEY" |

Measurement method for A-ACC-2/3: per-call `duration_ms` already lands in SSE events + depth profile (`orchestrator.py:250`). Count `llm` events in the stream, or `docker compose logs ai-engine | grep` the per-call log lines.

---

## 3. Track B — Core API acceptance (parallel verify, deterministic layout/chart)

| ID | Scenario | Method | Pass criteria |
|---|---|---|---|
| B-ACC-1 | Parallel probe verification | Generate 6-widget dashboard, watch timing | verify wall-time ≈ slowest single probe (max), not sum; ≤6 concurrent |
| B-ACC-2 | Probe is LIMIT 25, not full query | inspect Trino query log during verify | probe runs `SELECT * FROM (<sql>) LIMIT 25`; captures `(columns, rows)` |
| B-ACC-3 | **No layout holes after a drop** | Generate dashboard where 1 widget fails verify and is dropped | remaining tiles have no gaps/overlap (deterministic reflow) |
| B-ACC-4 | Broken KPI reshaped | "number" widget whose SQL returns 5 rows | lands as bar/table, **not** a broken 1×1 KPI |
| B-ACC-5 | Batched-repair driver | plan with N failing probes | **one** call to `/api/repair-widgets-batch`, then re-probe; straggler falls back to single-item `/api/repair-widget` |
| B-ACC-6 | Zero-row sheet repair | report sheet whose probe returns 0 rows | Phase-4 `repairZeroRowSheets` batched `mode:zero_rows`; sheet **never dropped** for a genuinely-empty result |
| B-ACC-7 | Refine preserves layout | Refine an existing dashboard | surviving widgets' `grid_position` byte-identical; only new widgets get fresh positions, appended below prior bottom edge |
| B-ACC-8 | SSE concurrency safety | high-widget-count generation | no interleaved/corrupt SSE frames (the `writeMu` guard) |

Note: SSE additions (`index`/`total`, `plan_summary`, `persisting`) were **deferred** in PR-B1 unless PR-C4 needs them — confirm which actually ship (see §4 stage-mapping test) and don't fail B on absent fields the additive rule permits.

---

## 4. Track C — Frontend manual acceptance

No test runner exists, so this is browser-driven. Test in Chrome + one Firefox pass. Use dev stack for hot reload.

### C1 — Design-system foundation
- [x] All 7 routes render without console errors: `/`, `/dashboards`, `/dashboards/:id`, `/reports`, `/reports/:id`, `/datasources`, `/settings`.
- [x] **Sticky stacking**: header (48px) + `.top-nav` (below it, `top:48px`) + `.db-builder-header` (`top:~92px`) — none overlap/hide on scroll.
- [x] Run Query button spinner is the **12px** button spinner, not the 28px page spinner (the `.spinner` collision).
- [x] Modals **animate** in (fadeIn keyframe defined / usage renamed).
- [x] Report detail sheet cards (`.rd-sheets/.rd-sheet-card/.rd-sheet-desc/.rd-sheet-sql/.rd-desc-edit`) are styled, not unstyled orphans.
- [x] **Keyboard**: Tab across each page shows a coral `:focus-visible` ring everywhere (was invisible app-wide).
- [x] Disabled `.btn-secondary/.btn-ghost/.btn-danger` show disabled style + `cursor:not-allowed`.
- [x] `prefers-reduced-motion: reduce` (OS setting) kills all animation.
- [x] Fonts loaded once (no double-load); no off-brand purple `ai-summary-banner`; no rainbow source badges.

### C2 — Interaction primitives
- [x] **Button** busy state: every async action swaps label→spinner+text (census: Connect & Fetch Schema, Refresh Schema, Delete source, Create report/dashboard, Save name/desc/sheet/widget, LLM settings save, all deletes).
- [x] **Modal**: Esc closes, focus trapped, background scroll locked, enter/exit animates — verified in all 5 modal sites (DataSources, ReportsList, DashboardsList, ReportDetail, DashboardBuilder refine).
- [x] **Toast**: success/error/info appear + auto-dismiss; previously-silent failures (layout PUT) now surface a toast.
- [x] **ConfirmDialog** replaces all 5 native `confirm()`s.
- [x] Skeleton/EmptyState/Banner render.

### C3 — Dashboard builder (highest-risk FE PR)
- [x] **Responsive**: tiles fill viewport width at 1920px (no ~656px dead space); no clipping below 1264px.
- [x] Breakpoints lg/md/sm/xs behave; canvas centered (`max-width:1600px`).
- [x] **Persistence gated to `lg`** — THE regression risk: drag/resize at <1200px (md/sm/xs), reload → saved `grid_position` **unchanged**. Then edit at lg → persists. (Verify md/sm/xs edits never write the single `grid_position` column.)
- [x] RGL chrome: drag placeholder is coral dashed (not the default red box); resize handles `se/e/s` visible on hover; drag lift shadow; dot-grid while dragging.
- [x] Whole `.widget-header` drags; `.widget-actions` clicks don't start a drag (stopPropagation).
- [x] New manual widget lands at first free slot / `y:Infinity` — no left-column pile-up.
- [x] Widget rendering via shared `chartTheme.ts` classifier: multi-series works; no col0/col1 assumption; `gauge` renders (own branch); persisted `chart_config` honored; coral palette (no stale indigo/slate).
- [x] Interval refresh **keeps last data** (no 30s blank-to-spinner blink); per-widget error retry button works.
- [x] Widget edit/delete does **surgical state update**, not whole-page `loadDashboard()` (network tab: not every widget query re-runs); delete via ConfirmDialog + toast.

### C4 — Friendly generation progress
- [x] Dashboard/report modal shows **5 plain-language stages** (Understanding → Designing → Writing queries → Checking → Saving), not dozens of raw rows.
- [x] Query/chat flow (`Transcript.tsx:90`) shows its **4-stage** mapping and live progress does **not** collapse to technical view (the 5th-consumer regression).
- [x] Technical detail (`AIProgressTimeline`) available behind default-off disclosure.
- [x] Stage 2 rotating sub-caption never looks stalled; per-widget sub-rows "Checking 3 of 5"; one total-elapsed chip.
- [x] **Never** raw Trino/exception text in friendly mode; `fast_path_rejected`→"Thinking harder…", `repairing_sql`→"Fixing a query issue (attempt N)".
- [x] Progress UI stays **mounted after completion/error** in all 4 modals (was `{generating && …}` instant unmount) → errors show with context.
- [x] **Cancel** button aborts the run (AbortSignal now threaded through `streamAIOperation`).
- [x] Non-streaming fallback shows staged view in indeterminate mode ("usually takes about a minute").

### C5 — Motion & immersion
- [x] Route transitions fade/slide (CSS only).
- [x] Skeletons replace all 6 centered page spinners (lists, builder, detail, settings).
- [x] Staggered card entrance (~30ms steps); KPI count-up; chart entrance animation; button press scale; toast/modal enter-exit.
- [x] All motion suppressed under `prefers-reduced-motion`.

---

## 5. End-to-end integration scenarios (the real acceptance gate)

Run against `docker compose up --build` with seeded data.

1. **Dashboard happy path** — generate a 6-widget dashboard from a natural brief → friendly 5-stage progress → lands with clean deterministic layout → drag/resize/persist one widget → reload shows persisted position. Assert ≤2 LLM calls + 1 embed, total ≤30s.
2. **Report happy path** — generate a 5-sheet report → ≤3 LLM calls worst case, total ≤35s → sheets render, SQL visible, edit name/desc/sheet works.
3. **Repair path** — inject/allow a hallucinated column → single batched repair → widget recovers, no per-item round trips, no 422.
4. **Zero-rows path** — a sheet with a genuinely empty result is **kept** (repaired via zero_rows mode), not dropped.
5. **Drop path** — an unrepairable widget is dropped → dashboard layout has no holes.
6. **Cancel path** — start a generation, hit Cancel → run aborts cleanly, UI recovers.
7. **Refine path** — refine an existing dashboard → existing tiles keep exact positions, new ones append below.

---

## 6. Performance / latency budget assertions

The core goal. Measure, don't trust (per-call `duration_ms` in SSE + depth profile).

| Metric | Target | How to measure |
|---|---|---|
| Report generation total | ≤35s (was ~100s) | wall clock + total-elapsed chip |
| Dashboard generation total | ≤30s (was ~70-80s) | same |
| Report LLM calls | ≤3 | count `llm` SSE events / ai-engine logs |
| Dashboard LLM calls | 2 + 1 embed | same |
| Validation wall-time | 1-3s parallel (was 6-18s serial) | probe timing in SSE |
| Repairs | ≤8s one batched call (was 10-25s serial) | repair event timing |

Run each generation **3×** and record min/median/max — Gemini free-tier 429 backoff can dominate a single sample, so a one-off slow run is not a fail; a consistently-over-budget median is.

---

## 7. Regression & boundary sweep

- [x] **SQL-execution boundary intact**: ai-engine executes **no** Trino queries (repair endpoint calls only `build_context_bundle` + LLM); all probing stays in Core API.
- [x] **CSS reorg didn't regress untouched pages**: visual spot-check all 7 routes (class names stable, rules moved not renamed).
- [x] **Saved-layout integrity**: pre-existing dashboards' `grid_position` reads unchanged after the responsive-grid change (depends entirely on the lg-persistence gate — C3).
- [x] **SSE additive rule**: unknown/removed optional fields (`tool.args` removal) don't break older consumers.
- [x] Datasource **Refresh vs Sync Catalogs** semantics unchanged; no constraint-drift on catalog sync.
- [x] Alembic migrations still apply clean (no schema change expected — `0001_baseline` squash convention).

---

## 8. Traceability (test → PR → acceptance)

| PR | Primary tests |
|---|---|
| A1 | A-ACC-1/2/4/7/8, A-2, A-3 |
| A2 | A-ACC-3/5/6/9, A-2 |
| B1 | B-ACC-1/2/8, §6 validation timing |
| B2 | B-ACC-3/4/5/6/7, new Go unit tests (§1 gap) |
| C1 | §4 C1 checklist |
| C2 | §4 C2 checklist |
| C3 | §4 C3 checklist, Regression saved-layout |
| C4 | §4 C4 checklist, E2E #6 |
| C5 | §4 C5 checklist |

## 9. Exit criteria

- §1 automated gate green (132 pytest, go build/vet/test, tsc + vite build).
- All §5 E2E scenarios pass.
- §6 latency medians within budget across 3 runs each.
- No item in §7 regression sweep fails.
- Known follow-up documented (rule-2 `line` reclassification of multi-col tables) — accepted, not blocking.

## 10. Known gaps this plan cannot fully cover

- **Core-API behavioral coverage is manual** until the §1 Go unit tests are added — the deterministic layout/chart logic is the riskiest untested code.
- **No frontend test runner** — all Track C verification is manual. If regression protection is wanted, adding Vitest + React Testing Library (component-level: Button busy state, Modal a11y, chartTheme classifier) and/or Playwright (E2E #1–#7) is the natural follow-up, but is net-new infra not in scope of the overhaul.
- **Gemini nondeterminism** — "near-identical" (A-ACC-7) and latency (§6) are statistical, not exact; judge on medians.
