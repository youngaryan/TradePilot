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
    return <span className={`skeleton skeleton--circle ${className}`} style={style} aria-hidden="true" />;
  }
  if (variant === "card") {
    return (
      <div className={`skeleton skeleton--card ${className}`} style={style} aria-hidden="true">
        <span className="skeleton skeleton--line" style={{ width: "60%" }} />
        <span className="skeleton skeleton--line" style={{ width: "40%" }} />
        <span className="skeleton skeleton--line" style={{ width: "80%" }} />
      </div>
    );
  }
  return <span className={`skeleton skeleton--line ${className}`} style={style} aria-hidden="true" />;
}

export function SkeletonCard({
  lines = 3,
  className = ""
}: {
  lines?: number;
  className?: string;
}) {
  return (
    <div className={`skeleton skeleton--card ${className}`} aria-hidden="true" role="presentation">
      <span className="skeleton skeleton--line" style={{ width: "55%" }} />
      {Array.from({ length: lines }).map((_, i) => (
        <span key={i} className="skeleton skeleton--line" style={{ width: `${70 - i * 10}%` }} />
      ))}
    </div>
  );
}
