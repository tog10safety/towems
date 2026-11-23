import { test, expect } from "@playwright/test";
import { signIn } from "./helpers.js";

test("Air Operations shows the seeded actual-time timing warning", async ({ page }) => {
  await signIn(page);

  await page.goto("/missions/air");
  await expect(page.getByRole("heading", { name: "Air Operations" })).toBeVisible();

  const date = page.getByLabel("Flight Operations date");
  await date.fill("2099-11-11");

  await expect(
    page.getByText("PW-SMOKE-AO-001", { exact: true })
  ).toBeVisible();
  await expect(page.getByText("1 actual-time timing check.", { exact: true })).toBeVisible();

  const warning = page.locator('[aria-label^="Timing check:"]').first();
  await expect(warning).toBeVisible();
  await expect(warning).toContainText("Check actual times");
  await expect(warning).toHaveAttribute(
    "aria-label",
    "Timing check: PW-01 leg 1: actual takeoff is 3 h before the planned ETD."
  );
});
