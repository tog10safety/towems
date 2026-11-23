import { expect } from "@playwright/test";

export const credentials = {
  username: process.env.TOWEMS_E2E_USERNAME || "playwright.smoke",
  password: process.env.TOWEMS_E2E_PASSWORD || "Playwright!2026",
};

/** Sign in with the seeded Operations Center smoke account. */
export async function signIn(page) {
  await page.goto("/login");
  await expect(page.getByRole("heading", { name: "Sign in" })).toBeVisible();
  await page.getByLabel("Username").fill(credentials.username);
  await page.locator('input[type="password"]').fill(credentials.password);
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page.getByRole("heading", { name: "Operational Dashboard" })).toBeVisible();
}
