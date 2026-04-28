import type { ReactNode } from "react";

import type { Tone } from "./Badge";

export function SectionHeader({
  eyebrow,
  title,
  children
}: {
  eyebrow?: string;
  title: string;
  children?: ReactNode;
}) {
  return (
    <div className="section-header">
      <div>
        {eyebrow ? <p className="eyebrow">{eyebrow}</p> : null}
        <h2>{title}</h2>
      </div>
      {children ? <div className="section-header__aside">{children}</div> : null}
    </div>
  );
}

export function Panel({
  title,
  subtitle,
  children,
  className = ""
}: {
  title?: string;
  subtitle?: string;
  children: ReactNode;
  className?: string;
}) {
  return (
    <section className={`panel ${className}`}>
      {title ? (
        <div className="panel-heading">
          <h3>{title}</h3>
          {subtitle ? <span>{subtitle}</span> : null}
        </div>
      ) : null}
      {children}
    </section>
  );
}

export function MetricCard({
  label,
  value,
  detail,
  tone = "neutral",
  icon
}: {
  label: string;
  value: string;
  detail?: string;
  tone?: Tone;
  icon?: ReactNode;
}) {
  return (
    <article className={`metric-card metric-card--${tone}`}>
      <div className="metric-card__top">
        <span>{label}</span>
        {icon}
      </div>
      <strong>{value}</strong>
      {detail ? <small>{detail}</small> : null}
    </article>
  );
}

export function Explainer({
  title,
  body,
  items,
  icon
}: {
  title: string;
  body: string;
  items?: string[];
  icon?: ReactNode;
}) {
  return (
    <aside className="explainer">
      <strong className={icon ? "explainer__title" : undefined}>
        {icon}
        {title}
      </strong>
      <p>{body}</p>
      {items?.length ? (
        <ul>
          {items.map((item) => (
            <li key={item}>{item}</li>
          ))}
        </ul>
      ) : null}
    </aside>
  );
}

export function EmptyState({
  icon,
  title,
  body
}: {
  icon: ReactNode;
  title: string;
  body: string;
}) {
  return (
    <section className="empty-state empty-state--large">
      {icon}
      <h2>{title}</h2>
      <p>{body}</p>
    </section>
  );
}
