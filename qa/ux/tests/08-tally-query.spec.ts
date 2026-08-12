import { test, expect } from "@playwright/test";
import fs from "fs";
import path from "path";
import { watchConsole, shot, goto } from "../utils/helpers";

// Continuation of 07-tally-create.spec.ts, run AFTER the shell has started a
// real `tally-bridge` container against the token that test wrote to
// artifacts/tally-bridge-token.txt (Playwright can't do that part itself —
// it only drives the browser). This is Phase 0's actual gap per
// docs/tally-e2e-verification-and-write-support-plan.md: nobody had asked
// ai-engine/query-service a real NL question against a live Tally-shaped
// dataset before.
const TOKEN_FILE = path.join(process.cwd(), "artifacts", "tally-bridge-token.txt");

test.describe.configure({ mode: "serial" });

test("Tally Schema Preview shows the four live tables after Refresh Schema", async ({ page }, testInfo) => {
  const [dsName] = fs.readFileSync(TOKEN_FILE, "utf8").trim().split("\n");
  const cw = watchConsole(page);
  await goto(page, "/datasources");

  const card = page.locator(".ds-card", { hasText: dsName });
  await expect(card).toBeVisible();

  await card.getByRole("button", { name: /refresh schema/i }).click();
  const toast = page.getByRole("alert").first();
  await expect(toast).toBeVisible({ timeout: 30_000 });
  const toastText = (await toast.textContent()) ?? "";
  test.info().annotations.push({ type: "refresh-toast", description: toastText.trim() });

  // Fail loudly (not silently) if the refresh itself reported an error —
  // this is exactly the "schema-rendering gap" the plan doc calls out.
  expect(toastText, `Refresh Schema toast: ${toastText}`).not.toMatch(/fail|error/i);

  await card.getByRole("button", { name: /schema preview/i }).click();
  await shot(page, testInfo, "08-tally-schema-preview");

  for (const table of ["ledgers", "vouchers", "stock_items", "groups"]) {
    await expect(card.getByText(new RegExp(table, "i"))).toBeVisible();
  }

  // Per-source "Refresh Schema" only writes data_sources.schema_cache (what
  // the preview above reads) — it does NOT register the tables in the
  // `datasets` table ai-engine's schema-RAG actually queries from (see
  // syncDatasetsForCatalog, only called from SyncCatalogsFromTrino). Without
  // this, the NL query below would never see the new tables exist at all.
  await page.getByRole("button", { name: /sync catalogs/i }).click();
  const syncToast = page.getByRole("alert").first();
  await expect(syncToast).toBeVisible({ timeout: 30_000 });
  const syncToastText = (await syncToast.textContent()) ?? "";
  test.info().annotations.push({ type: "sync-catalogs-toast", description: syncToastText.trim() });
  expect(syncToastText, `Sync Catalogs toast: ${syncToastText}`).not.toMatch(/fail|error/i);

  expect.soft(cw.hard(), `console errors:\n${cw.hard().join("\n")}`).toHaveLength(0);
});

test("NL query against live Tally data returns a correct result", async ({ page }, testInfo) => {
  const cw = watchConsole(page);
  await goto(page, "/");

  const mode = page.locator("#mode-ai-btn");
  if (await mode.count()) await mode.click().catch(() => {});
  await page.locator("#query-textarea").fill("What is the closing balance of the Cash ledger in Tally?");
  await page.locator("#submit-query-btn").click();

  const result = page.locator("table, canvas").first();
  const errored = page.getByText(/failed|error occurred|something went wrong/i).first();
  await expect(result.or(errored)).toBeVisible({ timeout: 200_000 });
  await page.waitForTimeout(1000);
  await shot(page, testInfo, "08-tally-nl-query-result");

  // Surface the generated SQL for manual/log inspection either way — this is
  // the one thing Phase 0 explicitly says was never verified before.
  const techBtn = page.getByRole("button", { name: /technical details/i }).first();
  if (await techBtn.count()) {
    await techBtn.click();
    const techText = await page.locator(".gp-technical").innerText().catch(() => "");
    test.info().annotations.push({ type: "generated-sql-context", description: techText.slice(0, 4000) });
    await shot(page, testInfo, "08-tally-nl-query-technical");
  }

  const bodyText = await page.locator("body").innerText();
  expect(bodyText, "NL query against Tally data surfaced an error").not.toMatch(/failed|error occurred|something went wrong/i);

  // The Cash ledger's mock closing balance is 15000.00 (mock-tally/server.py) —
  // confirm the actual number made it through Trino → query-service → UI,
  // not just "a table rendered".
  await expect(page.locator("table, .stat-value, .kpi-value").first()).toContainText(/15,?000/);

  expect.soft(cw.hard(), `console errors:\n${cw.hard().join("\n")}`).toHaveLength(0);
});
