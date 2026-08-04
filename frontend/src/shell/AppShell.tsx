import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { Link, NavLink, useLocation, useNavigate } from "react-router";
import {
  Activity,
  BookOpen,
  Building2,
  ChevronDown,
  CreditCard,
  Database,
  FlaskConical,
  Gauge,
  Layers,
  LogOut,
  Menu,
  Moon,
  Newspaper,
  RefreshCw,
  Search,
  ShieldAlert,
  Sparkles,
  Sun,
  UserCircle,
  Users,
  X,
} from "lucide-react";

import { BrandMark, BrandWord, Button, IconButton, StatusIndicator, Tag, useDismissable } from "../ui";
import type { AppSession } from "../session/useAppSession";
import { ORG_ROLE_SHORT } from "../access/model";
import {
  NAV_GROUPS,
  NAV_ITEMS,
  navItemForPath,
  visibleNavItems,
  type IconName,
  type NavItem,
} from "./navigation";
import { useNavCollapsed, useTelemetryConsent, useThemeMode } from "./preferences";
import { useBackgroundJobs } from "./useBackgroundJobs";

const ICONS: Record<IconName, ReactNode> = {
  overview: <Gauge size={16} />,
  strategies: <Layers size={16} />,
  backtests: <FlaskConical size={16} />,
  paper: <Activity size={16} />,
  research: <Sparkles size={16} />,
  sentiment: <Newspaper size={16} />,
  workspace: <Building2 size={16} />,
  management: <Users size={16} />,
  admin: <ShieldAlert size={16} />,
  account: <UserCircle size={16} />,
  plans: <CreditCard size={16} />,
  learn: <BookOpen size={16} />,
};

export interface AppShellProps {
  session: AppSession;
  children: ReactNode;
}

function relativeTime(iso: string | undefined): string {
  if (!iso) return "unknown";
  const value = new Date(iso).getTime();
  if (Number.isNaN(value)) return "unknown";
  const minutes = Math.max(0, Math.round((Date.now() - value) / 60_000));
  if (minutes < 1) return "just now";
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.round(minutes / 60);
  return hours < 24 ? `${hours}h ago` : `${Math.round(hours / 24)}d ago`;
}

