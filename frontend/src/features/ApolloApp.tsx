import { ApolloDashboard } from "./ApolloDashboard";
import { ApolloErrorBoundary } from "./ApolloErrorBoundary";
import { ApolloLogin } from "./ApolloLogin";
import { useAppSession } from "../session/useAppSession";

/**
 * Authenticated wrapper for the Apollo shell.
 *
 * Reuses the same session brain as the existing console (useAppSession):
 * shows the LoginScreen when signed out, and otherwise renders Apollo wired
 * with the real user identity, workspace/org switcher, backend health, premium
 * tier, and sign-out. Screen-level data is loaded from tenant-scoped API
 * endpoints and reports loading, empty, and failure states explicitly.
 */
export function ApolloApp() {
  const session = useAppSession();

  if (session.isLoading) {
    return (
      <div
        style={{
          minHeight: "100vh",
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          fontFamily: "'Archivo Black', 'Space Grotesk', sans-serif",
          fontSize: "22px",
          letterSpacing: ".02em",
          color: "oklch(20% 0.012 255)",
          background: "oklch(97.5% 0.003 255)",
        }}
      >
        APOLLO
      </div>
    );
  }

  if (!session.auth) {
    return <ApolloLogin onLogin={session.handleLogin} />;
  }

  const user = session.auth.user;
  const subscription = session.workspace?.subscription as { plan?: string; status?: string } | undefined;
  return (
    <ApolloErrorBoundary>
      <ApolloDashboard
        userId={user.id}
        userEmail={user.email}
        userRole={user.role}
        planName={subscription?.plan ?? (session.hasPremiumAccess ? "pro" : "free")}
        planStatus={subscription?.status ?? null}
        capabilities={session.workspace?.capabilities}
        userName={user.display_name || user.email}
        workspaceLabel={session.activeOrganization?.name ?? "Workspace"}
        organizations={session.organizations.map((o) => ({ id: o.id, name: o.name }))}
        activeOrgId={session.activeOrgId}
        onSwitchOrg={session.switchOrganization}
        backendOnline={session.backendOnline}
        hasPremium={session.hasPremiumAccess}
        onLogout={() => void session.handleLogout()}
      />
    </ApolloErrorBoundary>
  );
}

export default ApolloApp;
