/**
 * Meridian brand identity.
 *
 * The mark is a meridian: a circle crossed by a great-circle arc and a level
 * line — a navigator's reference, not a chart or a rocket. Drawn as strokes on
 * a plain fill so it stays legible at 16px, in either theme, and in print.
 */
export function BrandMark({ size = 26, className }: { size?: number; className?: string }) {
  const inner = Math.round(size * 0.66);
  return (
    <span className={className ?? "brand-mark"} style={{ width: size, height: size }} aria-hidden="true">
      <svg viewBox="0 0 24 24" width={inner} height={inner} fill="none" focusable="false">
        <circle cx="12" cy="12" r="9" stroke="currentColor" strokeWidth="1.9" />
        <ellipse cx="12" cy="12" rx="4" ry="9" stroke="currentColor" strokeWidth="1.6" />
        <line x1="3" y1="12" x2="21" y2="12" stroke="currentColor" strokeWidth="1.6" />
      </svg>
    </span>
  );
}

/** Wordmark + descriptor lockup used in the shell, sign-in, and legal pages. */
export function BrandWord({ descriptor = "Strategy Research Terminal" }: { descriptor?: string | null }) {
  return (
    <span className="brand-word">
      <strong>Meridian</strong>
      {descriptor ? <span>{descriptor}</span> : null}
    </span>
  );
}