export function AppShell({ session, children }: AppShellProps) {
  const { access } = session;
  const location = useLocation();
  const navigate = useNavigate();
  const { themeMode, resolvedTheme, setThemeMode } = useThemeMode();
  const { telemetryConsent, setTelemetryConsent } = useTelemetryConsent();
  const { navCollapsed } = useNavCollapsed();

  const [mobileNavOpen, setMobileNavOpen] = useState(false);
  const [paletteOpen, setPaletteOpen] = useState(false);
  const [workspaceMenuOpen, setWorkspaceMenuOpen] = useState(false);
  const [identityMenuOpen, setIdentityMenuOpen] = useState(false);
  const [activityMenuOpen, setActivityMenuOpen] = useState(false);

  const items = useMemo(
    () => visibleNavItems({
      viewManagement: access.viewManagement.allowed,
      administerPlatform: access.administerPlatform.allowed,
    }),
    [access.viewManagement.allowed, access.administerPlatform.allowed],
  );

  const activeItem = navItemForPath(location.pathname);
  const jobs = useBackgroundJobs(access.isAuthenticated, session.activeOrgId);

  // Close transient surfaces on navigation.
  useEffect(() => {
    setMobileNavOpen(false);
    setPaletteOpen(false);
    setWorkspaceMenuOpen(false);
    setIdentityMenuOpen(false);
    setActivityMenuOpen(false);
  }, [location.pathname]);

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k") {
        event.preventDefault();
        setPaletteOpen((open) => !open);
      }
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, []);

  const workspaceMenuRef = useDismissable(workspaceMenuOpen, () => setWorkspaceMenuOpen(false));
  const identityMenuRef = useDismissable(identityMenuOpen, () => setIdentityMenuOpen(false));
  const activityMenuRef = useDismissable(activityMenuOpen, () => setActivityMenuOpen(false));

  const nextTheme = resolvedTheme === "dark" ? "light" : "dark";

  return (
    <div className={navCollapsed ? "shell shell--nav-collapsed" : "shell"}>
      <a className="skip-link" href="#main-content">Skip to main content</a>

      <nav className="shell-nav" aria-label="Primary">
        <Link to="/overview" className="shell-brand">
          <BrandMark />
          <BrandWord />
        </Link>
        <div className="shell-nav__scroll">
          {NAV_GROUPS.map((group) => {
            const groupItems = items.filter((item) => item.group === group.id);
            if (!groupItems.length) return null;
            return (
              <div className="nav-group" key={group.id}>
                <span className="nav-group__label">{group.label}</span>
                {groupItems.map((item) => (
                  <NavEntry key={item.id} item={item} activeId={activeItem?.id} pathname={location.pathname} />
                ))}
              </div>
            );
          })}
        </div>
        <div className="shell-nav__foot">
          <PlanSummary session={session} />
        </div>
      </nav>

      <div className="shell-main">
        <header className="shell-topbar">
          <IconButton
            label="Open navigation"
            className="shell-topbar__mobile-brand"
            onClick={() => setMobileNavOpen(true)}
          >
            <Menu size={18} />
          </IconButton>
          <Link to="/overview" className="shell-topbar__mobile-brand" aria-label="Meridian home">
            <BrandMark size={24} />
            <BrandWord descriptor={null} />
          </Link>

          <button type="button" className="command-trigger" onClick={() => setPaletteOpen(true)}>
            <Search size={14} aria-hidden="true" />
            <span className="command-trigger__label">Search screens and actions</span>
            <kbd>Ctrl K</kbd>
          </button>

          <div className="shell-topbar__spacer" />

          <div className="shell-topbar__actions">
            <StatusIndicator tone={session.backendOnline ? "good" : "warn"}>
              <span className="ui-sr-only">Backend status: </span>
              {session.backendOnline ? "API online" : session.health ? "API degraded" : "Checking API"}
            </StatusIndicator>

            <div className="workspace-switcher" ref={activityMenuRef}>
              <IconButton
                label={`Background activity${jobs.activeCount ? ` — ${jobs.activeCount} running` : ""}`}
                aria-expanded={activityMenuOpen}
                onClick={() => setActivityMenuOpen((open) => !open)}
              >
                <Activity size={16} />
                {jobs.activeCount ? (
                  <span className="ui-sr-only">{jobs.activeCount} jobs running</span>
                ) : null}
              </IconButton>
              {jobs.activeCount ? <Tag tone="info">{jobs.activeCount}</Tag> : null}
              {activityMenuOpen ? (
                <div className="menu-pop" role="dialog" aria-label="Background activity">
                  <span className="menu-pop__label">Background jobs</span>
                  {jobs.error ? (
                    <span className="menu-pop__meta">{jobs.error}</span>
                  ) : jobs.entries == null ? (
                    <span className="menu-pop__meta">Loading job activity…</span>
                  ) : jobs.entries.length === 0 ? (
                    <span className="menu-pop__meta">
                      No jobs have run in this workspace yet. Start a backtest, research run, or sentiment scan.
                    </span>
                  ) : (
                    <div className="activity-list">
                      {jobs.entries.slice(0, 8).map((entry) => (
                        <Link key={entry.id} to={entry.path} className="activity-item">
                          <StatusIndicator
                            tone={
                              entry.status === "completed"
                                ? entry.warningCount
                                  ? "warn"
                                  : "good"
                                : entry.status === "failed" || entry.status === "interrupted"
                                  ? "bad"
                                  : "info"
                            }
                            busy={entry.status === "running" || entry.status === "queued"}
                          >
                            <span className="ui-sr-only">{entry.status}</span>
                          </StatusIndicator>
                          <span className="activity-item__body">
                            <span className="activity-item__title">{entry.kind} · {entry.label}</span>
                            <span className="activity-item__meta">
                              {entry.status}
                              {entry.warningCount ? ` · ${entry.warningCount} warning${entry.warningCount === 1 ? "" : "s"}` : ""}
                              {" · "}
                              {relativeTime(entry.updatedAtUtc)}
                            </span>
                          </span>
                        </Link>
                      ))}
                    </div>
                  )}
                  <div className="menu-pop__divider" />
                  <button type="button" className="menu-pop__item" onClick={() => void jobs.reload()}>
                    <RefreshCw size={15} aria-hidden="true" />
                    Refresh activity
                  </button>
                </div>
              ) : null}
            </div>

            <IconButton
              label={`Switch to ${nextTheme} theme`}
              onClick={() => setThemeMode(nextTheme)}
            >
              {resolvedTheme === "dark" ? <Sun size={16} /> : <Moon size={16} />}
            </IconButton>

            {session.organizations.length > 1 ? (
              <div className="workspace-switcher" ref={workspaceMenuRef}>
                <button
                  type="button"
                  className="workspace-switcher__button"
                  aria-expanded={workspaceMenuOpen}
                  aria-haspopup="menu"
                  onClick={() => setWorkspaceMenuOpen((open) => !open)}
                >
                  <Building2 size={14} aria-hidden="true" />
                  <span className="workspace-switcher__name">
                    {session.activeOrganization?.name ?? "Select workspace"}
                  </span>
                  <ChevronDown size={14} aria-hidden="true" />
                </button>
                {workspaceMenuOpen ? (
                  <div className="menu-pop" role="menu" aria-label="Workspaces">
                    <span className="menu-pop__label">Workspaces</span>
                    {session.organizations.map((organization) => (
                      <button
                        key={organization.id}
                        type="button"
                        role="menuitem"
                        className="menu-pop__item"
                        aria-current={organization.id === session.activeOrgId}
                        onClick={() => {
                          session.switchOrganization(organization.id);
                          setWorkspaceMenuOpen(false);
                        }}
                      >
                        <Building2 size={15} aria-hidden="true" />
                        <span>{organization.name}</span>
                        <Tag tone="neutral">{ORG_ROLE_SHORT[organization.role === "owner" || organization.role === "admin" || organization.role === "member" ? organization.role : "unknown"]}</Tag>
                      </button>
                    ))}
                    <span className="menu-pop__meta">
                      Switching workspace reloads tenant-scoped data. Your role can differ per workspace.
                    </span>
                  </div>
                ) : null}
              </div>
            ) : session.activeOrganization ? (
              <span className="workspace-pill" title={`Workspace: ${session.activeOrganization.name}`}>
                <Building2 size={13} aria-hidden="true" />
                {session.activeOrganization.name}
              </span>
            ) : null}

            <div className="workspace-switcher" ref={identityMenuRef}>
              <button
                type="button"
                className="identity-chip"
                aria-expanded={identityMenuOpen}
                aria-haspopup="menu"
                onClick={() => setIdentityMenuOpen((open) => !open)}
              >
                <span className="avatar" aria-hidden="true">{access.initials}</span>
                <span className="identity-chip__text">
                  <span className="identity-chip__name">{access.displayName}</span>
                  <span className="identity-chip__role">
                    {access.isPlatformAdmin ? "Administrator" : ORG_ROLE_SHORT[access.orgRole]}
                  </span>
                </span>
                <ChevronDown size={14} aria-hidden="true" />
              </button>
              {identityMenuOpen ? (
                <div className="menu-pop" role="menu" aria-label="Account">
                  <span className="menu-pop__label">Signed in</span>
                  <span className="menu-pop__meta">{access.email}</span>
                  <div className="menu-pop__divider" />
                  <span className="menu-pop__label">Plan and role</span>
                  <span className="menu-pop__meta">
                    Plan: {access.subscription.planLabel} · {access.subscription.stateLabel}
                    <br />
                    Workspace role: {access.orgRoleLabel}
                    <br />
                    Platform role: {access.isPlatformAdmin ? "Administrator" : "Standard account"}
                  </span>
                  <div className="menu-pop__divider" />
                  <Link to="/account" role="menuitem" className="menu-pop__item">
                    <UserCircle size={15} aria-hidden="true" />
                    Account & security
                  </Link>
                  <Link to="/pricing" role="menuitem" className="menu-pop__item">
                    <CreditCard size={15} aria-hidden="true" />
                    Plans & billing
                  </Link>
                  <Link to="/learn" role="menuitem" className="menu-pop__item">
                    <BookOpen size={15} aria-hidden="true" />
                    Learn the platform
                  </Link>
                  <div className="menu-pop__divider" />
                  <span className="menu-pop__label">Product analytics</span>
                  <button
                    type="button"
                    role="menuitem"
                    className="menu-pop__item"
                    onClick={() => setTelemetryConsent(telemetryConsent === "granted" ? "denied" : "granted")}
                  >
                    <Database size={15} aria-hidden="true" />
                    {telemetryConsent === "granted" ? "Turn analytics off" : "Turn analytics on"}
                  </button>
                  <div className="menu-pop__divider" />
                  <span className="menu-pop__label">Theme</span>
                  {(["light", "dark", "system"] as const).map((mode) => (
                    <button
                      key={mode}
                      type="button"
                      role="menuitem"
                      className="menu-pop__item"
                      aria-current={themeMode === mode}
                      onClick={() => setThemeMode(mode)}
                    >
                      {mode === "light" ? <Sun size={15} aria-hidden="true" /> : mode === "dark" ? <Moon size={15} aria-hidden="true" /> : <Gauge size={15} aria-hidden="true" />}
                      {mode === "system" ? "Match system" : `${mode[0].toUpperCase()}${mode.slice(1)}`}
                    </button>
                  ))}
                  <div className="menu-pop__divider" />
                  <button
                    type="button"
                    role="menuitem"
                    className="menu-pop__item menu-pop__item--danger"
                    onClick={() => void session.handleLogout()}
                  >
                    <LogOut size={15} aria-hidden="true" />
                    Sign out
                  </button>
                </div>
              ) : null}
            </div>
          </div>
        </header>

        <p className="sim-strip">
          <ShieldAlert size={13} aria-hidden="true" />
          <span>
            <strong>Simulated capital only.</strong> No broker is connected and no real-money orders are placed.
            Backtests describe the past and do not predict future results.
          </span>
        </p>

        <main className="shell-content" id="main-content" tabIndex={-1}>
          {children}
        </main>

        <footer className="shell-footer">
          <div className="shell-footer__inner">
            <span>Meridian · research and simulated paper trading. Informational only, not financial advice.</span>
            <span className="shell-footer__links">
              <a href="/privacy">Privacy</a>
              <a href="/terms">Terms</a>
              <a href="/risk-disclaimer">Risk disclaimer</a>
              <a href="/compliance">Compliance</a>
            </span>
          </div>
        </footer>
      </div>

      {mobileNavOpen ? (
        <MobileNav
          items={items}
          activeId={activeItem?.id}
          pathname={location.pathname}
          session={session}
          onClose={() => setMobileNavOpen(false)}
        />
      ) : null}

      {paletteOpen ? (
        <CommandPalette
          items={items}
          onClose={() => setPaletteOpen(false)}
          onSelect={(path) => {
            setPaletteOpen(false);
            navigate(path);
          }}
        />
      ) : null}
    </div>
  );
}

