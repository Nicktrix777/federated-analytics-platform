# Zoho Books / Tally: E2E Verification, then Tally Write Support

Date: 2026-08-10
Status: **Phase 0 complete** (2026-08-10) — Tally's full loop (creation → bridge → NL query → correct result) is verified working. Zoho Books verified as far as possible without real credentials. Three bugs found and fixed along the way (see "Phase 0 — Results" below). Cleared to start Phase 1.

## Context

The read-path Zoho Books and Tally connectors (native Trino plugins, the `tally-bridge` tunnel, frontend forms) were built and verified at the plumbing level in the previous pass — Java compiles, credentials decrypt correctly, a live query reaches Zoho's real OAuth servers, and the full `tally-bridge` tunnel round-trips through a mock Tally server for all four entities. **What was never exercised is the actual user-facing path**: creating a datasource through the real UI end-to-end, and — critically — asking a natural-language question against these new connectors and confirming ai-engine/query-service produce a correct result. That's a real gap: the AI pipeline's schema-rendering and prompt-building have never seen these two connector types, and nothing has confirmed they behave correctly with them.

Separately, a new CR came in: the client (primarily on TallyPrime, secondarily Zoho Books) wants **write/input capability**, not just read — and explicitly does not want it AI-mediated ("AI is a surcharge" — they want direct data entry, not NL-generated writes). A dedicated mobile app (Capacitor-wrapped) was also requested, tracked separately.

**Sequencing**: verify the existing read path properly first (Phase 0) before adding new surface area (Phase 1 below). Building more on top of an unverified foundation compounds risk.

---

## Phase 0 — E2E verification of the existing Zoho Books / Tally read path (do this first)

1. **Regression check**: run the existing Playwright UX suite (`qa/run.sh`) to confirm the `DataSourcesPage.tsx`/`Icon.tsx` changes didn't break anything already covered.
2. **Tally, fully through the UI** (the one we can actually complete without external credentials):
   - Create a Tally datasource through the real Connect Source modal (not curl).
   - Confirm the bridge-token reveal modal appears with the right content (already screenshot-verified once; re-confirm as part of the full flow).
   - Bring up `tally-bridge` + `mock-tally` (already built in `docker-compose.dev.yml`), connect it with the real token from the UI-driven creation.
   - Confirm the Data Sources page's own "Schema Preview" picks up the four Tally tables.
   - **The actual gap**: go to the Query page and ask a real natural-language question against the Tally data (e.g. "what's the closing balance of the Cash ledger" or "list all vouchers"). Confirm ai-engine's generated SQL is correct against the live schema, query-service executes it via Trino successfully, and the result renders correctly in the UI. This is the piece that was never tested — everything before it was infrastructure-level.
   - If the NL query fails or produces something wrong, diagnose whether it's a schema-rendering gap (ai-engine doesn't understand the new dataset shape), a prompt gap, or something else — fix what's actually broken rather than assuming success.
3. **Zoho Books, as far as it can go without real credentials**: repeat datasource creation through the real UI (Client ID/Secret/Grant Code/Org ID/Data Center fields), confirm the same clear rejection from Zoho's real OAuth servers surfaces correctly in the UI (already confirmed via curl reaching Zoho's real servers; confirming through the actual form is the remaining piece). Real credentials would be needed for a full loop through to an NL query — flag this plainly rather than fake success.
4. Fix whatever's found. Only move to Phase 1 once Tally's full loop (creation → bridge → NL query → correct result) genuinely works.

### Phase 0 — Results (2026-08-10)

New Playwright specs added at `qa/ux/tests/07-tally-create.spec.ts`, `08-tally-query.spec.ts`,
`09-zoho-books-form.spec.ts` — real browser automation against the real UI (not curl), run via
`qa/run.sh` alongside the existing suite. All pass.

