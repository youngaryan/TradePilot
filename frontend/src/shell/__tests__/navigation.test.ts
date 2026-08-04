import { describe, expect, it } from "vitest";

import { LEGACY_HASH_ROUTES, NAV_ITEMS, navItemForPath, visibleNavItems } from "../navigation";

const STANDARD = { viewManagement: false, administerPlatform: false };
const MANAGER = { viewManagement: true, administerPlatform: false };
const ADMIN = { viewManagement: true, administerPlatform: true };

function ids(capabilities: { viewManagement: boolean; administerPlatform: boolean }) {
  return visibleNavItems(capabilities).map((item) => item.id);
}

describe("role-aware navigation", () => {
  it("gives a standard member every workspace-level screen and no elevated sections", () => {
    const visible = ids(STANDARD);
    expect(visible).toEqual([
      "overview",
      "strategies",
      "backtests",
      "paper",
      "research",
      "sentiment",
      "workspace",
      "account",
      "plans",
      "learn",
    ]);
    expect(visible).not.toContain("management");
    expect(visible).not.toContain("admin");
  });

  it("adds workspace management for a manager but never platform administration", () => {
    const visible = ids(MANAGER);
    expect(visible).toContain("management");
    expect(visible).not.toContain("admin");
  });

  it("adds platform administration only for an administrator", () => {
    expect(ids(ADMIN)).toContain("admin");
  });

  it("keeps administration in its own navigation group so it reads as elevated", () => {
    const admin = NAV_ITEMS.find((item) => item.id === "admin");
    const management = NAV_ITEMS.find((item) => item.id === "management");
    expect(admin?.group).toBe("platform");
    expect(management?.group).toBe("workspace");
    expect(admin?.requires).toBe("administerPlatform");
    expect(management?.requires).toBe("viewManagement");
  });

  it("does not gate any research or account screen behind a role", () => {
    const gated = NAV_ITEMS.filter((item) => item.requires).map((item) => item.id);
    expect(gated).toEqual(["management", "admin"]);
  });

  it("describes every screen so search and page context are never empty", () => {
    for (const item of NAV_ITEMS) {
      expect(item.description.length).toBeGreaterThan(20);
      expect(item.path.startsWith("/")).toBe(true);
    }
  });
});

describe("legacy deep links", () => {
  it("maps every classic console view onto a canonical route", () => {
    expect(LEGACY_HASH_ROUTES).toEqual({
      command: "/overview",
      backtests: "/backtests",
      live: "/paper",
      research: "/research",
      sentiment: "/sentiment",
      workspace: "/workspace",
      admin: "/admin",
      account: "/account",
      pricing: "/pricing",
      system: "/learn",
    });
  });

  it("resolves nested paths to their owning section", () => {
    expect(navItemForPath("/strategies/builder")?.id).toBe("strategies");
    expect(navItemForPath("/strategies/community")?.id).toBe("strategies");
    expect(navItemForPath("/overview")?.id).toBe("overview");
    expect(navItemForPath("/pricing")?.id).toBe("plans");
    expect(navItemForPath("/nowhere")).toBeUndefined();
  });
});
