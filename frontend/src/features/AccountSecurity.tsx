import { useState } from "react";
import { AlertTriangle, CheckCircle2, Download, KeyRound, ShieldCheck, Trash2 } from "lucide-react";

import { deleteAccount, exportAccount, requestEmailVerification, requestPasswordReset, setupMfa, verifyMfa } from "../api/client";
import type { AuthResponse } from "../api/types";
import { Badge } from "../components/Badge";
import { Explainer, Panel, SectionHeader } from "../components/Cards";

export function AccountSecurity({
  auth,
  onDeleted
}: {
  auth: AuthResponse;
  onDeleted: () => void;
}) {
  const [mfaSecret, setMfaSecret] = useState<string | null>(null);
  const [mfaUrl, setMfaUrl] = useState<string | null>(null);
  const [mfaCode, setMfaCode] = useState("");
  const [exportText, setExportText] = useState("");
  const [notice, setNotice] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [isBusy, setIsBusy] = useState(false);

  async function run(action: () => Promise<void>) {
    setIsBusy(true);
    setError(null);
    setNotice(null);
    try {
      await action();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Account action failed.");
    } finally {
      setIsBusy(false);
    }
  }

  return (
    <div className="saas-workspace">
      <SectionHeader eyebrow="Account Security" title="Security, MFA, export, and deletion">
        <Badge label={auth.user.mfa_enabled ? "MFA enabled" : "MFA optional"} tone={auth.user.mfa_enabled ? "good" : "warn"} />
      </SectionHeader>

      {notice ? (
        <section className="alert-card alert-card--good">
          <CheckCircle2 size={18} />
          <span>{notice}</span>
        </section>
      ) : null}
      {error ? (
        <section className="alert-card">
          <AlertTriangle size={18} />
          <span>{error}</span>
        </section>
      ) : null}

      <div className="grid-two">
        <Panel title="Email and password" subtitle="Production login requires a verified email address. Password reset is tokenized and sent by SMTP.">
          <div className="billing-card">
            <ShieldCheck size={24} />
            <div>
              <strong>{auth.user.email}</strong>
              <span>{auth.user.email_verified_at_utc ? `Verified ${auth.user.email_verified_at_utc}` : "Email verification required in production"}</span>
            </div>
          </div>
          <div className="button-cluster">
            <button type="button" className="secondary-button" disabled={isBusy} onClick={() => void run(async () => {
              await requestEmailVerification(auth.user.email);
              setNotice("Verification email requested. In development, check artifacts/email_outbox.");
            })}>
              Send verification email
            </button>
            <button type="button" className="secondary-button" disabled={isBusy} onClick={() => void run(async () => {
              await requestPasswordReset(auth.user.email);
              setNotice("Password reset requested. In development, check artifacts/email_outbox.");
            })}>
              Send password reset
            </button>
          </div>
        </Panel>

        <Panel title="Admin MFA" subtitle="Admin APIs require TOTP MFA in production. Use any authenticator app.">
          <div className="button-cluster">
            <button type="button" className="primary-button" disabled={isBusy} onClick={() => void run(async () => {
              const payload = await setupMfa();
              setMfaSecret(payload.secret);
              setMfaUrl(payload.otpauth_url);
              setNotice("MFA setup started. Add the secret to an authenticator app, then verify a 6-digit code.");
            })}>
              <KeyRound size={16} />
              Setup MFA
            </button>
          </div>
          {mfaSecret ? (
            <div className="artifact-note">
              <strong>TOTP secret</strong>
              <span>{mfaSecret}</span>
              <span className="path-cell">{mfaUrl}</span>
            </div>
          ) : null}
          <div className="form-row">
            <label htmlFor="as-6-digit-code">
              6-digit code
              <input id="as-6-digit-code" value={mfaCode} onChange={(event) => setMfaCode(event.target.value)} placeholder="123456" />
            </label>
            <button type="button" className="primary-button" disabled={isBusy || !mfaCode.trim()} onClick={() => void run(async () => {
              await verifyMfa(mfaCode);
              setNotice("MFA verified for this session.");
            })}>
              Verify MFA
            </button>
          </div>
        </Panel>

        <Panel title="Export account data" subtitle="Exports account and active-workspace metadata without exposing raw artifact paths in production.">
          <button type="button" className="primary-button" disabled={isBusy} onClick={() => void run(async () => {
            const payload = await exportAccount();
            setExportText(JSON.stringify(payload, null, 2));
            setNotice("Account export loaded below.");
          })}>
            <Download size={16} />
            Export account
          </button>
          {exportText ? <pre>{exportText}</pre> : <div className="empty-state chart-empty">No export loaded yet.</div>}
        </Panel>

        <Panel title="Delete account" subtitle="This is a self-service soft delete: the account is deactivated and sessions are revoked.">
          <Explainer
            icon={<AlertTriangle size={17} />}
            title="Destructive action"
            body="Deletion immediately logs you out and disables access. The backend blocks deleting the last active admin."
          />
          <button type="button" className="danger-button" disabled={isBusy} onClick={() => {
            if (!window.confirm("Deactivate this account and log out?")) return;
            void run(async () => {
              await deleteAccount();
              onDeleted();
            });
          }}>
            <Trash2 size={16} />
            Delete account
          </button>
        </Panel>
      </div>
    </div>
  );
}
