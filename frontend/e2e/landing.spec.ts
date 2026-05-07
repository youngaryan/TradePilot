import { expect, test } from "@playwright/test";

test("landing page exposes conversion and legal flows", async ({ page }) => {
  await page.goto("/");

  await expect(page.getByRole("link", { name: /quantops home/i })).toBeVisible();
  await expect(page.getByRole("heading", { name: /research, validate, and paper trade/i })).toBeVisible();

  await page.getByRole("button", { name: /view pricing/i }).first().click();
  await expect(page.locator("#pricing")).toBeInViewport();
  await expect(page.getByRole("heading", { name: /start free, unlock premium workflows/i })).toBeVisible();

  await page.getByRole("button", { name: /login/i }).first().click();
  await expect(page.locator("#login")).toBeInViewport();
  await expect(page.getByLabel(/email/i).first()).toBeVisible();

  await page.goto("/privacy");
  await expect(page.getByRole("heading", { name: /privacy policy/i })).toBeVisible();

  await page.goto("/risk-disclaimer");
  await expect(page.getByRole("heading", { name: /risk disclaimer/i })).toBeVisible();

  await page.goto("/compliance");
  await expect(page.getByRole("heading", { name: /compliance boundary/i })).toBeVisible();
});

test("password reset and email verification utility pages render", async ({ page }) => {
  await page.goto("/password-reset");
  await expect(page.getByRole("heading", { name: /request a reset link/i })).toBeVisible();
  await expect(page.getByLabel(/account email/i)).toBeVisible();

  await page.goto("/password-reset?token=test-token-1234567890");
  await expect(page.getByRole("heading", { name: /choose a new password/i })).toBeVisible();
  await expect(page.getByLabel(/new password/i)).toBeVisible();

  await page.goto("/verify-email?token=test-token-1234567890");
  await expect(page.getByRole("heading", { name: /verify your email/i })).toBeVisible();
  await expect(page.getByLabel(/verification token/i)).toBeVisible();
});
