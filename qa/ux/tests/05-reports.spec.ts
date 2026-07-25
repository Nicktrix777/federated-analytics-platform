import { test, expect } from "@playwright/test";
import { watchConsole, shot, goto } from "../utils/helpers";
import fs from "fs";
import path from "path";

const PROMPT =
  "A contracts report from the Elasticsearch contracts index: a summary sheet of contract " +
  "counts by status, a sheet of contracts by kind, and a detail sheet of recent contracts.";

test("generate a report with AI, render sheets, and download a valid Excel file", async ({ page }, testInfo) => {
  const cw = watchConsole(page);
  await goto(page, "/reports");

  await page.getByRole("button", { name: /Generate with AI/i }).click();
  const dialog = page.getByRole("dialog");
  await expect(dialog).toBeVisible();
  await dialog.locator("textarea").fill(PROMPT);
  await dialog.getByRole("button", { name: /Generate Report/i }).click();

  // Terminal: navigation to the report detail page.
  await page.waitForURL(/\/reports\/\d+/, { timeout: 240_000 });
  await page.waitForLoadState("networkidle").catch(() => {});

  // Sheets rendered.
  const sheets = page.locator(".rd-sheet-card");
  await expect(sheets.first()).toBeVisible({ timeout: 30_000 });
  const count = await sheets.count();
  expect.soft(count, "report should have at least one sheet").toBeGreaterThan(0);
  test.info().annotations.push({ type: "sheet-count", description: String(count) });
  await shot(page, testInfo, "05-report-detail");

  // Download the Excel workbook and validate it's a real, non-trivial .xlsx (PK zip magic).
  const downloadPromise = page.waitForEvent("download", { timeout: 120_000 });
  await page.getByRole("button", { name: /Download Excel/i }).first().click();
  const download = await downloadPromise;
  const out = path.join(process.cwd(), "artifacts", "report.xlsx");
  await download.saveAs(out);
  const stat = fs.statSync(out);
  expect.soft(stat.size, "downloaded workbook is empty").toBeGreaterThan(1000);
  const magic = fs.readFileSync(out).subarray(0, 2).toString("latin1");
  expect.soft(magic, "downloaded file is not a valid xlsx (zip) container").toBe("PK");
  test.info().annotations.push({ type: "xlsx-bytes", description: String(stat.size) });

  expect.soft(cw.hard(), `console errors:\n${cw.hard().join("\n")}`).toHaveLength(0);
});
