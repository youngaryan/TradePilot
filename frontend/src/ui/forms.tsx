import { useId, type InputHTMLAttributes, type ReactNode, type SelectHTMLAttributes, type TextareaHTMLAttributes } from "react";
import { AlertCircle } from "lucide-react";

import { cx } from "./primitives";

export interface FieldProps {
  label: ReactNode;
  /** Rendered when there is no error. */
  hint?: ReactNode;
  error?: string | null;
  optional?: boolean;
  className?: string;
  /** Receives the generated ids to wire label / hint / error to the control. */
  children: (ids: { inputId: string; describedBy: string | undefined; invalid: boolean }) => ReactNode;
  htmlFor?: string;
}

/**
 * Labelled form field. Owns id generation and the `aria-describedby` wiring so
 * hints and validation errors are announced by screen readers.
 */
export function Field({ label, hint, error, optional, className, children, htmlFor }: FieldProps) {
  const generated = useId();
  const inputId = htmlFor ?? `field-${generated}`;
  const hintId = `${inputId}-hint`;
  const errorId = `${inputId}-error`;
  const describedBy = [error ? errorId : null, hint ? hintId : null].filter(Boolean).join(" ") || undefined;

  return (
    <div className={cx("ui-field", className)}>
      <label className="ui-field__label" htmlFor={inputId}>
        {label}
        {optional ? <span className="ui-field__optional">optional</span> : null}
      </label>
      {children({ inputId, describedBy, invalid: Boolean(error) })}
      {error ? (
        <span className="ui-field__error" id={errorId} role="alert">
          <AlertCircle size={13} aria-hidden="true" />
          {error}
        </span>
      ) : null}
      {hint ? (
        <span className="ui-field__hint" id={hintId}>
          {hint}
        </span>
      ) : null}
    </div>
  );
}

type BaseInput = Omit<InputHTMLAttributes<HTMLInputElement>, "id" | "value" | "onChange">;

export function TextInput({
  label, value, onChange, hint, error, optional, mono, className, id, ...rest
}: {
  label: ReactNode;
  value: string;
  onChange: (value: string) => void;
  hint?: ReactNode;
  error?: string | null;
  optional?: boolean;
  mono?: boolean;
  className?: string;
  id?: string;
} & BaseInput) {
  return (
    <Field label={label} hint={hint} error={error} optional={optional} className={className} htmlFor={id}>
      {({ inputId, describedBy, invalid }) => (
        <input
          id={inputId}
          className={cx("ui-input", mono && "ui-input--mono")}
          value={value}
          aria-describedby={describedBy}
          aria-invalid={invalid || undefined}
          onChange={(event) => onChange(event.target.value)}
          {...rest}
        />
      )}
    </Field>
  );
}

export function TextArea({
  label, value, onChange, hint, error, optional, mono, className, id, ...rest
}: {
  label: ReactNode;
  value: string;
  onChange: (value: string) => void;
  hint?: ReactNode;
  error?: string | null;
  optional?: boolean;
  mono?: boolean;
  className?: string;
  id?: string;
} & Omit<TextareaHTMLAttributes<HTMLTextAreaElement>, "id" | "value" | "onChange">) {
  return (
    <Field label={label} hint={hint} error={error} optional={optional} className={className} htmlFor={id}>
      {({ inputId, describedBy, invalid }) => (
        <textarea
          id={inputId}
          className={cx("ui-textarea", mono && "ui-textarea--mono")}
          value={value}
          aria-describedby={describedBy}
          aria-invalid={invalid || undefined}
          onChange={(event) => onChange(event.target.value)}
          {...rest}
        />
      )}
    </Field>
  );
}

export function SelectInput({
  label, value, onChange, hint, error, optional, className, children, id, ...rest
}: {
  label: ReactNode;
  value: string;
  onChange: (value: string) => void;
  hint?: ReactNode;
  error?: string | null;
  optional?: boolean;
  className?: string;
  children: ReactNode;
  id?: string;
} & Omit<SelectHTMLAttributes<HTMLSelectElement>, "id" | "value" | "onChange" | "children">) {
  return (
    <Field label={label} hint={hint} error={error} optional={optional} className={className} htmlFor={id}>
      {({ inputId, describedBy, invalid }) => (
        <select
          id={inputId}
          className="ui-select"
          value={value}
          aria-describedby={describedBy}
          aria-invalid={invalid || undefined}
          onChange={(event) => onChange(event.target.value)}
          {...rest}
        >
          {children}
        </select>
      )}
    </Field>
  );
}

export function CheckboxInput({ label, checked, onChange, hint, disabled, className }: {
  label: ReactNode;
  checked: boolean;
  onChange: (checked: boolean) => void;
  hint?: ReactNode;
  disabled?: boolean;
  className?: string;
}) {
  return (
    <label className={cx("ui-check", className)}>
      <input
        type="checkbox"
        checked={checked}
        disabled={disabled}
        onChange={(event) => onChange(event.target.checked)}
      />
      <span className="ui-check__body">
        <span>{label}</span>
        {hint ? <span className="ui-check__hint">{hint}</span> : null}
      </span>
    </label>
  );
}

export function SwitchInput({ label, checked, onChange, disabled, className }: {
  label: ReactNode;
  checked: boolean;
  onChange: (checked: boolean) => void;
  disabled?: boolean;
  className?: string;
}) {
  return (
    <label className={cx("ui-switch", className)}>
      <input
        type="checkbox"
        role="switch"
        checked={checked}
        disabled={disabled}
        onChange={(event) => onChange(event.target.checked)}
      />
      <span className="ui-switch__track" aria-hidden="true" />
      <span>{label}</span>
    </label>
  );
}

export function FilterBar({ children, label = "Filters", className }: {
  children: ReactNode;
  label?: string;
  className?: string;
}) {
  return (
    <div className={cx("ui-filter-bar", className)} role="group" aria-label={label}>
      {children}
    </div>
  );
}
