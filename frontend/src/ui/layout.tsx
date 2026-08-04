import { useId, useState, type ReactNode } from "react";

import { cx, type Tone } from "./primitives";

/* -------------------------------------------------------------- PageHeader */

export function PageHeader({ eyebrow, title, description, actions, meta, className }: {
  eyebrow?: ReactNode;
  title: string;
  description?: ReactNode;
  actions?: ReactNode;
  /** Contextual facts: data freshness, run counts, plan/role state. */
  meta?: ReactNode;
  className?: string;
}) {
  return (
    <header className={cx("ui-page-header", className)}>
      <div className="ui-page-header__text">
        {eyebrow ? <span className="ui-page-header__eyebrow">{eyebrow}</span> : null}
        <h1>{title}</h1>
        {description ? <p>{description}</p> : null}
        {meta ? <div className="ui-page-header__meta">{meta}</div> : null}
      </div>
      {actions ? <div className="ui-page-header__actions">{actions}</div> : null}
    </header>
  );
}

export function SectionTitle({ title, children, as: As = "h2", id }: {
  title: ReactNode;
  children?: ReactNode;
  as?: "h2" | "h3" | "h4";
  id?: string;
}) {
  return (
    <div className="ui-section-title">
      <As id={id}>{title}</As>
      {children}
    </div>
  );
}

/* -------------------------------------------------------------------- Card */

export function Card({ title, subtitle, actions, footer, children, className, inset, flush, as: As = "section", labelledBy }: {
  title?: ReactNode;
  subtitle?: ReactNode;
  actions?: ReactNode;
  footer?: ReactNode;
  children?: ReactNode;
  className?: string;
  inset?: boolean;
  flush?: boolean;
  as?: "section" | "article" | "div";
  labelledBy?: string;
}) {
  const generated = useId();
  const titleId = title ? `card-${generated}` : undefined;
  return (
    <As
      className={cx("ui-card", inset && "ui-card--inset", flush && "ui-card--flush", className)}
      aria-labelledby={labelledBy ?? titleId}
    >
      {title || actions ? (
        <div className="ui-card__header">
          <div>
            {title ? <h3 className="ui-card__title" id={titleId}>{title}</h3> : null}
            {subtitle ? <p className="ui-card__subtitle">{subtitle}</p> : null}
          </div>
          {actions ? <div className="ui-btn-row">{actions}</div> : null}
        </div>
      ) : null}
      {children}
      {footer ? <div className="ui-card__footer">{footer}</div> : null}
    </As>
  );
}

/* ------------------------------------------------------------------ Metric */

export function MetricGrid({ children, className }: { children: ReactNode; className?: string }) {
  return <div className={cx("ui-metric-grid", className)}>{children}</div>;
}

export function Metric({ label, value, unavailable, delta, footnote, explain, tone }: {
  label: ReactNode;
  /** Formatted value. Pass `unavailable` instead of inventing a placeholder. */
  value?: ReactNode;
  /** Honest "not measured / not available" copy shown instead of a value. */
  unavailable?: string;
  delta?: ReactNode;
  footnote?: ReactNode;
  explain?: { term: string; body: string };
  tone?: Extract<Tone, "good" | "bad" | "neutral">;
}) {
  return (
    <article className="ui-metric">
      <span className="ui-metric__label">
        {label}
        {explain ? <Explain term={explain.term} body={explain.body} /> : null}
      </span>
      {unavailable != null ? (
        <span className="ui-metric__value ui-metric__value--unavailable">{unavailable}</span>
      ) : (
        <span className={cx("ui-metric__value", tone === "good" && "ui-pos", tone === "bad" && "ui-neg")}>{value}</span>
      )}
      {delta || footnote ? (
        <span className="ui-metric__foot">
          {delta}
          {footnote}
        </span>
      ) : null}
    </article>
  );
}

/* ----------------------------------------------------------------- Explain */

/**
 * Inline metric explainer. Uses a real button + `aria-describedby` so the
 * definition is reachable by keyboard and announced, not hover-only.
 */
export function Explain({ term, body, align = "center" }: {
  term: string;
  body: string;
  align?: "center" | "end";
}) {
  const [open, setOpen] = useState(false);
  const id = useId();
  return (
    <span className={cx("ui-explain", align === "end" && "ui-explain--end")}>
      <button
        type="button"
        className="ui-explain__trigger"
        aria-label={`What does ${term} mean?`}
        aria-expanded={open}
        aria-describedby={open ? id : undefined}
        onClick={() => setOpen((value) => !value)}
        onBlur={() => setOpen(false)}
        onMouseEnter={() => setOpen(true)}
        onMouseLeave={() => setOpen(false)}
      >
        ?
      </button>
      {open ? (
        <span className="ui-explain__bubble" id={id} role="tooltip">
          <strong>{term}</strong>
          {body}
        </span>
      ) : null}
    </span>
  );
}

/* -------------------------------------------------------------- Disclosure */

/**
 * Progressive disclosure for advanced parameters, raw JSON, provenance and
 * diagnostics. Native `<details>` keeps it keyboard-accessible for free.
 */
export function Disclosure({ summary, children, defaultOpen, className }: {
  summary: ReactNode;
  children: ReactNode;
  defaultOpen?: boolean;
  className?: string;
}) {
  return (
    <details className={cx("ui-disclosure", className)} open={defaultOpen}>
      <summary>{summary}</summary>
      <div className="ui-disclosure__body">{children}</div>
    </details>
  );
}

export function Stack({ children, tight, className, as: As = "div" }: {
  children: ReactNode;
  tight?: boolean;
  className?: string;
  as?: "div" | "section";
}) {
  return <As className={cx("ui-stack", tight && "ui-stack--tight", className)}>{children}</As>;
}

export function Split({ children, weight = "even", className }: {
  children: ReactNode;
  weight?: "even" | "main-first" | "aside-first";
  className?: string;
}) {
  return (
    <div
      className={cx(
        "ui-split",
        weight === "main-first" && "ui-split--main-first",
        weight === "aside-first" && "ui-split--aside-first",
        className,
      )}
    >
      {children}
    </div>
  );
}
