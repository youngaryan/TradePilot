interface StatusBadgeProps {
  label: string;
  tone?: "neutral" | "positive" | "negative" | "warning";
}

export function StatusBadge({ label, tone = "neutral" }: StatusBadgeProps) {
  return <span className={`status-badge status-badge--${tone}`}>{label}</span>;
}
