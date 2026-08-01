import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import {
  getCurrentUser,
  getHealth,
  getWorkspace,
  logout as logoutRequest,
  setActiveOrganizationId,
  setApiAuth,
} from "../api/client";
import type {
  AuthResponse,
  HealthResponse,
  Organization,
  WorkspacePayload,
} from "../api/types";

const ORG_STORAGE_KEY = "quantops.organization_id";

export function isAdminRole(role: unknown): boolean {
  return String(role ?? "user").toLowerCase() === "admin";
}

export interface AppSession {
  /** Current authenticated user + org membership, or null when signed out. */
  auth: AuthResponse | null;
  /** True until the initial "who am I" bootstrap resolves. */
  isLoading: boolean;
  organizations: Organization[];
  activeOrgId: string | null;
  activeOrganization: Organization | undefined;
  workspace: WorkspacePayload | null;
  health: HealthResponse | null;
  backendOnline: boolean;
  isAdminAccess: boolean;
  hasPremiumAccess: boolean;
  handleLogin: (nextAuth: AuthResponse) => void;
  handleLogout: () => Promise<void>;
  switchOrganization: (organizationId: string) => void;
  /** Re-fetch health + workspace (used after org switch). */
  refresh: () => Promise<void>;
}

/**
 * Shared authentication / workspace session, extracted from App.tsx so the
 * Apollo shell can reuse the exact same login, org-switching, and premium-gating
 * behavior as the existing console. Owns only cross-cutting session concerns;
 * per-screen data fetching lives in the screens themselves.
 */
export function useAppSession(): AppSession {
  const [auth, setAuth] = useState<AuthResponse | null>(null);
  const [organizations, setOrganizations] = useState<Organization[]>([]);
  const [activeOrgId, setActiveOrgId] = useState<string | null>(null);
  const [workspace, setWorkspace] = useState<WorkspacePayload | null>(null);
  const [health, setHealth] = useState<HealthResponse | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const refreshVersion = useRef(0);

  const refresh = useCallback(async (authed: boolean = Boolean(auth)) => {
    const version = ++refreshVersion.current;
    const [nextHealth, nextWorkspace] = await Promise.all([
      getHealth().catch(() => null),
      authed ? getWorkspace().catch(() => null) : Promise.resolve(null),
    ]);
    if (version !== refreshVersion.current) return;
    setHealth(nextHealth);
    setWorkspace(nextWorkspace);
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

  const isAdminAccess = isAdminRole(auth?.user.role);

  const hasPremiumAccess = useMemo(() => {
    if (isAdminAccess) return true;
    const subscription = workspace?.subscription;
    const plan = String(subscription?.plan ?? "free");
    const status = String(subscription?.status ?? "");
    return plan !== "free" && status === "active";
  }, [workspace?.subscription, isAdminAccess]);

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
    void refresh(true);
  }, [refresh]);

  const handleLogout = useCallback(async () => {
    refreshVersion.current += 1;
    setAuth(null);
    setWorkspace(null);
    setOrganizations([]);
    setActiveOrgId(null);
    setHealth(null);
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
    setActiveOrgId(organizationId);
    setActiveOrganizationId(organizationId);
    window.localStorage.setItem(ORG_STORAGE_KEY, organizationId);
    void refresh(true);
  }, [organizations, refresh]);

  const activeOrganization = useMemo(
    () => organizations.find((org) => org.id === activeOrgId),
    [organizations, activeOrgId],
  );

  return {
    auth,
    isLoading,
    organizations,
    activeOrgId,
    activeOrganization,
    workspace,
    health,
    backendOnline: health?.status === "ok",
    isAdminAccess,
    hasPremiumAccess,
    handleLogin,
    handleLogout,
    switchOrganization,
    refresh: () => refresh(),
  };
}
