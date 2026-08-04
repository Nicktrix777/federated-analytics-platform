import { test, expect } from "@playwright/test";
import { shot, goto, assertNoHorizontalScroll } from "../utils/helpers";

// A11y baseline the overhaul plan committed to: a visible focus ring (the app sets
// outline:none everywhere, so it must supply :focus-visible), Esc-closable modals,
// and no horizontal overflow at any breakpoint.

test("keyboard focus produces a visible focus ring", async ({ page }, testInfo) => {
  await goto(page, "/dashboards");
  // Tab into the page and find where focus lands.
  for (let i = 0; i < 4; i++) await page.keyboard.press("Tab");
  const ring = await page.evaluate(() => {
    const el = document.activeElement as HTMLElement | null;
    if (!el || el === document.body) return null;
    const s = getComputedStyle(el);
    const outlinePx = parseFloat(s.outlineWidth || "0");
    const hasOutline = s.outlineStyle !== "none" && outlinePx > 0;
    const hasShadow = s.boxShadow !== "none" && s.boxShadow !== "";
    return { tag: el.tagName, outlineWidth: s.outlineWidth, outlineStyle: s.outlineStyle, boxShadow: s.boxShadow, visible: hasOutline || hasShadow };
  });
  test.info().annotations.push({ type: "focused", description: JSON.stringify(ring) });
  await shot(page, testInfo, "06-focus-ring");
  expect.soft(ring?.visible, `focused <${ring?.tag}> has no visible focus indicator`).toBe(true);
});

test("modal closes on Escape", async ({ page }, testInfo) => {
  await goto(page, "/dashboards");
  await page.getByRole("button", { name: /New Dashboard/i }).click();
  const dialog = page.getByRole("dialog");
  await expect(dialog).toBeVisible();
  await shot(page, testInfo, "06-modal-open");
  await page.keyboard.press("Escape");
  await expect(dialog, "non-persistent modal should close on Escape").toBeHidden({ timeout: 5000 });
});

test("no horizontal overflow at mobile / tablet widths on list pages", async ({ page }) => {
  for (const path of ["/", "/dashboards", "/reports", "/datasources", "/settings"]) {
    for (const w of [1280, 768, 390]) {
      await page.setViewportSize({ width: w, height: 844 });
      await goto(page, path);
      await assertNoHorizontalScroll(page, `${path} @${w}`);
    }
  }
});
