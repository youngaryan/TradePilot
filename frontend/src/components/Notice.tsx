import type { ReactNode } from "react";
import { AlertTriangle, CheckCircle2, Info, X } from "lucide-react";

import type { Tone } from "./Badge";
import styles from "./Notice.module.css";

const toneIconMap: Record<string, ReactNode> = {
  error: <AlertTriangle size={16} />,
  bad: <AlertTriangle size={16} />,
  warn: <AlertTriangle size={16} />,
  success: <CheckCircle2 size={16} />,
  good: <CheckCircle2 size={16} />,
  info: <Info size={16} />
};

const toneClassMap: Record<string, string> = {
  error: "inline-error",
  bad: "inline-error",
  warn: "inline-warning",
  success: "inline-success",
  good: "inline-success",
  info: "alert-card alert-card--info"
};

export function Notice({
  tone = "info",
  children,
  onDismiss
}: {
  tone?: Tone | "error" | "success";
  children: ReactNode;
  onDismiss?: () => void;
}) {
  const toneClass = toneClassMap[tone] ?? "inline-error";
  const icon = toneIconMap[tone] ?? null;

  return (
    <div className={toneClass}>
      {icon}
      <span>{children}</span>
      {onDismiss ? (
        <button type="button" className={styles.dismiss} onClick={onDismiss} aria-label="Dismiss notice">
          <X size={14} />
        </button>
      ) : null}
    </div>
  );
}

export function NoticeBanner({
  tone = "info",
  title,
  children,
  onDismiss
}: {
  tone?: Tone | "error" | "success";
  title: string;
  children: ReactNode;
  onDismiss?: () => void;
}) {
  const toneSuffix = tone === "error" || tone === "bad" ? "" : tone === "success" || tone === "good" ? "--good" : tone === "warn" ? "" : "--info";
  return (
    <section className={`alert-card alert-card${toneSuffix}`}>
      <div className="alert-card__content">
        <strong>{title}</strong>
        <p>{children}</p>
      </div>
      {onDismiss ? (
        <button type="button" className={styles.dismiss} onClick={onDismiss} aria-label="Dismiss notice">
          <X size={16} />
        </button>
      ) : null}
    </section>
  );
}
