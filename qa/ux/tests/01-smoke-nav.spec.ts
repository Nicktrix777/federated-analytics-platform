import { test, expect } from "@playwright/test";
import { watchConsole, shot, goto, assertNoHorizontalScroll } from "../utils/helpers";

// Every primary route should load, render its landmark, not overflow horizontally,
// and produce no uncaught console errors. This is the fast tripwire for a broken build.
const ROUTES: { path: string; label: string; landmark: (page: import("@playwright/test").Page) => Promise<void> }[] = [
  { path: "/", label: "query", landmark: async (p) => await expect(p.locator("#query-textarea")).toBeVisible() },
  { path: "/dashboards", label: "dashboards", landmark: async (p) => await expect(p.getByRole("heading", { name: /Dashboards/i }).first()).toBeVisible() },
  { path: "/reports", label: "reports", landmark: async (p) => await expect(p.getByRole("heading", { name: /Reports/i }).first()).toBeVisible() },
  { path: "/datasources", label: "datasources", landmark: async (p) => await expect(p.getByRole("button", { name: /Sync Catalogs/i })).toBeVisible() },
  { path: "/settings", label: "settings", landmark: async (p) => await expect(p.locator("#llm_model")).toBeVisible() },
];

test("app loads and every route renders cleanly", async ({ page }, testInfo) => {
  const cw = watchConsole(page);

  // Home must render its composer — also guards against the query page
  // white-screening in a non-secure context (crypto.randomUUID fallback).
  await goto(page, "/");
  await expect(page.locator("#query-textarea")).toBeVisible();
  await shot(page, testInfo, "01-home");

  for (const r of ROUTES) {
    await goto(page, r.path);
    await r.landmark(page);
    await assertNoHorizontalScroll(page, `route ${r.path}`);
    await shot(page, testInfo, `01-route-${r.label}`);
  }

  // Report console errors as soft failures so we see them all, not just the first.
  expect.soft(cw.hard(), `console errors:\n${cw.hard().join("\n")}`).toHaveLength(0);
});

test("theme toggle flips the document theme", async ({ page }, testInfo) => {
  await goto(page, "/");
  const before = await page.evaluate(() => document.documentElement.getAttribute("data-theme"));
  const toggle = page.getByRole("button", { name: /theme|dark|light/i }).first();
  if (await toggle.count()) {
    await toggle.click();
    await page.waitForTimeout(300);
    const after = await page.evaluate(() => document.documentElement.getAttribute("data-theme"));
    expect.soft(after, "data-theme should change after clicking the theme toggle").not.toBe(before);
    await shot(page, testInfo, "01-theme-toggled");
  } else {
    test.info().annotations.push({ type: "note", description: "no theme toggle button found by accessible name" });
  }
});
