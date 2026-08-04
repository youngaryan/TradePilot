// Visual QA harness (scratchpad tool, not part of the repo).
// Usage: node qa.mjs <persona> <viewport> <theme> <shot-spec...>
import { chromium } from "@playwright/test";
import fs from "node:fs";
import path from "node:path";

const BASE = process.env.QA_BASE ?? "http://127.0.0.1:5173";
const OUT = process.env.QA_OUT ?? path.join(process.cwd(), "shots");
fs.mkdirSync(OUT, { recursive: true });

const PERSONAS = {
  admin: ["demo@quantops.local", "quantops-demo"],
  freeMember: ["user@quantops.local", "quantops-user"],
  paidMember: ["paid.member@quantops.local", "Meridian-QA-2026"],
  freeManager: ["free.manager@quantops.local", "Meridian-QA-2026"],
  paidManager: ["paid.manager@quantops.local", "Meridian-QA-2026"],
  adminNoPaid: ["admin.nopaid@quantops.local", "Meridian-QA-2026"],
  pastDue: ["pastdue@quantops.local", "Meridian-QA-2026"],
  anon: null,
};

const VIEWPORTS = {
  desktop: { width: 1440, height: 940 },
  tablet: { width: 1024, height: 820 },
  mobile: { width: 390, height: 844 },
};

const [personaKey = "admin", viewportKey = "desktop", theme = "light"] = process.argv.slice(2);
const targets = (process.env.QA_ROUTES ?? "/overview")
  .split(",")
  .map((route) => route.trim())
  .filter(Boolean)
  .map((route) => (route.startsWith("/") ? route : `/${route}`));

const browser = await chromium.launch();
const context = await browser.newContext({
  viewport: VIEWPORTS[viewportKey],
  colorScheme: theme === "dark" ? "dark" : "light",
  deviceScaleFactor: 1,
});
const page = await context.newPage();
const consoleErrors = [];
page.on("console", (message) => {
  if (message.type() === "error") consoleErrors.push(message.text());
});
page.on("pageerror", (error) => consoleErrors.push(`pageerror: ${error.message}`));

await page.addInitScript((t) => {
  window.localStorage.setItem("quantops.theme", t);
}, theme);

const credentials = PERSONAS[personaKey];
if (credentials) {
  const [email, password] = credentials;
  const response = await page.request.post(`${BASE.replace("5173", "8000")}/api/auth/login`, {
    data: { email, password },
  });
  if (!response.ok()) throw new Error(`login failed for ${personaKey}: ${response.status()}`);
  const cookies = await page.request.storageState();
  await context.addCookies(
    cookies.cookies.map((cookie) => ({ ...cookie, domain: "127.0.0.1", path: "/" })),
  );
}

for (const target of targets) {
  const url = `${BASE}${target}`;
  await page.goto(url, { waitUntil: "domcontentloaded" });
  await page.waitForTimeout(2200);
  if (process.env.QA_CLICK) {
    for (const selector of process.env.QA_CLICK.split("|")) {
      await page.locator(selector).first().click();
      await page.waitForTimeout(700);
    }
  }
  const slug = target.replace(/[^a-z0-9]+/gi, "-").replace(/^-|-$/g, "") || "root";
  const file = path.join(OUT, `${personaKey}_${viewportKey}_${theme}_${slug}.png`);
  const clip = process.env.QA_CLIP;
  if (clip) {
    const locator = page.locator(clip).first();
    await locator.scrollIntoViewIfNeeded();
    await page.waitForTimeout(400);
    await locator.screenshot({ path: file });
  } else {
    await page.screenshot({ path: file, fullPage: true });
  }
  // Horizontal-overflow probe.
  const overflow = await page.evaluate(() => ({
    scrollWidth: document.documentElement.scrollWidth,
    clientWidth: document.documentElement.clientWidth,
    offenders: Array.from(document.querySelectorAll("*"))
      .filter((node) => node.scrollWidth > node.clientWidth + 2 && getComputedStyle(node).overflowX === "visible")
      .slice(0, 6)
      .map((node) => `${node.tagName.toLowerCase()}.${String(node.className).slice(0, 60)} (${node.scrollWidth}>${node.clientWidth})`),
  }));
  console.log(
    `${file}\n  overflow=${overflow.scrollWidth > overflow.clientWidth ? "YES" : "no"} (${overflow.scrollWidth}/${overflow.clientWidth})`
    + (overflow.offenders.length ? `\n  offenders: ${overflow.offenders.join(" | ")}` : ""),
  );
}

if (consoleErrors.length) {
  console.log(`\nCONSOLE ERRORS (${consoleErrors.length}):`);
  for (const message of consoleErrors.slice(0, 12)) console.log("  - " + message);
}

await browser.close();
