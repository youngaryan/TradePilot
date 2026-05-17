import styles from "./Skeleton.module.css";

export function Skeleton({
  variant = "line",
  width,
  height,
  className = ""
}: {
  variant?: "line" | "card" | "circle";
  width?: string | number;
  height?: string | number;
  className?: string;
}) {
  const style: Record<string, string | number> = {};
  if (width) style.width = typeof width === "number" ? `${width}px` : width;
  if (height) style.height = typeof height === "number" ? `${height}px` : height;

  if (variant === "circle") {
    return <span data-testid="skeleton" className={`${styles.circle} ${className}`} style={style} aria-hidden="true" />;
  }
  if (variant === "card") {
    return (
      <div data-testid="skeleton-card" className={`${styles.card} ${className}`} style={style} aria-hidden="true">
        <span data-testid="skeleton-line" className={styles.cardLine} style={{ width: "60%" }} />
        <span data-testid="skeleton-line" className={styles.cardLine} style={{ width: "40%" }} />
        <span data-testid="skeleton-line" className={styles.cardLine} style={{ width: "80%" }} />
      </div>
    );
  }
  return <span data-testid="skeleton" className={`${styles.line} ${className}`} style={style} aria-hidden="true" />;
}

export function SkeletonCard({
  lines = 3,
  className = ""
}: {
  lines?: number;
  className?: string;
}) {
  return (
    <div data-testid="skeleton-card" className={`${styles.card} ${className}`} aria-hidden="true" role="presentation">
      <span data-testid="skeleton-line" className={styles.cardLine} style={{ width: "55%" }} />
      {Array.from({ length: lines }).map((_, i) => (
        <span key={i} data-testid="skeleton-line" className={styles.cardLine} style={{ width: `${70 - i * 10}%` }} />
      ))}
    </div>
  );
}
