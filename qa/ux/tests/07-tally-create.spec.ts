import { test, expect } from "@playwright/test";
import fs from "fs";
import path from "path";
import { watchConsole, shot, goto } from "../utils/helpers";

// Phase 0 of docs/tally-e2e-verification-and-write-support-plan.md: the
// Tally/Zoho connectors were previously only exercised via curl/plumbing
// checks, never through the real Connect Source modal. This drives the
// actual UI: pick "tally" as the source type, submit with just a name (no
// host/port fields — LIVE_CONNECTOR_TYPES skips them), and confirm the
// one-time bridge-token reveal modal appears with real content.
//
// The token is written to artifacts/tally-bridge-token.txt so the shell
// can pick it up and start a real `tally-bridge` container against it —
// Playwright itself has no way to reach outside the browser/app.
const TOKEN_FILE = path.join(process.cwd(), "artifacts", "tally-bridge-token.txt");
const DS_NAME = `QA Tally Co ${Date.now()}`;

test("creates a Tally datasource through the Connect Source modal and reveals a bridge token", async ({ page }, testInfo) => {
  const cw = watchConsole(page);
  await goto(page, "/datasources");

  await page.getByRole("button", { name: /connect source/i }).click();
  const modal = page.getByRole("dialog");
  await expect(modal.getByRole("heading", { name: /connect data source/i })).toBeVisible();

  // Source type picker is a grid of buttons labelled by the raw type string.
  await modal.locator(".source-type-btn", { hasText: "tally" }).click();
  await expect(modal.locator(".source-type-btn.selected")).toHaveText(/tally/);

  // Live-connector types (tally/zoho_books) must NOT show the classic
  // host/port/trino_catalog fields — this is the whole point of
  // LIVE_CONNECTOR_TYPES in DataSourcesPage.tsx.
  await expect(modal.getByLabel(/trino catalog name/i)).toHaveCount(0);
  await expect(modal.getByLabel(/^host/i)).toHaveCount(0);

  await modal.getByPlaceholder(/acme corp tally/i).fill(DS_NAME);
  await shot(page, testInfo, "07-tally-form-filled");

  await modal.getByRole("button", { name: /connect.*fetch schema/i }).click();

  // Bridge-token reveal modal, shown exactly once.
  const tokenModal = page.getByRole("dialog").filter({ hasText: /install the tally bridge/i });
  await expect(tokenModal).toBeVisible({ timeout: 15_000 });
  await expect(tokenModal.getByText(DS_NAME)).toBeVisible();
  await expect(tokenModal.getByText(/shown only once/i)).toBeVisible();
  await expect(tokenModal.getByText(/tally-bridge -server/i)).toBeVisible();

  const token = await tokenModal.locator("input.form-input[readonly]").inputValue();
  expect(token, "bridge token should be a non-trivial opaque string").toMatch(/^.{16,}$/);

  fs.mkdirSync(path.dirname(TOKEN_FILE), { recursive: true });
  fs.writeFileSync(TOKEN_FILE, `${DS_NAME}\n${token}\n`);
  test.info().annotations.push({ type: "bridge-token-datasource", description: DS_NAME });

  await shot(page, testInfo, "07-tally-bridge-token-modal");

  await tokenModal.getByRole("button", { name: /^done$/i }).click();
  await expect(page.getByRole("alert").filter({ hasText: /connected successfully/i })).toBeVisible();

  // Confirm the card shows up in the grid with the right type badge.
  await expect(page.getByText(DS_NAME)).toBeVisible();

  expect.soft(cw.hard(), `console errors:\n${cw.hard().join("\n")}`).toHaveLength(0);
});
