import { useState, type CSSProperties } from "react";

import { confirmPasswordReset, login as loginRequest, requestPasswordReset, signup as signupRequest } from "../api/client";
import type { AuthResponse } from "../api/types";

/**
 * Apollo sign-in.
 *
 * Standard corporate layout: a slim header with the wordmark, a centered
 * credentials card, and an application path. "Apply" is the real signup
 * (approved on submit); demo accounts and password reset are intact.
 */

function css(s: string): CSSProperties {
  const out: Record<string, string> = {};
  s.split(";").forEach((decl) => {
    const i = decl.indexOf(":");
    if (i === -1) return;
    const prop = decl.slice(0, i).trim();
    const val = decl.slice(i + 1).trim();
    if (!prop) return;
    out[prop.replace(/-([a-z])/g, (_, ch: string) => ch.toUpperCase())] = val;
  });
  return out as CSSProperties;
}

type Stage = "login" | "apply" | "reset";

const demoLoginEnabled = import.meta.env.DEV || import.meta.env.VITE_ENABLE_DEMO_LOGIN === "true";

export function ApolloLogin({ onLogin }: { onLogin: (auth: AuthResponse) => void }) {
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

  const c = {
    bg: "oklch(97.5% 0.003 255)",
    surface: "oklch(100% 0 0)",
    border: "oklch(91% 0.005 255)",
    line: "oklch(94% 0.004 255)",
    text: "oklch(20% 0.012 255)",
    faint: "oklch(52% 0.012 255)",
    loss: "oklch(55% 0.19 25)",
    ok: "oklch(50% 0.14 155)",
    accent: "oklch(62% 0.11 215)",
  };
  const inter = "'Inter', -apple-system, sans-serif";
  const black = "'Archivo Black', 'Space Grotesk', sans-serif";

  // Apollo sun mark — matches the in-app sidebar logo.
  const logo = (size: number) => (
    <div style={css(`width:${size}px; height:${size}px; border-radius:7px; background:${c.accent}; display:flex; align-items:center; justify-content:center; flex-shrink:0;`)}>
      <svg viewBox="0 0 24 24" fill="none" width={Math.round(size * 0.58)} height={Math.round(size * 0.58)} style={{ color: "white" }}>
        <circle cx="12" cy="12" r="4.5" stroke="currentColor" strokeWidth="2" />
        <g stroke="currentColor" strokeWidth="2" strokeLinecap="round">
          <line x1="12" y1="1.5" x2="12" y2="4" /><line x1="12" y1="20" x2="12" y2="22.5" />
          <line x1="1.5" y1="12" x2="4" y2="12" /><line x1="20" y1="12" x2="22.5" y2="12" />
          <line x1="4.2" y1="4.2" x2="6" y2="6" /><line x1="18" y1="18" x2="19.8" y2="19.8" />
          <line x1="19.8" y1="4.2" x2="18" y2="6" /><line x1="6" y1="18" x2="4.2" y2="19.8" />
        </g>
      </svg>
    </div>
  );

  const go = (next: Stage) => {
    setStage(next);
    setError(null);
    setNotice(null);
  };

  async function submitLogin() {
    setIsLoading(true);
    setError(null);
    try {
      onLogin(await loginRequest(email, password));
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Login failed.");
    } finally {
      setIsLoading(false);
    }
  }

  async function submitApplication() {
    setIsLoading(true);
    setError(null);
    try {
      onLogin(await signupRequest({
        email,
        password,
        display_name: displayName || email.split("@")[0],
        organization_name: organizationName || "Private desk",
      }));
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Application failed.");
    } finally {
      setIsLoading(false);
    }
  }

  async function sendResetRequest() {
    setIsLoading(true);
    setError(null);
    setNotice(null);
    try {
      await requestPasswordReset(email);
      setNotice("If that account exists, instructions are on the way. Local dev: artifacts/email_outbox.");
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Could not request a reset.");
    } finally {
      setIsLoading(false);
    }
  }

  async function applyReset() {
    setIsLoading(true);
    setError(null);
    setNotice(null);
    try {
      await confirmPasswordReset(resetToken.trim(), newPassword);
      setResetToken("");
      setNewPassword("");
      setPassword("");
      go("login");
      setNotice("Password updated. Log in.");
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Reset failed — check the token.");
    } finally {
      setIsLoading(false);
    }
  }

  // One-click demo: sign straight into the full-access demo workspace so every
  // feature (backtests, research, the news matrix) is populated and usable.
  // Pass credentials directly — don't rely on async state updates.
  async function loginAsDemo() {
    setIsLoading(true);
    setError(null);
    try {
      onLogin(await loginRequest("demo@quantops.local", "quantops-demo"));
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Demo sign-in failed.");
    } finally {
      setIsLoading(false);
    }
  }

  const input = css(`width:100%; box-sizing:border-box; font-family:${inter}; font-size:13.5px; color:${c.text}; background:${c.surface}; border:1px solid ${c.border}; border-radius:6px; padding:11px 13px; margin-top:6px; outline:none;`);
  const label = css(`font-size:12px; font-weight:600; color:${c.text}; display:block; margin-top:15px;`);
  const solidBtn = css(`width:100%; margin-top:20px; background:${c.text}; color:${c.surface}; border:none; border-radius:6px; padding:0 16px; height:44px; font-size:13.5px; font-weight:600; font-family:${inter}; cursor:${isLoading ? "default" : "pointer"}; opacity:${isLoading ? 0.6 : 1};`);
  const ghostBtn = css(`background:${c.bg}; border:1px solid ${c.border}; color:${c.faint}; border-radius:6px; padding:9px 12px; font-size:12px; font-weight:500; font-family:${inter}; cursor:pointer; flex:1;`);
  const textLink = css(`background:transparent; border:none; color:${c.faint}; font-size:12.5px; font-weight:500; font-family:${inter}; cursor:pointer; padding:0; text-decoration:none;`);

  const title = stage === "login" ? "Sign in" : stage === "apply" ? "Apply for access" : "Reset password";
  const headerCta =
    stage === "login"
      ? { label: "Apply for access", run: () => go("apply") }
      : { label: "Sign in", run: () => go("login") };

  return (
    <div style={css(`min-height:100vh; width:100%; background:${c.bg}; color:${c.text}; font-family:${inter}; display:flex; flex-direction:column; box-sizing:border-box;`)}>
      {/* HEADER */}
      <header style={css(`display:flex; align-items:center; justify-content:space-between; gap:12px; padding:16px 26px; border-bottom:1px solid ${c.line}; box-sizing:border-box;`)}>
        <div style={{ display: "flex", alignItems: "center", gap: "10px" }}>
          {logo(28)}
          <div style={{ fontFamily: black, fontSize: "17px", letterSpacing: ".01em", color: c.text }}>APOLLO</div>
        </div>
        <div style={{ display: "flex", alignItems: "center", gap: "16px" }}>
          <span style={{ fontSize: "13px", color: c.faint }}>{stage === "login" ? "New to Apollo?" : "Already a member?"}</span>
          <button onClick={headerCta.run} style={css(`background:${c.surface}; border:1px solid ${c.border}; color:${c.text}; border-radius:6px; padding:8px 15px; font-size:13px; font-weight:600; font-family:${inter}; cursor:pointer;`)}>{headerCta.label}</button>
        </div>
      </header>

      {/* CARD */}
      <main style={css("flex:1; display:flex; align-items:center; justify-content:center; padding:32px; box-sizing:border-box;")}>
        <div style={css(`width:100%; max-width:392px; background:${c.surface}; border:1px solid ${c.border}; border-radius:10px; padding:32px; box-shadow:0 1px 2px oklch(20% 0.01 255 / 0.04), 0 12px 32px oklch(20% 0.01 255 / 0.05);`)}>
          <div style={{ marginBottom: "18px" }}>{logo(38)}</div>
          <h1 style={{ fontSize: "20px", fontWeight: 700, letterSpacing: "-0.01em", color: c.text, margin: 0 }}>{title}</h1>
          <div style={{ fontSize: "13px", color: c.faint, marginTop: "5px" }}>
            {stage === "login" ? "Access your workspace." : stage === "apply" ? "Applications are approved on submission." : "We'll email a reset token."}
          </div>

          {stage === "login" ? (
            <form onSubmit={(e) => { e.preventDefault(); if (!isLoading) void submitLogin(); }}>
              <label style={label}>Email<input style={input} type="email" value={email} onChange={(e) => setEmail(e.target.value)} autoComplete="email" /></label>
              <div style={label}>
                <span style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline" }}>
                  <label htmlFor="apollo-login-password">Password</label>
                  <button type="button" onClick={() => go("reset")} style={textLink}>Forgot?</button>
                </span>
                <input id="apollo-login-password" style={input} type="password" value={password} onChange={(e) => setPassword(e.target.value)} autoComplete="current-password" />
              </div>
              <button type="submit" style={solidBtn} disabled={isLoading}>{isLoading ? "…" : "Sign in"}</button>
              {demoLoginEnabled ? (
                <button type="button" style={{ ...ghostBtn, width: "100%", marginTop: "12px" }} onClick={() => { if (!isLoading) void loginAsDemo(); }}>Enter the demo →</button>
              ) : null}
            </form>
          ) : null}

          {stage === "apply" ? (
            <form onSubmit={(e) => { e.preventDefault(); if (!isLoading) void submitApplication(); }}>
              <label style={label}>Full name<input style={input} value={displayName} onChange={(e) => setDisplayName(e.target.value)} autoComplete="name" /></label>
              <label style={label}>Desk name<input style={input} value={organizationName} onChange={(e) => setOrganizationName(e.target.value)} placeholder="Private desk" /></label>
              <label style={label}>Email<input style={input} type="email" value={email} onChange={(e) => setEmail(e.target.value)} autoComplete="email" /></label>
              <label style={label}>Password<input style={input} type="password" value={password} onChange={(e) => setPassword(e.target.value)} autoComplete="new-password" /></label>
              <button type="submit" style={solidBtn} disabled={isLoading}>{isLoading ? "…" : "Submit application"}</button>
            </form>
          ) : null}

          {stage === "reset" ? (
            <div>
              <label style={label}>Email<input style={input} type="email" value={email} onChange={(e) => setEmail(e.target.value)} /></label>
              <button type="button" onClick={() => { if (!isLoading) void sendResetRequest(); }} style={solidBtn} disabled={isLoading}>{isLoading ? "…" : "Send instructions"}</button>
              <div style={css(`border-top:1px solid ${c.line}; margin-top:22px; padding-top:6px;`)}>
                <label style={label}>Reset token<input style={input} value={resetToken} onChange={(e) => setResetToken(e.target.value)} /></label>
                <label style={label}>New password<input style={input} type="password" value={newPassword} onChange={(e) => setNewPassword(e.target.value)} /></label>
                <button type="button" onClick={() => { if (!isLoading && resetToken.trim() && newPassword) void applyReset(); }} style={{ ...solidBtn, opacity: isLoading || !resetToken.trim() || !newPassword ? 0.5 : 1 }} disabled={isLoading || !resetToken.trim() || !newPassword}>Set new password</button>
              </div>
              <button type="button" onClick={() => go("login")} style={{ ...textLink, marginTop: "16px", display: "block" }}>← Back to sign in</button>
            </div>
          ) : null}

          {error ? <div style={css(`margin-top:14px; font-size:12.5px; color:${c.loss}; background:oklch(from ${c.loss} l c h / 0.08); border-radius:6px; padding:10px 12px;`)}>{error}</div> : null}
          {notice ? <div style={css(`margin-top:14px; font-size:12.5px; color:${c.ok}; background:oklch(from ${c.ok} l c h / 0.08); border-radius:6px; padding:10px 12px;`)}>{notice}</div> : null}
        </div>
      </main>

      {/* FOOTER */}
      <footer style={css(`padding:16px 26px; border-top:1px solid ${c.line}; display:flex; align-items:center; justify-content:space-between; gap:10px; flex-wrap:wrap; box-sizing:border-box;`)}>
        <div style={{ fontSize: "12px", color: c.faint }}>© Apollo</div>
        <div style={{ fontSize: "12px", color: c.faint }}>Simulated capital only · not financial advice</div>
      </footer>
    </div>
  );
}

export default ApolloLogin;