**The actual gap is closed**: created a Tally datasource through the Connect Source modal, got a
real bridge token, started a real `tally-bridge` container against it (pointed at `mock-tally`),
ran `Refresh Schema` (Schema Preview shows all 4 tables: ledgers/vouchers/stock_items/groups) and
`Sync Catalogs` (registers them as datasets for ai-engine — see bug #3 below), then asked
"What is the closing balance of the Cash ledger in Tally?" on the live Query page. ai-engine
planned correct SQL against the new `tally.<schema>.ledgers` shape, query-service executed it
through Trino, Trino's tally connector forwarded it through the tunnel to the real `tally-bridge`,
and the UI rendered the correct answer (15,000, matching `mock-tally/server.py`'s fixture). No
schema-rendering or prompt gap — the generic Trino-introspection pipeline handled the new
connector type with no special-casing needed.

**Zoho Books**: real UI form (Client ID/Secret/Grant Code/Org ID/Data Center) submitted against
Zoho's real OAuth token endpoint with a fake grant code. Rejection (`zoho rejected the grant code:
invalid_client`) surfaces correctly in the modal, the modal stays open (no false-success), no row
is created. A full loop to an NL query still needs real Zoho credentials — not possible here.

**Three bugs found, all fixed**:

1. **`frontend/vite.config.ts`** — Vite 5's dev-server host-check silently blocked every request
   with `Host: frontend` (the compose service DNS name other containers use — notably
   `qa/run.sh`'s Playwright runner). 100% of tests failed with a blocked-host page, not an app
   error. Fixed with `server.allowedHosts: ["frontend"]`.
2. **`core-api/middleware/cors.go`** — `AllowOrigins` was hardcoded to `localhost:3000`/`5173`
   only. Browsers attach `Origin` on every POST (even ones same-origin-via-Vite-proxy), so once
   the host above was fixed, every mutating request (`/api/query`, `/api/query/stream`,
   `/api/datasources/sync`) started 403-ing in ~20µs while GETs kept working — looked exactly like
   a rate limit or a hung backend, was neither. Fixed by adding `http://frontend:5173` /
   `http://frontend` to the allowlist.
3. **Per-source "Refresh Schema" vs. "Sync Catalogs"** (not a bug, a real trap): Refresh Schema
   only writes `data_sources.schema_cache` (what the UI's Schema Preview reads) — it does **not**
   register tables in the `datasets` table ai-engine's schema-RAG actually queries from (only
   `SyncCatalogsFromTrino` → `syncDatasetsForCatalog` does that). A newly-created Tally/Zoho source
   would look correct in the Schema Preview and still be completely invisible to NL queries until
   someone also clicks "Sync Catalogs." Confirms [[datasource-refresh-vs-sync]] from memory; worth
   a UI nudge eventually (e.g. auto-run a full sync, not just a schema fetch, right after a
   live-connector source is created) but out of scope for this pass.

**Minor, non-blocking finding**: `core-api/handlers/datasource.go`'s `HandleCreate` maps every
`svc.Create` error — including a client-input problem like a bad/expired Zoho grant code — to HTTP
500. The correct message still reaches the UI (frontend reads `details` regardless of status), so
this didn't block anything, but a bad grant code is a 400, not a 500. Not fixed (out of scope —
affects the shared error path for every source type, not something to change opportunistically).

## Phase 1 — TallyPrime voucher write support (direct form, no AI)

Client's stated priority is Tally over Zoho, so write support starts there. Deliberately **not** routed through Trino or the AI layer at all — this is exactly why that architecture choice (confirmed with the user) pays off here: the existing `tally-bridge` tunnel (`core-api/services/tally_tunnel.go`'s `TallyTunnelRegistry.Forward`) is a content-agnostic relay and needs **zero changes** to carry write requests. No Trino write-SPI work, no Java connector changes — this is entirely new core-api + frontend surface reusing the tunnel as-is.

**Same caveat as the read side applies**: there's no live Tally instance to verify the exact XML dialect against. Voucher-import XML shape below follows Tally's long-documented `TALLYREQUEST=Import Data` / `ALLLEDGERENTRIES.LIST` pattern (used by many real third-party Tally integrations), but needs a live-Tally verification pass before being trusted for a real customer — flag this the same way `TallyEntity.java`'s class comment already does for reads.

