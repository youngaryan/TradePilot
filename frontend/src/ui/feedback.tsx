import { Component, type ErrorInfo, type ReactNode } from "react";
import {
  AlertTriangle,
  CheckCircle2,
  CircleSlash,
  Info,
  Loader2,
  Lock,
  ShieldAlert,
} from "lucide-react";

import { cx, type Tone } from "./primitives";
import { Button } from "./primitives";
import { DENIAL_DETAIL, DENIAL_HEADLINE, type DenialReason, type Gate } from "../access/model";

/* ------------------------------------------------------------------ Notices */

const NOTICE_ICON: Record<string, ReactNode> = {
  info: <Info size={16} aria-hidden="true" />,
  good: <CheckCircle2 size={16} aria-hidden="true" />,
  warn: <AlertTriangle size={16} aria-hidden="true" />,
  bad: <AlertTriangle size={16} aria-hidden="true" />,
  elevated: <ShieldAlert size={16} aria-hidden="true" />,
  neutral: <Info size={16} aria-hidden="true" />,
};

export function InlineNotice({ tone = "info", title, children, actions, compact, className, role }: {
  tone?: Tone;
  title?: ReactNode;
  children?: ReactNode;
  actions?: ReactNode;
  compact?: boolean;
  className?: string;
  role?: "alert" | "status";
}) {
  return (
    <div
      className={cx("ui-notice", tone !== "neutral" && `ui-notice--${tone}`, compact && "ui-notice--compact", className)}
      role={role ?? (tone === "bad" ? "alert" : undefined)}
    >
      {NOTICE_ICON[tone] ?? NOTICE_ICON.info}
      <div className="ui-notice__body">
        {title ? <span className="ui-notice__title">{title}</span> : null}
        {children ? <span>{children}</span> : null}
        {actions ? <div className="ui-notice__actions">{actions}</div> : null}
      </div>
    </div>
  );
}

/* -------------------------------------------------------------- EmptyState */

export function EmptyPanel({ icon, title, body, actions, center, className }: {
  icon?: ReactNode;
  title: string;
  body?: ReactNode;
  actions?: ReactNode;
  center?: boolean;
  className?: string;
}) {
  return (
    <div className={cx("ui-empty", center && "ui-empty--center", className)}>
      {icon ? <span className="ui-empty__icon">{icon}</span> : null}
      <h3>{title}</h3>
      {body ? <p>{body}</p> : null}
      {actions ? <div className="ui-btn-row">{actions}</div> : null}
    </div>
  );
}

/* ---------------------------------------------------------------- Skeleton */

export function SkeletonBlock({ height = 14, width, radius, className }: {
  height?: number | string;
  width?: number | string;
  radius?: number | string;
  className?: string;
}) {
  return (
    <span
      aria-hidden="true"
      className={cx("ui-skeleton", className)}
      style={{
        height: typeof height === "number" ? `${height}px` : height,
        width: width == null ? "100%" : typeof width === "number" ? `${width}px` : width,
        borderRadius: radius == null ? undefined : typeof radius === "number" ? `${radius}px` : radius,
      }}
    />
  );
}

/** Announces loading to assistive tech while showing skeleton geometry. */
export function LoadingBlock({ label, lines = 3, className }: { label: string; lines?: number; className?: string }) {
  return (
    <div className={cx("ui-skeleton-stack", className)} role="status" aria-live="polite">
      <span className="ui-sr-only">{label}</span>
      {Array.from({ length: lines }).map((_, index) => (
        <SkeletonBlock key={index} width={`${92 - index * 14}%`} />
      ))}
    </div>
  );
}

/* ---------------------------------------------------------------- Progress */