function NavEntry({ item, activeId, pathname }: { item: NavItem; activeId?: string; pathname: string }) {
  const isActive = activeId === item.id;
  return (
    <>
      <NavLink
        to={item.path}
        end={item.path === "/overview"}
        className={({ isActive: linkActive }) =>
          [
            "nav-item",
            (linkActive || isActive) && "nav-item--active",
            item.requires === "administerPlatform" && "nav-item--elevated",
          ]
            .filter(Boolean)
            .join(" ")
        }
        title={item.description}
      >
        {ICONS[item.icon]}
        <span className="nav-item__label">{item.label}</span>
      </NavLink>
      {isActive && item.children?.length ? (
        <div className="nav-sub">
          {item.children.map((child) => (
            <NavLink
              key={child.id}
              to={child.path}
              end
              className={({ isActive: childActive }) =>
                ["nav-sub__item", (childActive || pathname === child.path) && "nav-sub__item--active"]
                  .filter(Boolean)
                  .join(" ")
              }
            >
              {child.label}
            </NavLink>
          ))}
        </div>
      ) : null}
    </>
  );
}

function PlanSummary({ session }: { session: AppSession }) {
  const { access } = session;
  return (
    <div className="plan-summary">
      <div className="plan-summary__row">
        <span className="plan-summary__label">Plan</span>
        <span className="plan-summary__value">{access.subscription.planLabel}</span>
      </div>
      <div className="plan-summary__row">
        <span className="plan-summary__label">Status</span>
        <Tag
          tone={
            access.subscription.needsBillingAttention
              ? "bad"
              : access.hasPremium
                ? "good"
                : "neutral"
          }
        >
          {access.subscription.stateLabel}
        </Tag>
      </div>
      <div className="plan-summary__row">
        <span className="plan-summary__label">Role</span>
        <span className="plan-summary__value">
          {access.isPlatformAdmin ? "Administrator" : ORG_ROLE_SHORT[access.orgRole]}
        </span>
      </div>
      <p className="plan-summary__note">
        {access.premiumViaAdminOverride
          ? "Premium workflows are open because this account has the platform administrator role, not because of a workspace subscription."
          : access.hasPremium
            ? "Premium research and compute workflows are available to this workspace."
            : "Premium research and compute workflows need an active paid plan for this workspace."}
      </p>
      {!access.hasPremium ? (
        <Button variant="secondary" size="sm" block onClick={() => { window.location.assign("/pricing"); }}>
          Compare plans
        </Button>
      ) : null}
    </div>
  );
}

