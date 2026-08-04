import { Page, TestInfo, expect } from "@playwright/test";
import fs from "fs";
import path from "path";

const SHOT_DIR = path.join(process.cwd(), "artifacts", "screenshots");

// Console noise we deliberately ignore — none of these indicate an app defect.
const BENIGN = [
  /ResizeObserver loop/i,
  /favicon/i,
  /React DevTools/i,
  /Download the React DevTools/i,
  /\[vite\]/i,
];

export interface ConsoleWatcher {
  errors: string[];
  /** Errors minus benign noise. */
  hard(): string[];
}

/** Attach a watcher for console.error + uncaught page errors. Call before navigation. */
export function watchConsole(page: Page): ConsoleWatcher {
  const errors: string[] = [];
  page.on("console", (msg) => {
    if (msg.type() === "error") errors.push(`[console.error] ${msg.text()}`);
  });
  page.on("pageerror", (err) => errors.push(`[pageerror] ${err.message}`));
  page.on("requestfailed", (req) => {
    const f = req.failure();
    // Only surface failed same-origin API calls — external/aborted are ignored.
    if (req.url().includes("/api/")) {
      errors.push(`[requestfailed] ${req.method()} ${req.url()} — ${f?.errorText}`);
    }
  });
  return {
    errors,
    hard: () => errors.filter((e) => !BENIGN.some((re) => re.test(e))),
  };
}

/** Full-page screenshot saved under artifacts/screenshots AND attached to the HTML report. */
export async function shot(page: Page, testInfo: TestInfo, name: string): Promise<void> {
  fs.mkdirSync(SHOT_DIR, { recursive: true });
  const file = path.join(SHOT_DIR, `${name}.png`);
  const buf = await page.screenshot({ path: file, fullPage: true });
  await testInfo.attach(name, { body: buf, contentType: "image/png" });
}

/** Assert the page body doesn't scroll horizontally (dead-space / overflow guard). */
export async function assertNoHorizontalScroll(page: Page, label: string): Promise<void> {
  const overflow = await page.evaluate(() => {
    const el = document.documentElement;
    return { scroll: el.scrollWidth, client: el.clientWidth };
  });
  // 2px tolerance for sub-pixel rounding.
  expect.soft(overflow.scroll, `${label}: horizontal overflow (scrollWidth ${overflow.scroll} > clientWidth ${overflow.client})`)
    .toBeLessThanOrEqual(overflow.client + 2);
}

/** Navigate to a route and wait for the SPA to be interactive. */
export async function goto(page: Page, route: string): Promise<void> {
  await page.goto(route, { waitUntil: "domcontentloaded" });
  await page.waitForLoadState("networkidle").catch(() => {});
}
