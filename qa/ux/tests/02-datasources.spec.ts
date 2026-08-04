import { test, expect } from "@playwright/test";
import { watchConsole, shot, goto } from "../utils/helpers";

// The three seeded sources should be visible, and "Sync Catalogs" must give
// visible feedback (a toast) rather than failing silently — a specific gripe
// the overhaul plan called out (silently-swallowed failures).
test("data sources list shows seeded sources and sync gives feedback", async ({ page }, testInfo) => {
  const cw = watchConsole(page);
  await goto(page, "/datasources");

  // All three seeded sources should be present by name.
  for (const name of [/Elasticsearch/i, /Mongo/i, /Postgres/i]) {
    await expect(page.getByText(name).first()).toBeVisible();
  }
  await shot(page, testInfo, "02-datasources");

  const syncBtn = page.getByRole("button", { name: /Sync Catalogs/i });
  await expect(syncBtn).toBeVisible();
  await syncBtn.click();

  // Expect a toast (role=alert) confirming the sync — success OR a surfaced error,
  // but never nothing.
  const toast = page.getByRole("alert").first();
  await expect(toast).toBeVisible({ timeout: 30_000 });
  const toastText = (await toast.textContent()) ?? "";
  test.info().annotations.push({ type: "sync-toast", description: toastText.trim() });
  await shot(page, testInfo, "02-sync-toast");

  expect.soft(cw.hard(), `console errors:\n${cw.hard().join("\n")}`).toHaveLength(0);
});