function MobileNav({ items, activeId, pathname, session, onClose }: {
  items: NavItem[];
  activeId?: string;
  pathname: string;
  session: AppSession;
  onClose: () => void;
}) {
  return (
    <>
      <div className="ui-scrim" onClick={onClose} aria-hidden="true" />
      <div className="ui-drawer" role="dialog" aria-modal="true" aria-label="Navigation">
        <div className="shell-brand" style={{ justifyContent: "space-between" }}>
          <span style={{ display: "flex", alignItems: "center", gap: 10 }}>
            <BrandMark />
            <BrandWord />
          </span>
          <IconButton label="Close navigation" onClick={onClose}>
            <X size={18} />
          </IconButton>
        </div>
        <div className="shell-nav__scroll">
          {NAV_GROUPS.map((group) => {
            const groupItems = items.filter((item) => item.group === group.id);
            if (!groupItems.length) return null;
            return (
              <div className="nav-group" key={group.id}>
                <span className="nav-group__label">{group.label}</span>
                {groupItems.map((item) => (
                  <NavEntry key={item.id} item={item} activeId={activeId} pathname={pathname} />
                ))}
              </div>
            );
          })}
        </div>
        <div className="shell-nav__foot">
          {/* The topbar hides the status label on small screens, so it lives here. */}
          <StatusIndicator tone={session.backendOnline ? "good" : "warn"}>
            {session.backendOnline ? "API online" : session.health ? "API degraded" : "Checking API"}
          </StatusIndicator>
          <PlanSummary session={session} />
          <Button variant="ghost" size="sm" block icon={<LogOut size={14} />} onClick={() => void session.handleLogout()}>
            Sign out
          </Button>
        </div>
      </div>
    </>
  );
}

