# SSE Event Contract — AI Operations

All AI operations can stream progress as Server-Sent Events. The AI Engine
emits the pipeline events; the Core API proxies them to the frontend and
appends its own execution/verification events. Every `data` payload is JSON
and always includes `elapsed_ms` (ms since that service started handling the
request) and, when provided, `request_id`.

Wire format per event:

```
event: <type>
data: {"...": "...", "elapsed_ms": 1234, "request_id": "..."}

```

Comment frames (`: heartbeat`) are sent every 15s of silence to keep proxies
from closing idle connections — clients must ignore them.

## AI Engine endpoints

`POST /api/plan/stream` (body: PlanRequest),
`POST /api/dashboard-plan/stream` (body: DashboardPlanRequest) and
`POST /api/report-plan/stream` (body: ReportPlanRequest).
Optional `X-Request-ID` header is echoed into every event.

| event | payload fields | meaning |
|---|---|---|
| `stage` | `stage`, `detail?` | Pipeline checkpoint. Stages: `schema_retrieval` (detail = "selected N/M datasets by relevance"; emitted when schema-RAG trims the catalog), `fast_path_started`, `fast_path_rejected` (detail = reason), `pipeline_started`, `subagent_started`, `subagent_finished`, `widget_sql_started`, `widget_sql_done`, `sheet_sql_started`, `sheet_sql_done`, `validating`, `persisting` |
| `llm` | `phase` ("start"/"end"), `agent`, `model?`, `duration_ms?`, `input_tokens?`, `output_tokens?` | One LLM call inside the pipeline. `agent` is who made it: `fast-path`, `query-planner`, `dashboard-designer`, `report-designer`, `schema-analyst`, `sql-generator` |
| `tool` | `phase` ("start"/"end"), `tool`, `agent`, `args?`, `duration_ms?` | One agent tool call (schema lookups etc.) |
| `plan` | `plan` (full QueryPlan JSON), `path` ("fast"/"full") | Terminal success event of `/api/plan/stream` (plan produced) |
| `clarification` | `question`, `options` (string[]), `kind` | Terminal event of `/api/plan/stream` (clarification needed — PR5) |
| `dashboard_plan` | `plan` (full DashboardPlan JSON) | Terminal success event of `/api/dashboard-plan/stream` |
| `report_plan` | `plan` (full ReportPlan JSON) | Terminal success event of `/api/report-plan/stream` |
| `error` | `detail`, `status_code` | Terminal failure event |

The stream closes after the terminal event (`plan`, `clarification`,
`dashboard_plan`, `report_plan`, or `error`). Exactly one terminal event is
sent per request.

## Core API endpoints (proxy + execution events)

`POST /api/query/stream` (body: same as `/api/query`, AI mode),
`POST /api/dashboards/generate/stream`, `POST /api/dashboards/:id/refine/stream`,
`POST /api/reports/generate/stream`, `POST /api/reports/:id/refine/stream`.
Same auth as the non-streaming routes. The Core API:

1. Generates/receives the request ID and forwards it to the AI Engine as
   `X-Request-ID`.
2. Pipes every AI Engine event through **verbatim** (same `event:` name, same
   `data:`), except the AI Engine's terminal event, which it consumes.
3. Appends its own events:

| event | payload fields | meaning |
|---|---|---|
| `stage` | `stage`: `executing_sql` | Plan accepted, SQL sent to the Query Service |
| `stage` | `stage`: `repairing_sql`, `detail`: `"attempt N/M: <error first line>"` | AI-generated SQL failed; repair attempt in progress. Emitted up to `maxQueryRepairAttempts` times per request (PR6) |
| `stage` | `stage`: `zero_rows_retry` | SQL ran fine but returned zero rows; trying filter-literal correction (PR6) |
| `widget` | `title`, `status` ("verifying"/"repairing"/"ok"/"dropped"), `attempt?`, `detail?`, `index?`, `total?` | Dashboard flows: per-widget verify/repair progress |
| `sheet` | `title`, `status` ("verifying"/"repairing"/"ok"/"dropped"), `attempt?`, `detail?`, `index?`, `total?` | Report flows: per-sheet verify/repair progress |
| `plan_summary` | `titles` (string[]), `total` (number) | Emitted before validation to pre-render the checklist |
| `result` | full QueryResponse JSON (same shape as `POST /api/query`) | Terminal success event of `/api/query/stream` |
| `clarification` | full QueryResponse JSON (with `clarification` field set, empty rows) | Terminal event of `/api/query/stream` (clarification needed — PR5; also emitted when repair is exhausted — PR6) |
| `dashboard` | the persisted dashboard JSON incl. `id` (same shape as the non-streaming response) | Terminal success event of the dashboard streams |
| `report` | the persisted report JSON incl. `id` (same shape as the non-streaming response) | Terminal success event of the report streams |
| `error` | `detail`, `status_code` | Terminal failure event |

> **PR6 note:** The streaming path (`/api/query/stream`) now runs the same
> repair loop as the blocking path (`/api/query`). Both paths emit
> `repairing_sql` stage events during repair attempts and `zero_rows_retry`
> when attempting filter-literal correction. A heartbeat comment frame is sent
> every 15s during the Core-API execution/repair phase (the AI Engine's own
> heartbeats stop when its stream closes). Repair-exhausted failures produce a
> `clarification` terminal event rather than an `error` — the user can retry
> with clarified intent.

## Client notes

- These are POST endpoints, so native `EventSource` (GET-only) cannot be
  used — consume with `fetch` + a ReadableStream SSE parser.
- Treat any terminal event as end-of-operation; then close/cancel the stream.
- Unknown event types must be ignored (forward-compatibility).
- The non-streaming endpoints remain available and return the identical final
  JSON; streaming is additive, not a replacement.
