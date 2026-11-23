import { test, expect } from "@playwright/test";
import { credentials } from "./helpers.js";

test("seeded smoke user signs in", async ({ page }) => {
  await page.goto("/login");
  await expect(page.getByRole("heading", { name: "Sign in" })).toBeVisible();

  await page.getByLabel("Username").fill(credentials.username);
  await page.locator('input[type="password"]').fill(credentials.password);
  await page.getByRole("button", { name: "Sign in" }).click();

  await expect(page).toHaveURL(/\/$/);
  await expect(page.getByRole("heading", { name: "Operational Dashboard" })).toBeVisible();
  await expect(page.getByText("Air Operations", { exact: true }).first()).toBeVisible();
});
