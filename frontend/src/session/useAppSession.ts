import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import {
  getBillingStatus,
  getCurrentUser,
  getHealth,
  getWorkspace,
  logout as logoutRequest,
  setActiveOrganizationId,
  setApiAuth,
} from "../api/client";
import type {
  AuthResponse,
  BillingStatusPayload,
  HealthResponse,
  Organization,
  WorkspacePayload,
} from "../api/types";
import { buildAccessContext, isAdminRole, type AccessContext } from "../access/model";

const ORG_STORAGE_KEY = "quantops.organization_id";

export { isAdminRole };

export interface AppSession {
  /** Current authenticated user + org membership, or null when signed out. */
  auth: AuthResponse | null;
  /** True until the initial "who am I" bootstrap resolves. */
  isLoading: boolean;
  /** True while a background refresh of workspace/billing/health is in flight. */
  isRefreshing: boolean;
  organizations: Organization[];
  activeOrgId: string | null;
  activeOrganization: Organization | undefined;
  workspace: WorkspacePayload | null;
  billing: BillingStatusPayload | null;
  health: HealthResponse | null;
  backendOnline: boolean;
  /** True when the authenticated workspace payload could not be loaded. */
  workspaceError: boolean;
  isAdminAccess: boolean;
  hasPremiumAccess: boolean;
  /** Derived subscription × role capability set used across the interface. */
  access: AccessContext;
  handleLogin: (nextAuth: AuthResponse) => void;
  handleLogout: () => Promise<void>;
  switchOrganization: (organizationId: string) => void;
  /** Re-fetch health + workspace + billing status (used after org switch). */
  refresh: () => Promise<void>;
}

/**
 * Shared authentication / workspace session.
 *
 * Owns only cross-cutting session concerns — identity, organization selection,
 * subscription entitlement, and backend health — so every screen can read one
 * consistent access context. Per-screen data fetching lives in the screens.
 */
export function useAppSession(): AppSession {
  const [auth, setAuth] = useState<AuthResponse | null>(null);
  const [organizations, setOrganizations] = useState<Organization[]>([]);
  const [activeOrgId, setActiveOrgId] = useState<string | null>(null);
  const [workspace, setWorkspace] = useState<WorkspacePayload | null>(null);
  const [billing, setBilling] = useState<BillingStatusPayload | null>(null);
  const [health, setHealth] = useState<HealthResponse | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [isRefreshing, setIsRefreshing] = useState(false);
  const [workspaceError, setWorkspaceError] = useState(false);
  const refreshVersion = useRef(0);

  const refresh = useCallback(async (authed: boolean = Boolean(auth)) => {
    const version = ++refreshVersion.current;
    setIsRefreshing(true);
    const [nextHealth, nextWorkspace, nextBilling] = await Promise.all([
      getHealth().catch(() => null),
      authed ? getWorkspace().catch(() => null) : Promise.resolve(null),
      authed ? getBillingStatus().catch(() => null) : Promise.resolve(null),
    ]);
    if (version !== refreshVersion.current) return;
    setHealth(nextHealth);
    setWorkspace(nextWorkspace);
    setBilling(nextBilling);
    setWorkspaceError(authed && nextWorkspace === null);
    setIsRefreshing(false);
  }, [auth]);

  // Bootstrap: restore the session from the server on first mount.
  useEffect(() => {
    let active = true;
    const storedOrg = window.localStorage.getItem(ORG_STORAGE_KEY);
    setApiAuth(null, storedOrg);
    void (async () => {
      try {
        const me = await getCurrentUser();
        const nextAuth: AuthResponse = {
          user: me.user,
          organizations: me.organizations,
          active_organization_id: me.active_organization_id,
        };
        if (!active) return;
        setAuth(nextAuth);
        setOrganizations(me.organizations);
        setActiveOrgId(me.active_organization_id);
        setActiveOrganizationId(me.active_organization_id);
        await refresh(true);
      } catch {
        if (!active) return;
        window.localStorage.removeItem(ORG_STORAGE_KEY);
        setApiAuth(null, null);
        await refresh(false);
      } finally {
        if (active) setIsLoading(false);
      }
    })();
    return () => {
      active = false;
      refreshVersion.current += 1;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const activeOrganization = useMemo(
    () => organizations.find((org) => org.id === activeOrgId),
    [organizations, activeOrgId],
  );

  const backendOnline = health?.status === "ok";

  const access = useMemo(
    () => buildAccessContext({
      user: auth?.user ?? null,
      organization: activeOrganization ?? null,
      workspace,
      billing,
      backendOnline,
    }),
    [auth?.user, activeOrganization, workspace, billing, backendOnline],
  );

  const handleLogin = useCallback((nextAuth: AuthResponse) => {
    refreshVersion.current += 1;
    setAuth(nextAuth);
    setOrganizations(nextAuth.organizations);
    setActiveOrgId(nextAuth.active_organization_id);
    setApiAuth(null, nextAuth.active_organization_id);
    if (nextAuth.active_organization_id) {
      window.localStorage.setItem(ORG_STORAGE_KEY, nextAuth.active_organization_id);
    }
    setWorkspace(null);
    setBilling(null);
    setWorkspaceError(false);
    void refresh(true);
  }, [refresh]);

  const handleLogout = useCallback(async () => {
    refreshVersion.current += 1;
    setAuth(null);
    setWorkspace(null);
    setBilling(null);
    setOrganizations([]);
    setActiveOrgId(null);
    setHealth(null);
    setWorkspaceError(false);
    setApiAuth(null, null);
    window.localStorage.removeItem(ORG_STORAGE_KEY);
    try {
      await logoutRequest();
    } catch {
      // Local logout should still clear client state if the server session expired.
    }
  }, []);

  const switchOrganization = useCallback((organizationId: string) => {
    if (!organizations.some((organization) => organization.id === organizationId)) return;
    refreshVersion.current += 1;
    setWorkspace(null);
    setBilling(null);
    setWorkspaceError(false);
    setActiveOrgId(organizationId);
    setActiveOrganizationId(organizationId);
    window.localStorage.setItem(ORG_STORAGE_KEY, organizationId);
    void refresh(true);
  }, [organizations, refresh]);

  return {
    auth,
    isLoading,
    isRefreshing,
    organizations,
    activeOrgId,
    activeOrganization,
    workspace,
    billing,
    health,
    backendOnline,
    workspaceError,
    isAdminAccess: access.isPlatformAdmin,
    hasPremiumAccess: access.hasPremium,
    access,
    handleLogin,
    handleLogout,
    switchOrganization,
    refresh: () => refresh(),
  };
}
