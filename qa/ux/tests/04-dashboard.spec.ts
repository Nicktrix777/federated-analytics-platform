import { test, expect } from "@playwright/test";
import { watchConsole, shot, goto, assertNoHorizontalScroll } from "../utils/helpers";

const PROMPT =
  "A contracts overview from the Elasticsearch contracts index: total contracts and " +
  "active-contract KPIs, contracts by status, contracts by kind, and contracts created per month.";

test("generate a dashboard with AI, render widgets, fill the canvas (no dead space)", async ({ page }, testInfo) => {
  const cw = watchConsole(page);
  await goto(page, "/dashboards");

  await page.getByRole("button", { name: /Generate with AI/i }).click();
  const dialog = page.getByRole("dialog");
  await expect(dialog).toBeVisible();
  await dialog.locator("textarea").fill(PROMPT);
  await shot(page, testInfo, "04-dashboard-prompt");

  await dialog.getByRole("button", { name: /Generate Dashboard/i }).click();

  // Terminal: navigation to the builder for the new dashboard.
  await page.waitForURL(/\/dashboards\/\d+/, { timeout: 240_000 });
  await page.waitForLoadState("networkidle").catch(() => {});

  // Widgets present.
  const widgets = page.locator(".widget-card");
  await expect(widgets.first()).toBeVisible({ timeout: 30_000 });
  const count = await widgets.count();
  expect.soft(count, "dashboard should have at least one widget").toBeGreaterThan(0);
  test.info().annotations.push({ type: "widget-count", description: String(count) });

  // At least one widget renders real content (chart / number / table), not a stuck spinner.
  await expect(
    page.locator(".widget-card").locator("canvas, table, .kpi-value, .widget-number, .animated-number").first(),
    "at least one widget should render data"
  ).toBeVisible({ timeout: 45_000 });
  await page.waitForTimeout(1500);

  // Dead-space check: the responsive grid should fill most of its container width.
  const grid = page.locator(".react-grid-layout").first();
  if (await grid.count()) {
    const metrics = await grid.evaluate((el) => {
      const parent = el.parentElement as HTMLElement;
      return { gridW: el.getBoundingClientRect().width, parentW: parent.getBoundingClientRect().width };
    });
    const ratio = metrics.gridW / metrics.parentW;
    test.info().annotations.push({ type: "grid-fill-ratio", description: ratio.toFixed(2) });
    expect.soft(ratio, `grid fills only ${(ratio * 100).toFixed(0)}% of its container (dead space on the right)`).toBeGreaterThan(0.85);
  }
  await assertNoHorizontalScroll(page, "dashboard builder @1440");
  await shot(page, testInfo, "04-dashboard-desktop");

  // Responsive: narrower viewports must not overflow horizontally.
  for (const w of [996, 768, 480]) {
    await page.setViewportSize({ width: w, height: 900 });
    await page.waitForTimeout(600);
    await assertNoHorizontalScroll(page, `dashboard builder @${w}`);
    await shot(page, testInfo, `04-dashboard-w${w}`);
  }

  expect.soft(cw.hard(), `console errors:\n${cw.hard().join("\n")}`).toHaveLength(0);
});
