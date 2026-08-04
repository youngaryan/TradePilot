import { forwardRef, type ButtonHTMLAttributes, type ReactNode } from "react";

export type Tone = "neutral" | "good" | "warn" | "bad" | "info" | "brand" | "elevated";

function cx(...parts: Array<string | false | null | undefined>) {
  return parts.filter(Boolean).join(" ");
}

/* ------------------------------------------------------------------ Button */

export type ButtonVariant = "primary" | "secondary" | "ghost" | "danger";

export interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: ButtonVariant;
  size?: "sm" | "md" | "lg";
  block?: boolean;
  /** Leading icon element. */
  icon?: ReactNode;
  /** Trailing icon element. */
  iconEnd?: ReactNode;
}

export const Button = forwardRef<HTMLButtonElement, ButtonProps>(function Button(
  { variant = "secondary", size = "md", block, icon, iconEnd, className, children, type = "button", ...rest },
  ref,
) {
  return (
    <button
      ref={ref}
      type={type}
      className={cx(
        "ui-btn",
        `ui-btn--${variant}`,
        size !== "md" && `ui-btn--${size}`,
        block && "ui-btn--block",
        className,
      )}
      {...rest}
    >
      {icon}
      {children}
      {iconEnd}
    </button>
  );
});

export interface IconButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  /** Required: icon-only controls must expose an accessible name. */
  label: string;
  bordered?: boolean;
  danger?: boolean;
}

export const IconButton = forwardRef<HTMLButtonElement, IconButtonProps>(function IconButton(
  { label, bordered, danger, className, children, type = "button", ...rest },
  ref,
) {
  return (
    <button
      ref={ref}
      type={type}
      aria-label={label}
      title={label}
      className={cx("ui-icon-btn", bordered && "ui-icon-btn--bordered", danger && "ui-icon-btn--danger", className)}
      {...rest}
    >
      {children}
    </button>
  );
});

/* ------------------------------------------------------------------- Badge */

export function Tag({ tone = "neutral", children, className, title }: {
  tone?: Tone;
  children: ReactNode;
  className?: string;
  title?: string;
}) {
  return (
    <span className={cx("ui-badge", tone !== "neutral" && `ui-badge--${tone}`, className)} title={title}>
      {children}
    </span>
  );
}

/**
 * Status with a redundant text label so state is never carried by colour alone
 * (WCAG 1.4.1). The dot is decorative.
 */
export function StatusIndicator({ tone = "neutral", children, busy }: {
  tone?: Tone;
  children: ReactNode;
  busy?: boolean;
}) {
  return (
    <span className={cx("ui-status", busy ? "ui-status--busy" : tone !== "neutral" && `ui-status--${tone}`)}>
      <span className="ui-status__dot" aria-hidden="true" />
      <span>{children}</span>
    </span>
  );
}

/* -------------------------------------------------------------------- Chip */

export function Chip({ active, children, className, ...rest }: ButtonHTMLAttributes<HTMLButtonElement> & { active?: boolean }) {
  return (
    <button type="button" aria-pressed={active} className={cx("ui-chip", className)} {...rest}>
      {children}
    </button>
  );
}

/* -------------------------------------------------- Segmented / tab groups */

export interface SegmentOption<T extends string> {
  value: T;
  label: string;
  icon?: ReactNode;
  title?: string;
  disabled?: boolean;
}

export function SegmentedControl<T extends string>({ options, value, onChange, label, className }: {
  options: Array<SegmentOption<T>>;
  value: T;
  onChange: (value: T) => void;
  /** Accessible group name. */
  label: string;
  className?: string;
}) {
  return (
    <div className={cx("ui-segmented", className)} role="group" aria-label={label}>
      {options.map((option) => (
        <button
          key={option.value}
          type="button"
          className="ui-segmented__item"
          aria-pressed={option.value === value}
          disabled={option.disabled}
          title={option.title}
          onClick={() => onChange(option.value)}
        >
          {option.icon}
          {option.label}
        </button>
      ))}
    </div>
  );
}

export interface TabOption<T extends string> {
  value: T;
  label: string;
  icon?: ReactNode;
  count?: number | null;
}

export function Tabs<T extends string>({ options, value, onChange, label, className }: {
  options: Array<TabOption<T>>;
  value: T;
  onChange: (value: T) => void;
  label: string;
  className?: string;
}) {
  return (
    <div className={cx("ui-tabs", className)} role="tablist" aria-label={label}>
      {options.map((option) => (
        <button
          key={option.value}
          type="button"
          role="tab"
          aria-selected={option.value === value}
          className="ui-tab"
          onClick={() => onChange(option.value)}
        >
          {option.icon}
          {option.label}
          {option.count != null ? <span className="ui-tab__count">{option.count}</span> : null}
        </button>
      ))}
    </div>
  );
}

export { cx };
