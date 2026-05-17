import type { ChangeEvent, InputHTMLAttributes, ReactNode, SelectHTMLAttributes } from "react";

type FormFieldProps = {
  id: string;
  label: string;
  error?: string | null;
  hint?: string;
  children: ReactNode;
};

function FormField({ id, label, error, hint, children }: FormFieldProps) {
  return (
    <label htmlFor={id}>
      {label}
      {children}
      {error ? <span className="form-error">{error}</span> : null}
      {hint && !error ? <small>{hint}</small> : null}
    </label>
  );
}

export function TextField({
  id,
  label,
  value,
  onChange,
  error,
  hint,
  placeholder,
  type = "text",
  ...rest
}: {
  id: string;
  label: string;
  value: string;
  onChange: (value: string) => void;
  error?: string | null;
  hint?: string;
  placeholder?: string;
  type?: string;
} & Omit<InputHTMLAttributes<HTMLInputElement>, "id" | "value" | "onChange">) {
  return (
    <FormField id={id} label={label} error={error} hint={hint}>
      <input
        id={id}
        type={type}
        value={value}
        onChange={(event: ChangeEvent<HTMLInputElement>) => onChange(event.target.value)}
        placeholder={placeholder}
        {...rest}
      />
    </FormField>
  );
}

export function Select({
  id,
  label,
  value,
  onChange,
  error,
  hint,
  children,
  ...rest
}: {
  id: string;
  label: string;
  value: string;
  onChange: (value: string) => void;
  error?: string | null;
  hint?: string;
  children: ReactNode;
} & Omit<SelectHTMLAttributes<HTMLSelectElement>, "id" | "value" | "onChange" | "children">) {
  return (
    <FormField id={id} label={label} error={error} hint={hint}>
      <select id={id} value={value} onChange={(event: ChangeEvent<HTMLSelectElement>) => onChange(event.target.value)} {...rest}>
        {children}
      </select>
    </FormField>
  );
}

export function Checkbox({
  id,
  label,
  checked,
  onChange,
  error,
  hint
}: {
  id: string;
  label: string;
  checked: boolean;
  onChange: (checked: boolean) => void;
  error?: string | null;
  hint?: string;
}) {
  return (
    <label htmlFor={id} className="checkbox-line">
      <input id={id} type="checkbox" checked={checked} onChange={(event: ChangeEvent<HTMLInputElement>) => onChange(event.target.checked)} />
      {label}
      {error ? <span className="form-error">{error}</span> : null}
      {hint && !error ? <small>{hint}</small> : null}
    </label>
  );
}
