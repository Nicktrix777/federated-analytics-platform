import { test, expect, Page } from "@playwright/test";
import { watchConsole, shot, goto } from "../utils/helpers";

// Raw engine internals that must NEVER appear in the friendly (non-technical) view.
const LEAK_PATTERNS = /GraphRecursionError|Traceback \(most recent|recursion_limit|gemini-\d|gpt-4o|OPENAI_API_KEY|langgraph/i;

async function runQuery(page: Page, question: string) {
  await goto(page, "/");
  const mode = page.locator("#mode-ai-btn");
  if (await mode.count()) await mode.click().catch(() => {});
  await page.locator("#query-textarea").fill(question);
  await page.locator("#submit-query-btn").click();

  // Terminal signal: a result table or chart renders in the transcript.
  const result = page.locator("table, canvas").first();
  await expect(result, "query should produce a table or chart").toBeVisible({ timeout: 200_000 });
  // Let echarts settle / count-ups finish.
  await page.waitForTimeout(1500);
}

test("AI query returns a result with friendly progress and no leaked internals", async ({ page }, testInfo) => {
  const cw = watchConsole(page);
  await runQuery(page, "What is the average salary per department?");
  await shot(page, testInfo, "03-query-simple");

  // Friendly progress component should have rendered (its disclosure button proves it).
  await expect
    .soft(page.getByRole("button", { name: /technical details/i }).first(), "friendly GenerationProgress should be present")
    .toBeVisible();

  // No raw engine internals visible while technical details are collapsed.
  const visibleText = await page.locator("body").innerText();
  expect.soft(visibleText, "engine internals leaked into the friendly view").not.toMatch(LEAK_PATTERNS);

  expect.soft(cw.hard(), `console errors:\n${cw.hard().join("\n")}`).toHaveLength(0);
});

test("AI query handles a heavily-nested contracts question", async ({ page }, testInfo) => {
  const cw = watchConsole(page);
  await runQuery(page, "How many contracts have at least one driver whose nationality is IND?");
  await shot(page, testInfo, "03-query-nested-contracts");

  // Should render a numeric/table answer (not an error state).
  const visibleText = await page.locator("body").innerText();
  expect.soft(visibleText, "nested query surfaced an error to the user").not.toMatch(/failed|error occurred|something went wrong/i);
  expect.soft(cw.hard(), `console errors:\n${cw.hard().join("\n")}`).toHaveLength(0);
});
