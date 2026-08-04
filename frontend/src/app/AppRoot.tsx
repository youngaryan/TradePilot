import { Route, Routes } from "react-router";

import App from "../App";
import { LoginScreen } from "../features/LoginScreen";
import { SignIn } from "../features/auth/SignIn";
import { AppShell } from "../shell/AppShell";
import { useAppSession } from "../session/useAppSession";
import { BrandMark, BrandWord, ErrorBoundary } from "../ui";
import { AppRoutes } from "./AppRoutes";

/**
 * Public routes served regardless of authentication state. Legal, compliance,
 * verification and reset links must resolve for someone who is signed out, and
 * must keep working for someone who is signed in.
 */
const PUBLIC_PATHS = ["/privacy", "/terms", "/risk-disclaimer", "/compliance", "/password-reset", "/verify-email"];

function BootSplash() {
  return (
    <div
      role="status"
      aria-live="polite"
      style={{
        minHeight: "100vh",
        display: "flex",
        flexDirection: "column",
        alignItems: "center",
        justifyContent: "center",
        gap: "14px",
        background: "var(--bg-app)",
        color: "var(--text-primary)",
      }}
    >
      <BrandMark size={40} />
      <BrandWord />
      <span style={{ fontSize: "var(--text-xs)", color: "var(--text-tertiary)" }}>Restoring your session…</span>
    </div>
  );
}

/**
 * Application root.
 *
 * One session, one shell, one route table. Public pages resolve first, then the
 * authenticated shell takes over every remaining path so a deep link keeps its
 * address through sign-in.
 */
export function AppRoot() {
  const session = useAppSession();

  const publicRoutes = PUBLIC_PATHS.map((path) => (
    <Route key={path} path={path} element={<LoginScreen onLogin={session.handleLogin} />} />
  ));

  if (session.isLoading) {
    return <BootSplash />;
  }

  if (!session.auth) {
    return (
      <ErrorBoundary area="Sign in">
        <Routes>
          {publicRoutes}
          <Route path="/product" element={<LoginScreen onLogin={session.handleLogin} />} />
          {/* Any other address falls back to sign-in while preserving the URL, so
              the deep link resolves once the session exists. */}
          <Route path="*" element={<SignIn onLogin={session.handleLogin} />} />
        </Routes>
      </ErrorBoundary>
    );
  }

  return (
    <Routes>
      {publicRoutes}
      {/* Compatibility entry point for the classic console address. */}
      <Route path="/classic" element={<App />} />
      <Route path="/classic/*" element={<App />} />
      <Route
        path="*"
        element={
          <AppShell session={session}>
            <AppRoutes session={session} />
          </AppShell>
        }
      />
    </Routes>
  );
}

export default AppRoot;