export function ProgressBar({ value, label, valueLabel, tone = "neutral", indeterminateLabel }: {
  /** 0–1. Pass null for an unknown-duration job. */
  value: number | null;
  label: string;
  valueLabel?: string;
  tone?: Extract<Tone, "neutral" | "good" | "warn" | "bad">;
  indeterminateLabel?: string;
}) {
  const pct = value == null ? null : Math.max(0, Math.min(100, Math.round(value * 100)));
  return (
    <div className={cx("ui-progress", tone !== "neutral" && `ui-progress--${tone}`)}>
      <div className="ui-progress__head">
        <span>{label}</span>
        <span className="ui-num">{valueLabel ?? (pct == null ? indeterminateLabel ?? "In progress" : `${pct}%`)}</span>
      </div>
      <div
        className="ui-progress__track"
        role="progressbar"
        aria-label={label}
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={pct ?? undefined}
        aria-valuetext={pct == null ? indeterminateLabel ?? "In progress" : `${pct}%`}
      >
        <div className="ui-progress__fill" style={{ width: `${pct ?? 12}%` }} />
      </div>
    </div>
  );
}

export type JobPhase = "idle" | "queued" | "running" | "completed" | "completed_with_warnings" | "failed" | "timeout";

const JOB_TONE: Record<JobPhase, Extract<Tone, "neutral" | "good" | "warn" | "bad" | "info">> = {
  idle: "neutral",
  queued: "info",
  running: "info",
  completed: "good",
  completed_with_warnings: "warn",
  failed: "bad",
  timeout: "warn",
};

const JOB_LABEL: Record<JobPhase, string> = {
  idle: "Not started",
  queued: "Queued",
  running: "Running",
  completed: "Completed",
  completed_with_warnings: "Completed with warnings",
  failed: "Failed",
  timeout: "Status checks timed out",
};

export function jobPhaseFor(status: string | null | undefined, warningCount = 0): JobPhase {
  const value = String(status ?? "").toLowerCase();
  if (value === "queued") return "queued";
  if (value === "running") return "running";
  if (value === "completed") return warningCount > 0 ? "completed_with_warnings" : "completed";
  if (value === "failed") return "failed";
  if (value === "interrupted") return "failed";
  if (value === "timeout") return "timeout";
  return "idle";
}

/**
 * Job state panel: one component for queued / running / completed / warnings /
 * failed / polling-timeout so every long-running workflow reads the same way.
 */
export function JobState({ phase, title, message, progress, stage, actions, children }: {
  phase: JobPhase;
  title: string;
  message?: ReactNode;
  progress?: number | null;
  stage?: string;
  actions?: ReactNode;
  children?: ReactNode;
}) {
  const tone = JOB_TONE[phase];
  const busy = phase === "queued" || phase === "running";
  return (
    <div
      className={cx(
        "ui-job",
        tone === "good" && "ui-job--good",
        tone === "warn" && "ui-job--warn",
        tone === "bad" && "ui-job--bad",
        phase === "idle" && "ui-job--idle",
      )}
      role={phase === "failed" ? "alert" : "status"}
      aria-live={busy ? "polite" : "off"}
    >
      <div className="ui-job__head">
        <span className="ui-job__title">
          {busy ? <Loader2 size={14} className="spin" aria-hidden="true" style={{ marginRight: 6, verticalAlign: -2 }} /> : null}
          {title}
        </span>
        <span className={cx("ui-badge", tone !== "neutral" && `ui-badge--${tone}`)}>{JOB_LABEL[phase]}</span>
      </div>
      {busy ? (
        <ProgressBar
          value={progress ?? null}
          label={stage ? `Stage: ${stage}` : "Progress"}
          indeterminateLabel="Working"
        />
      ) : null}
      {message ? <span className="ui-job__message">{message}</span> : null}
      {children}
      {actions ? <div className="ui-btn-row">{actions}</div> : null}
    </div>
  );
}

/* ------------------------------------------------------ Access restriction */

