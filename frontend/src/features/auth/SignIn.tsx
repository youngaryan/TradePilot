import { useState } from "react";
import { ArrowRight } from "lucide-react";
import { Link } from "react-router";

import { confirmPasswordReset, login as loginRequest, requestPasswordReset, signup as signupRequest } from "../../api/client";
import type { AuthResponse } from "../../api/types";
import { BrandMark, BrandWord, Button, InlineNotice, TextInput } from "../../ui";

/**
 * Primary authentication surface.
 *
 * Three stages on one route so the credential card never moves: sign in, apply
 * for access, and password reset. Demo sign-in is only offered when the build
 * explicitly enables it, so production never advertises shared credentials.
 */

type Stage = "login" | "apply" | "reset";

const demoLoginEnabled = import.meta.env.DEV || import.meta.env.VITE_ENABLE_DEMO_LOGIN === "true";

const STAGE_TITLE: Record<Stage, string> = {
  login: "Sign in",
  apply: "Apply for access",
  reset: "Reset password",
};

const STAGE_BLURB: Record<Stage, string> = {
  login: "Access your research workspace.",
  apply: "Applications are approved on submission. You start on the free plan.",
  reset: "We email a single-use reset token to the address on the account.",
};

export function SignIn({ onLogin }: { onLogin: (auth: AuthResponse) => void }) {
  const [stage, setStage] = useState<Stage>("login");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [displayName, setDisplayName] = useState("");
  const [organizationName, setOrganizationName] = useState("");
  const [resetToken, setResetToken] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  function go(next: Stage) {
    setStage(next);
    setError(null);
    setNotice(null);
  }

  async function run(action: () => Promise<void>, fallback: string) {
    setIsLoading(true);
    setError(null);
    try {
      await action();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : fallback);
    } finally {
      setIsLoading(false);
    }
  }

  const submitLogin = () => run(async () => {
    onLogin(await loginRequest(email, password));
  }, "Sign-in failed.");

  const submitApplication = () => run(async () => {
    onLogin(await signupRequest({
      email,
      password,
      display_name: displayName || email.split("@")[0],
      organization_name: organizationName || "Private desk",
    }));
  }, "Application failed.");

  const sendResetRequest = () => run(async () => {
    setNotice(null);
    await requestPasswordReset(email);
    setNotice("If that account exists, reset instructions are on the way. In local development they are written to artifacts/email_outbox.");
  }, "Could not request a reset.");

  const applyReset = () => run(async () => {
    setNotice(null);
    await confirmPasswordReset(resetToken.trim(), newPassword);
    setResetToken("");
    setNewPassword("");
    setPassword("");
    setStage("login");
    setNotice("Password updated. Sign in with your new password.");
  }, "Reset failed — check the token.");

  const loginAsDemo = () => run(async () => {
    onLogin(await loginRequest("demo@quantops.local", "quantops-demo"));
  }, "Demo sign-in failed.");

  const headerCta = stage === "login"
    ? { label: "Apply for access", run: () => go("apply") }
    : { label: "Sign in", run: () => go("login") };

  return (
    <div className="marketing-shell">
      <header className="marketing-nav">
        <Link to="/" className="marketing-brand" aria-label="Meridian home">
          <BrandMark size={24} />
          <BrandWord descriptor={null} />
        </Link>
        <nav aria-label="Public navigation">
          <Link to="/product">Product</Link>
          <a href="/risk-disclaimer">Risk</a>
          <a href="/privacy">Privacy</a>
        </nav>
        <span style={{ fontSize: "var(--text-xs)", color: "var(--text-tertiary)" }}>
          {stage === "login" ? "New to Meridian?" : "Already a member?"}
        </span>
        <button type="button" className="nav-cta" onClick={headerCta.run}>
          {headerCta.label}
        </button>
      </header>

      <main className="auth-utility-page">
        <section className="signin-card" aria-labelledby="signin-heading">
          <div style={{ marginBottom: 4 }}>
            <BrandMark size={34} />
          </div>
          <h1 id="signin-heading">{STAGE_TITLE[stage]}</h1>
          <p>{STAGE_BLURB[stage]}</p>

          {stage === "login" ? (
            <form
              onSubmit={(event) => {
                event.preventDefault();
                if (!isLoading) void submitLogin();
              }}
              style={{ display: "flex", flexDirection: "column", gap: "var(--space-3)" }}
            >
              <TextInput label="Email" type="email" value={email} onChange={setEmail} autoComplete="email" required />
              <TextInput
                label="Password"
                type="password"
                value={password}
                onChange={setPassword}
                autoComplete="current-password"
                required
              />
              <button
                type="button"
                className="secondary-link"
                style={{ alignSelf: "flex-end", marginTop: "calc(-1 * var(--space-2))" }}
                onClick={() => go("reset")}
              >
                Forgot your password?
              </button>
              <Button type="submit" variant="primary" block disabled={isLoading} iconEnd={<ArrowRight size={16} />}>
                {isLoading ? "Signing in…" : "Sign in"}
              </Button>
              {demoLoginEnabled ? (
                <Button variant="secondary" block disabled={isLoading} onClick={() => void loginAsDemo()}>
                  Enter the demo
                </Button>
              ) : null}
            </form>
          ) : null}

          {stage === "apply" ? (
            <form
              onSubmit={(event) => {
                event.preventDefault();
                if (!isLoading) void submitApplication();
              }}
              style={{ display: "flex", flexDirection: "column", gap: "var(--space-3)" }}
            >
              <TextInput label="Full name" value={displayName} onChange={setDisplayName} autoComplete="name" />
              <TextInput
                label="Desk name"
                value={organizationName}
                onChange={setOrganizationName}
                placeholder="Private desk"
                hint="This becomes your workspace name. You can be a member of more than one."
              />
              <TextInput label="Email" type="email" value={email} onChange={setEmail} autoComplete="email" required />
              <TextInput
                label="Password"
                type="password"
                value={password}
                onChange={setPassword}
                autoComplete="new-password"
                hint="At least 8 characters."
                required
              />
              <Button type="submit" variant="primary" block disabled={isLoading} iconEnd={<ArrowRight size={16} />}>
                {isLoading ? "Submitting…" : "Submit application"}
              </Button>
            </form>
          ) : null}

          {stage === "reset" ? (
            <div style={{ display: "flex", flexDirection: "column", gap: "var(--space-3)" }}>
              <TextInput label="Email" type="email" value={email} onChange={setEmail} autoComplete="email" />
              <Button variant="primary" block disabled={isLoading} onClick={() => void sendResetRequest()}>
                {isLoading ? "Sending…" : "Send instructions"}
              </Button>
              <hr className="ui-divider" />
              <TextInput
                label="Reset token"
                value={resetToken}
                onChange={setResetToken}
                mono
                hint="Paste the single-use token from the reset email."
              />
              <TextInput label="New password" type="password" value={newPassword} onChange={setNewPassword} autoComplete="new-password" />
              <Button
                variant="primary"
                block
                disabled={isLoading || !resetToken.trim() || newPassword.length < 8}
                onClick={() => void applyReset()}
              >
                Set new password
              </Button>
              <button type="button" className="secondary-link" onClick={() => go("login")}>
                Back to sign in
              </button>
            </div>
          ) : null}

          {error ? <InlineNotice tone="bad" role="alert">{error}</InlineNotice> : null}
          {notice ? <InlineNotice tone="good" role="status">{notice}</InlineNotice> : null}

          <InlineNotice tone="info" compact>
            Meridian is a research and simulated paper-trading product. No broker is connected and no real-money
            orders are placed.
          </InlineNotice>
        </section>
      </main>

      <footer className="marketing-footer">
        <span>© Meridian</span>
        <span className="shell-footer__links">
          <Link to="/product" className="secondary-link">Product</Link>
          <a className="secondary-link" href="/privacy">Privacy</a>
          <a className="secondary-link" href="/terms">Terms</a>
          <a className="secondary-link" href="/risk-disclaimer">Risk disclaimer</a>
          <a className="secondary-link" href="/compliance">Compliance</a>
        </span>
      </footer>
    </div>
  );
}

export default SignIn;
