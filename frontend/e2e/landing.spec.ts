import { expect, test } from "@playwright/test";

test("primary sign-in exposes account and legal flows without production demo access", async ({ page }) => {
  await page.goto("/");

  await expect(page.getByRole("heading", { name: /^sign in$/i })).toBeVisible();
  await expect(page.getByLabel(/^email$/i)).toBeVisible();
  await expect(page.getByLabel(/^password$/i)).toBeVisible();
  await expect(page.getByRole("button", { name: /enter the demo/i })).toHaveCount(0);

  await page.getByRole("button", { name: /apply for access/i }).click();
  await expect(page.getByRole("heading", { name: /apply for access/i })).toBeVisible();
  await expect(page.getByLabel(/full name/i)).toBeVisible();
  await expect(page.getByLabel(/desk name/i)).toBeVisible();

  await page.getByRole("button", { name: /^sign in$/i }).click();
  await page.getByRole("button", { name: /forgot/i }).click();
  await expect(page.getByRole("heading", { name: /reset password/i })).toBeVisible();
  await expect(page.getByRole("button", { name: /send instructions/i })).toBeVisible();
  await expect(page.getByLabel(/reset token/i)).toBeVisible();

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