function CommandPalette({ items, onClose, onSelect }: {
  items: NavItem[];
  onClose: () => void;
  onSelect: (path: string) => void;
}) {
  const [query, setQuery] = useState("");
  const [cursor, setCursor] = useState(0);
  const inputRef = useRef<HTMLInputElement | null>(null);

  useEffect(() => {
    inputRef.current?.focus();
  }, []);

  const options = useMemo(() => {
    const flat: Array<{ id: string; label: string; path: string; description: string; group: string }> = [];
    for (const item of items) {
      flat.push({ id: item.id, label: item.label, path: item.path, description: item.description, group: "Screens" });
      for (const child of item.children ?? []) {
        flat.push({
          id: child.id,
          label: `${item.label} · ${child.label}`,
          path: child.path,
          description: child.description,
          group: "Screens",
        });
      }
    }
    const needle = query.trim().toLowerCase();
    if (!needle) return flat;
    return flat.filter((option) =>
      option.label.toLowerCase().includes(needle) || option.description.toLowerCase().includes(needle),
    );
  }, [items, query]);

  useEffect(() => {
    setCursor(0);
  }, [query]);

  const onKeyDown = useCallback((event: React.KeyboardEvent) => {
    if (event.key === "Escape") {
      onClose();
      return;
    }
    if (event.key === "ArrowDown") {
      event.preventDefault();
      setCursor((value) => Math.min(options.length - 1, value + 1));
    } else if (event.key === "ArrowUp") {
      event.preventDefault();
      setCursor((value) => Math.max(0, value - 1));
    } else if (event.key === "Enter" && options[cursor]) {
      event.preventDefault();
      onSelect(options[cursor].path);
    }
  }, [cursor, onClose, onSelect, options]);

  return (
    <>
      <div className="ui-scrim" onClick={onClose} aria-hidden="true" />
      <div className="palette">
        <div className="palette__panel" role="dialog" aria-modal="true" aria-label="Command palette">
          <div className="palette__input-row">
            <Search size={16} aria-hidden="true" />
            <input
              ref={inputRef}
              className="palette__input"
              type="search"
              value={query}
              placeholder="Go to a screen…"
              aria-label="Search screens and actions"
              aria-controls="palette-results"
              onChange={(event) => setQuery(event.target.value)}
              onKeyDown={onKeyDown}
            />
            <IconButton label="Close search" onClick={onClose}>
              <X size={15} />
            </IconButton>
          </div>
          <div className="palette__results" id="palette-results" role="listbox" aria-label="Results">
            {options.length === 0 ? (
              <p className="palette__empty">No screen matches “{query}”.</p>
            ) : (
              <>
                <span className="palette__group-label">Screens</span>
                {options.map((option, index) => (
                  <button
                    key={option.id}
                    type="button"
                    role="option"
                    aria-selected={index === cursor}
                    data-active={index === cursor}
                    className="palette__item"
                    onMouseEnter={() => setCursor(index)}
                    onClick={() => onSelect(option.path)}
                  >
                    <Search size={14} aria-hidden="true" />
                    <span>{option.label}</span>
                    <span className="palette__item-desc">{option.path}</span>
                  </button>
                ))}
              </>
            )}
          </div>
        </div>
      </div>
    </>
  );
}

export { NAV_ITEMS };
