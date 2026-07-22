import React from "react";

export interface SkeletonProps {
  /** Height of the skeleton line/block in px. Default: 16 */
  height?: number;
  /** Width as CSS string. Default: "100%" */
  width?: string;
  /** Border-radius as CSS string. Default: "var(--radius-xs)" */
  borderRadius?: string;
  className?: string;
}

/**
 * Skeleton — animated shimmer placeholder for loading states.
 * Resurrects the existing `.skeleton` + `@keyframes shimmer` from App.css.
 */
export const Skeleton: React.FC<SkeletonProps> = ({
  height = 16,
  width = "100%",
  borderRadius,
  className = "",
}) => {
  return (
    <div
      className={`skeleton${className ? ` ${className}` : ""}`}
      style={{
        height,
        width,
        borderRadius: borderRadius ?? "var(--radius-xs)",
        flexShrink: 0,
      }}
      aria-hidden="true"
    />
  );
};

// ── Preset compositions ────────────────────────────────────────────────────────

/** A card skeleton with a title line and two body lines */
export const SkeletonCard: React.FC<{ className?: string }> = ({
  className = "",
}) => (
  <div
    className={`skeleton-card${className ? ` ${className}` : ""}`}
    aria-hidden="true"
  >
    <Skeleton height={14} width="55%" />
    <div style={{ marginTop: 12 }}>
      <Skeleton height={12} />
    </div>
    <div style={{ marginTop: 6 }}>
      <Skeleton height={12} width="75%" />
    </div>
  </div>
);

/** Row of N skeleton cards */
export const SkeletonGrid: React.FC<{ count?: number; className?: string }> = ({
  count = 3,
  className = "",
}) => (
  <div className={`skeleton-grid${className ? ` ${className}` : ""}`}>
    {Array.from({ length: count }).map((_, i) => (
      <SkeletonCard key={i} />
    ))}
  </div>
);

export default Skeleton;
