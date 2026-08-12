import { test, expect } from "@playwright/test";
import { watchConsole, shot, goto } from "../utils/helpers";

// Phase 0, Zoho Books leg: no real Zoho org is available in this environment,
// so a full loop (creation → NL query) can't be exercised — this confirms as
// much of the real UI path as is possible without live credentials: the
// OAuth fields render correctly for the "zoho_books" source type, and Zoho's
// real token endpoint's rejection of a fake grant code surfaces as a clear
// in-modal error rather than a silent failure or a generic 500.
test("Zoho Books form submits real fields and surfaces Zoho's OAuth rejection in the UI", async ({ page }, testInfo) => {
  const cw = watchConsole(page);
  await goto(page, "/datasources");

  await page.getByRole("button", { name: /connect source/i }).click();
  const modal = page.getByRole("dialog");
  await modal.locator(".source-type-btn", { hasText: "zoho_books" }).click();
  await expect(modal.locator(".source-type-btn.selected")).toHaveText(/zoho_books/);

  // Same LIVE_CONNECTOR_TYPES check as Tally: no host/port/trino_catalog fields.
  await expect(modal.getByLabel(/trino catalog name/i)).toHaveCount(0);
  await expect(modal.getByLabel(/^host/i)).toHaveCount(0);

  await modal.getByPlaceholder(/acme corp zoho/i).fill(`QA Zoho Co ${Date.now()}`);
  await modal.getByPlaceholder(/from your zoho self client/i).fill("qa-test-client-id");
  await modal.locator('input[type="password"]').first().fill("qa-test-client-secret");
  await modal.getByPlaceholder(/one-time code/i).fill("qa-test-fake-grant-code");
  await modal.getByPlaceholder(/organization profile/i).fill("123456789");
  await expect(modal.locator("select")).toHaveValue("com");
  await shot(page, testInfo, "09-zoho-form-filled");

  await modal.getByRole("button", { name: /connect.*fetch schema/i }).click();

  // Real call to Zoho's OAuth servers — expect a clear rejection, not a hang
  // or a generic failure. exchangeZohoGrantCode (core-api/services/zoho_auth.go)
  // surfaces either "zoho rejected the grant code: ..." or "zoho token
  // exchange failed ...".
  const err = modal.locator(".form-error");
  await expect(err).toBeVisible({ timeout: 20_000 });
  const errText = (await err.textContent()) ?? "";
  test.info().annotations.push({ type: "zoho-oauth-rejection", description: errText.trim() });
  await shot(page, testInfo, "09-zoho-oauth-rejection");

  expect(errText, `Zoho rejection message: ${errText}`).toMatch(
    /zoho rejected the grant code|zoho token exchange failed|failed to reach zoho/i
  );

  // Creation must NOT have silently succeeded — the modal stays open on error.
  await expect(modal).toBeVisible();

  // The create call above is EXPECTED to fail with a 500: per the Phase 0
  // doc's "Minor, non-blocking finding", HandleCreate maps every svc.Create
  // error — including a client-input problem like a bad grant code — to 500
  // rather than 400 (deliberately not fixed, since it's a shared error path
  // for every source type). The browser's own console.error for that
  // expected failed resource load isn't a real app defect for this test.
  const consoleErrors = cw.hard().filter((e) => !/Failed to load resource.*500/i.test(e));
  expect.soft(consoleErrors, `console errors:\n${consoleErrors.join("\n")}`).toHaveLength(0);
});