### Backend (core-api)

- New `core-api/services/tally_voucher.go`:
  - `CreateVoucherRequest` — `voucher_type` (validated against a known set: Payment/Receipt/Sales/Purchase/Journal/Contra), `date` (YYYYMMDD), optional `voucher_number`/`narration`, `ledger_entries` (min 2: `ledger_name`, `is_debit bool`, `amount float64 > 0`).
  - Server-side balance check before ever calling Tally: sum(debit) must equal sum(credit) — fail fast with a clear error, don't rely on Tally to catch it.
  - Build the request via `encoding/xml` struct marshaling, **not** raw string concatenation (unlike the read side's fixed, no-user-input requests, this embeds user-supplied ledger names/narration — needs real XML escaping, not manual string-building).
  - Call the existing `TallyTunnelRegistry.Forward(datasourceID, requestXML, timeout)` — same registry instance already wired in `main.go`, just a new caller.
  - Parse the `<RESPONSE>` (`<CREATED>`/`<ALTERED>`/`<ERRORS>`/`<LASTVCHID>`) and any `<LINEERROR>` messages into a clear success/failure result.
- New `core-api/handlers/tally_voucher.go`, new route `POST /api/tally/:id/vouchers` — inside the **existing** authenticated `/api` group (unlike the internal tunnel endpoints), so it gets the standard bearer auth + `middleware.Audit(db)` for free. A write action against a customer's real ledger should be audited like every other mutation in this app, more so than reads.

### Frontend

- New page (e.g. `TallyVoucherPage.tsx`), reachable via a "Record Voucher" action on Tally-type rows in `DataSourcesPage.tsx` (not a new top-level nav item yet — promote to one later if more write capabilities land, e.g. Zoho write).
- Form: voucher type dropdown, date, optional voucher number/narration, dynamic ledger-entry rows (ledger name, debit/credit toggle, amount) with a live running-balance indicator disabling submit until balanced.
- Built responsive-first (flex-wrap layout, touch-friendly inputs) since mobile is an explicit future consumer of this same UI via the Capacitor wrapper — no extra work now, just don't build it desktop-only.

### Mock-tally extension (for testing, same reasoning as the read side)

- Extend `mock-tally/server.py` to also handle `<TALLYREQUEST>Import Data</TALLYREQUEST>`: parse the incoming `<VOUCHER>`, store it in memory, return a proper `<RESPONSE><CREATED>1</CREATED>...</RESPONSE>`.
- Make the mock's existing "Day Book" (read) response reflect in-memory created vouchers too — lets a full loop (create via the new form → read it back via the existing Tally read connector) be verified end-to-end, the strongest test available without a real instance.

## Verification

- Phase 0 first, as its own gate — do not start Phase 1 until Tally's full UI-driven creation → NL query → correct result loop is confirmed working.
- Phase 1: create a voucher through the new form against the dev `tally-bridge`/`mock-tally` setup, confirm `mock-tally` received a well-formed, correctly-escaped Import Data XML request, confirm the success response renders correctly in the UI, then confirm the same voucher shows up when reading `tally.<schema>.vouchers` through the existing (unchanged) read connector.
- Test the balance-mismatch rejection path (unbalanced ledger entries) fails fast client-side and server-side, never reaching the tunnel at all.
- Test the "bridge not connected" path (stop `tally-bridge`, attempt a write) surfaces a clear, actionable error rather than a timeout with no explanation.

## Explicitly deferred, tracked separately (not this pass)

- **Zoho Books write support** — same shape as Tally (direct form, no AI), calling Zoho's REST write endpoints directly. Do after Tally write is proven and the client's priority shifts or expands to it.
- **Capacitor mobile app** — wraps the existing React frontend (including whatever's built here) into an installable iOS/Android app. Its own project once there's more to wrap than one form.

## Session context (why this doc exists)

Written mid-session because the conversation's context window was getting too large to safely continue in. This doc is the durable source of truth for what's next — pick up here in a fresh session rather than relying on conversation history.
