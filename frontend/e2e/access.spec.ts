import { expect, test, type Page } from "@playwright/test";

/**
 * Role-aware navigation and restricted-route behaviour against a live backend.
 *
 * These tests use the two accounts the backend seeds for local development:
 *   demo@quantops.local  — platform administrator, workspace owner
 *   user@quantops.local  — standard member on a free workspace
 *
 * They are skipped when the API is not reachable, because they assert real
 * server-side authorization rather than a mocked approximation of it.
 */

const ADMIN = { email: "demo@quantops.local", password: "quantops-demo" };
const FREE_MEMBER = { email: "user@quantops.local", password: "quantops-user" };

async function apiReachable(page: Page) {
  try {
    const response = await page.request.get("/api/health");
    return response.ok();
  } catch {
    return false;
  }
}

async function signIn(page: Page, credentials: { email: string; password: string }) {
  const response = await page.request.post("/api/auth/login", { data: credentials });
  expect(response.ok(), `sign-in failed for ${credentials.email}`).toBeTruthy();
  await page.goto("/overview");
  await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
}

test.describe("role-aware navigation", () => {
  test.beforeEach(async ({ page }) => {
    test.skip(!(await apiReachable(page)), "backend API is not running on 127.0.0.1:8000");
  });

  test("a platform administrator sees administration; a standard member does not", async ({ page }) => {
    await signIn(page, ADMIN);
    const nav = page.getByRole("navigation", { name: "Primary" });
    // Desktop rail is hidden below 1024px, so read the drawer on mobile instead.
    const navigation = (await nav.isVisible()) ? nav : await openDrawer(page);
    await expect(navigation.getByRole("link", { name: "Administration" })).toBeVisible();
    await expect(navigation.getByRole("link", { name: "Management" })).toBeVisible();

    await page.request.post("/api/auth/logout", { headers: csrfHeaders(await page.context().cookies()) });
    await page.context().clearCookies();

    await signIn(page, FREE_MEMBER);
    const memberNav = (await nav.isVisible()) ? nav : await openDrawer(page);
    await expect(memberNav.getByRole("link", { name: "Administration" })).toHaveCount(0);
    await expect(memberNav.getByRole("link", { name: "Management" })).toHaveCount(0);
    // Everything genuinely free is still present.
    await expect(memberNav.getByRole("link", { name: "Strategies" })).toBeVisible();
    await expect(memberNav.getByRole("link", { name: "Backtests" })).toBeVisible();
    await expect(memberNav.getByRole("link", { name: "Learn" })).toBeVisible();
  });

  test("entering /admin directly as a standard member is refused with an explanation", async ({ page }) => {
    await signIn(page, FREE_MEMBER);
    await page.goto("/admin");
    await expect(page.getByText("Requires platform administrator permission")).toBeVisible();
    await expect(page.getByText(/limited to accounts with the administrator role/i)).toBeVisible();
    await expect(page.getByRole("button", { name: /grant platform admin/i })).toHaveCount(0);
  });

  test("entering /management directly as a standard member is refused with an explanation", async ({ page }) => {
    await signIn(page, FREE_MEMBER);
    await page.goto("/management");
    await expect(page.getByText("Requires workspace manager permission")).toBeVisible();
    await expect(page.getByRole("tab", { name: /shared research/i })).toHaveCount(0);
  });

  test("a free member gets an explained paid gate on backtests, not a dead control", async ({ page }) => {
    await signIn(page, FREE_MEMBER);
    await page.goto("/backtests");
    await expect(page.getByRole("heading", { name: "Starting a new backtest" })).toBeVisible();
    await expect(page.getByText("Included with a paid plan")).toBeVisible();
    await expect(page.getByRole("button", { name: /launch backtest agent/i })).toBeDisabled();
  });

  test("the strategy builder is available on the free plan", async ({ page }) => {
    await signIn(page, FREE_MEMBER);
    await page.goto("/strategies/builder");
    await expect(page.getByRole("heading", { name: "Describe the idea" })).toBeVisible();
    await expect(page.getByLabel("Your description")).toBeEnabled();
  });
});

test.describe("legacy deep links", () => {
  test.beforeEach(async ({ page }) => {
    test.skip(!(await apiReachable(page)), "backend API is not running on 127.0.0.1:8000");
  });

  test("a #/app/<view> hash link resolves onto the unified route", async ({ page }) => {
    await signIn(page, ADMIN);
    await page.goto("/#/app/backtests");
    await expect(page).toHaveURL(/\/backtests$/);
    await expect(page.getByRole("heading", { name: "Historical validation" })).toBeVisible();
  });

  test("/apollo and /classic both land in the unified product", async ({ page }) => {
    await signIn(page, ADMIN);
    await page.goto("/apollo");
    await expect(page).toHaveURL(/\/overview$/);

    await page.goto("/classic");
    await expect(page.getByRole("heading", { name: /unified workspace/i })).toBeVisible();
    await page.getByRole("button", { name: /continue to meridian/i }).click();
    await expect(page).toHaveURL(/\/overview$/);
  });
});

function csrfHeaders(cookies: Array<{ name: string; value: string }>) {
  const csrf = cookies.find((cookie) => cookie.name === "quantops_csrf");
  return csrf ? { "X-CSRF-Token": decodeURIComponent(csrf.value) } : {};
}

async function openDrawer(page: Page) {
  await page.getByRole("button", { name: "Open navigation" }).click();
  return page.getByRole("dialog", { name: "Navigation" });
}