const REASON_ICON: Record<DenialReason, ReactNode> = {
  subscription: <Lock size={18} aria-hidden="true" />,
  billing_attention: <AlertTriangle size={18} aria-hidden="true" />,
  workspace_role: <ShieldAlert size={18} aria-hidden="true" />,
  platform_role: <ShieldAlert size={18} aria-hidden="true" />,
  workspace_membership: <ShieldAlert size={18} aria-hidden="true" />,
  configuration: <CircleSlash size={18} aria-hidden="true" />,
  data: <CircleSlash size={18} aria-hidden="true" />,
  backend: <AlertTriangle size={18} aria-hidden="true" />,
  job: <Loader2 size={18} aria-hidden="true" />,
};

/**
 * Explains *why* something is unavailable and what to do instead.
 *
 * Every restricted surface answers the same four questions in text (never by
 * colour or a disabled control alone): what the feature does, why it is
 * unavailable, what unlocks it, and what the user can do right now.
 */
export function AccessNotice({ reason, feature, whatItDoes, unlockedBy, alternative, actions }: {
  reason: DenialReason;
  /** Name of the feature, e.g. "Walk-forward backtesting". */
  feature: string;
  whatItDoes: string;
  /** What plan / role / condition unlocks it. */
  unlockedBy?: string;
  /** What the user can usefully do instead. */
  alternative?: string;
  actions?: ReactNode;
}) {
  return (
    <section className="ui-access" aria-labelledby={`access-${reason}-${feature.replace(/\W+/g, "-")}`}>
      <div className="ui-access__head">
        <span className="ui-access__icon">{REASON_ICON[reason]}</span>
        <div>
          <span className="ui-access__reason">{DENIAL_HEADLINE[reason]}</span>
          <h3 id={`access-${reason}-${feature.replace(/\W+/g, "-")}`}>{feature}</h3>
        </div>
      </div>
      <dl>
        <dt>What it does</dt>
        <dd>{whatItDoes}</dd>
        <dt>Why it is unavailable</dt>
        <dd>{DENIAL_DETAIL[reason]}</dd>
        {unlockedBy ? (
          <>
            <dt>What unlocks it</dt>
            <dd>{unlockedBy}</dd>
          </>
        ) : null}
        {alternative ? (
          <>
            <dt>What you can do now</dt>
            <dd>{alternative}</dd>
          </>
        ) : null}
      </dl>
      {actions ? <div className="ui-btn-row">{actions}</div> : null}
    </section>
  );
}

/** Compact variant for gating a single control inside an otherwise usable page. */
export function GateHint({ gate, children }: { gate: Gate; children?: ReactNode }) {
  if (gate.allowed || !gate.reason) return null;
  return (
    <InlineNotice tone={gate.reason === "platform_role" || gate.reason === "workspace_role" ? "elevated" : "warn"} compact>
      <strong>{DENIAL_HEADLINE[gate.reason]}.</strong> {children ?? DENIAL_DETAIL[gate.reason]}
    </InlineNotice>
  );
}

/* ----------------------------------------------------------- ErrorBoundary */

interface BoundaryState {
  error: Error | null;
}

/**
 * Route-level error boundary. Renders an honest failure state — never an empty
 * state — and offers a retry that remounts the subtree.
 */
export class ErrorBoundary extends Component<{ children: ReactNode; area?: string }, BoundaryState> {
  state: BoundaryState = { error: null };

  static getDerivedStateFromError(error: Error): BoundaryState {
    return { error };
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    // Surface the stack for local debugging without swallowing the failure.
    console.error(`[${this.props.area ?? "app"}] render failed`, error, info.componentStack);
  }

  render() {
    if (!this.state.error) return this.props.children;
    return (
      <InlineNotice
        tone="bad"
        title={`${this.props.area ?? "This view"} could not be displayed`}
        actions={
          <>
            <Button variant="secondary" size="sm" onClick={() => this.setState({ error: null })}>
              Try again
            </Button>
            <Button variant="ghost" size="sm" onClick={() => window.location.reload()}>
              Reload the application
            </Button>
          </>
        }
      >
        {this.state.error.message || "An unexpected error occurred while rendering this screen."}
      </InlineNotice>
    );
  }
}
