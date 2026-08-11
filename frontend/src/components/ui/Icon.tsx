import React from "react";

// ── Icon set ──────────────────────────────────────────────────
// Inline SVG asset library replacing every emoji in the UI. Emoji render in
// the OS colour font — they ignore the monochrome design tokens, change shape
// per platform, and read as decoration. These are 24×24 stroke drawings on
// `currentColor`, so they inherit the surrounding text colour and track the
// light/dark theme for free.
//
// Geometry rules (keep new icons consistent):
//   · 24×24 viewBox, 1.5 stroke, round caps/joins, no fills except where a
//     solid mark is the point (status dot, pie slice).
//   · Drawn on the pixel grid at 16px — half-pixel coordinates only where a
//     stroke centre needs them.

export type IconName =
  // navigation
  | "search"
  | "dashboard"
  | "report"
  | "database"
  | "settings"
  // data sources
  | "postgresql"
  | "mongodb"
  | "elasticsearch"
  | "mysql"
  | "trino"
  | "zoho-books"
  | "tally"
  // chart types
  | "chart-bar"
  | "chart-line"
  | "chart-pie"
  | "chart-area"
  | "chart-area-off"
  | "chart-number"
  | "chart-gauge"
  | "table"
  // actions
  | "refresh"
  | "sync"
  | "edit"
  | "close"
  | "plus"
  | "check"
  | "alert"
  | "info"
  | "play"
  | "upload"
  | "download"
  | "grip"
  | "chevron-right"
  | "chevron-down"
  | "arrow-right"
  | "arrow-up"
  | "arrow-down"
  | "sun"
  | "moon"
  | "sparkles"
  | "message"
  | "lightbulb"
  | "trending-up"
  | "trending-down"
  | "minus"
  | "link"
  | "columns"
  | "sort-asc"
  | "sort-desc"
  | "sort"
  | "spark";

interface IconProps {
  name: IconName;
  /** Pixel size of the square icon box. Defaults to 16. */
  size?: number;
  className?: string;
  /** Stroke weight in viewBox units. Defaults to 1.5. */
  strokeWidth?: number;
  /** Accessible label. Omit for decorative icons (default: aria-hidden). */
  title?: string;
}

// Path geometry per icon, drawn in a 24×24 box.
const PATHS: Record<IconName, React.ReactNode> = {
  // ── Navigation ──────────────────────────────────────────────
  search: (
    <>
      <circle cx="11" cy="11" r="6.5" />
      <path d="M15.8 15.8 20.5 20.5" />
    </>
  ),
  dashboard: (
    <>
      <rect x="3.5" y="3.5" width="7" height="8.5" rx="1" />
      <rect x="3.5" y="15" width="7" height="5.5" rx="1" />
      <rect x="13.5" y="3.5" width="7" height="5.5" rx="1" />
      <rect x="13.5" y="12" width="7" height="8.5" rx="1" />
    </>
  ),
  report: (
    <>
      <path d="M6 2.5h8l4.5 4.5v14.5H6z" />
      <path d="M14 2.5V7h4.5" />
      <path d="M9 12.5h6M9 16.5h6" />
    </>
  ),
  database: (
    <>
      <ellipse cx="12" cy="5.75" rx="7.5" ry="3.25" />
      <path d="M4.5 5.75v12.5c0 1.8 3.36 3.25 7.5 3.25s7.5-1.45 7.5-3.25V5.75" />
      <path d="M4.5 12c0 1.8 3.36 3.25 7.5 3.25s7.5-1.45 7.5-3.25" />
    </>
  ),
  settings: (
    <>
      <path d="M4 7h10M18 7h2M4 17h2M10 17h10" />
      <circle cx="16" cy="7" r="2.25" />
      <circle cx="8" cy="17" r="2.25" />
    </>
  ),

  // ── Data sources ────────────────────────────────────────────
  // Each is a distinct silhouette so sources stay distinguishable without
  // colour: stacked discs (relational), leaf (document), layered rings
  // (search index), dolphin-fin arc (MySQL), lightning (Trino).
  postgresql: (
    <>
      <ellipse cx="12" cy="6" rx="7" ry="3" />
      <path d="M5 6v5c0 1.66 3.13 3 7 3s7-1.34 7-3V6" />
      <path d="M5 13v5c0 1.66 3.13 3 7 3s7-1.34 7-3v-5" />
    </>
  ),
  mongodb: (
    <>
      <path d="M12 2.5c3.2 3.4 4.8 6.6 4.8 9.6 0 3.4-2.1 6.2-4.8 7.9-2.7-1.7-4.8-4.5-4.8-7.9 0-3 1.6-6.2 4.8-9.6Z" />
      <path d="M12 8v13.5" />
    </>
  ),
  elasticsearch: (
    <>
      <circle cx="12" cy="12" r="8.5" />
      <path d="M6.2 7.5h11.6M3.5 12h13.8M6.2 16.5h11.6" />
    </>
  ),
  mysql: (
    <>
      <path d="M3 16c3.5 0 6-1.6 8-4.5S15.5 5 19.5 5" />
      <path d="M19.5 5c0 4-1 7.2-3 9.6S11.5 19 8 19" />
      <circle cx="6" cy="8.5" r="1.25" />
    </>
  ),
  trino: (
    <>
      <path d="M13.5 2.5 5 13.5h5.5L9.5 21.5 19 10.5h-5.8z" />
    </>
  ),
  "zoho-books": (
    <>
      <path d="M7.5 17.5a4 4 0 0 1-.5-7.97A5 5 0 0 1 16.7 8.3 3.8 3.8 0 0 1 16.3 17.5H7.5Z" />
    </>
  ),
  tally: (
    <>
      <path d="M6 5v14M10 5v14M14 5v14M18 5v14M4.5 8.5 19.5 15.5" />
    </>
  ),

  // ── Chart types ─────────────────────────────────────────────
  "chart-bar": (
    <>
      <path d="M3.5 20.5h17" />
      <path d="M7 20.5v-7M12 20.5v-12M17 20.5v-4.5" />
    </>
  ),
  "chart-line": (
    <>
      <path d="M3.5 20.5h17" />
      <path d="M5 16.5 9.5 11l3.5 3 5.5-7.5" />
    </>
  ),
  "chart-pie": (
    <>
      <path d="M12 3.5a8.5 8.5 0 1 0 8.5 8.5H12z" />
      <path d="M15 3.9a8.5 8.5 0 0 1 5.1 5.1H15z" />
    </>
  ),
  "chart-area": (
    <>
      <path d="M3.5 20.5h17" />
      <path d="M4.5 17.5V12l4.5-4.5 4 4 6.5-6v12z" />
    </>
  ),
  "chart-area-off": (
    <>
      <path d="M3.5 20.5h17" />
      <path d="M4.5 17.5V12l4.5-4.5 4 4" />
      <path d="M3 3l18 18" />
    </>
  ),
  "chart-number": (
    <>
      <path d="M9 3.5 7 20.5M17 3.5l-2 17" />
      <path d="M4 8.5h16M3 15.5h16" />
    </>
  ),
  "chart-gauge": (
    <>
      <path d="M3.5 17.5a8.5 8.5 0 1 1 17 0" />
      <path d="M12 17.5 16 10" />
      <circle cx="12" cy="17.5" r="1.25" />
    </>
  ),
  table: (
    <>
      <rect x="3.5" y="4.5" width="17" height="15" rx="1.5" />
      <path d="M3.5 9.5h17M3.5 14.5h17M9.5 9.5v10M15 9.5v10" />
    </>
  ),

  // ── Actions ─────────────────────────────────────────────────
  refresh: (
    <>
      <path d="M20 12a8 8 0 1 1-2.6-5.9" />
      <path d="M20.5 4v4.5H16" />
    </>
  ),
  sync: (
    <>
      <path d="M4 10a8 8 0 0 1 13.4-3.4L20 9" />
      <path d="M20 14a8 8 0 0 1-13.4 3.4L4 15" />
      <path d="M20.5 4.5V9H16M3.5 19.5V15H8" />
    </>
  ),
  edit: (
    <>
      <path d="M4 20h4l10.5-10.5a2.1 2.1 0 0 0-3-3L5 17v3z" />
      <path d="M14.5 6 18 9.5" />
    </>
  ),
  close: <path d="M6 6l12 12M18 6 6 18" />,
  plus: <path d="M12 5v14M5 12h14" />,
  minus: <path d="M5 12h14" />,
  check: <path d="M4.5 12.5 9.5 17.5 19.5 6.5" />,
  alert: (
    <>
      <path d="M12 3.5 21.5 20H2.5z" />
      <path d="M12 9.5v5" />
      <circle cx="12" cy="17.25" r="0.9" fill="currentColor" stroke="none" />
    </>
  ),
  info: (
    <>
      <circle cx="12" cy="12" r="8.5" />
      <path d="M12 11v5.5" />
      <circle cx="12" cy="7.75" r="0.9" fill="currentColor" stroke="none" />
    </>
  ),
  play: <path d="M7 4.5 19 12 7 19.5z" />,
  upload: (
    <>
      <path d="M12 16V4.5" />
      <path d="M7.5 9 12 4.5 16.5 9" />
      <path d="M4.5 15.5v3a2 2 0 0 0 2 2h11a2 2 0 0 0 2-2v-3" />
    </>
  ),
  download: (
    <>
      <path d="M12 4.5V16" />
      <path d="M7.5 11.5 12 16l4.5-4.5" />
      <path d="M4.5 15.5v3a2 2 0 0 0 2 2h11a2 2 0 0 0 2-2v-3" />
    </>
  ),
  grip: (
    <g fill="currentColor" stroke="none">
      <circle cx="9" cy="6" r="1.4" />
      <circle cx="15" cy="6" r="1.4" />
      <circle cx="9" cy="12" r="1.4" />
      <circle cx="15" cy="12" r="1.4" />
      <circle cx="9" cy="18" r="1.4" />
      <circle cx="15" cy="18" r="1.4" />
    </g>
  ),
  "chevron-right": <path d="M9.5 5.5 16 12l-6.5 6.5" />,
  "chevron-down": <path d="M5.5 9.5 12 16l6.5-6.5" />,
  "arrow-right": (
    <>
      <path d="M4 12h15" />
      <path d="M13.5 6.5 19 12l-5.5 5.5" />
    </>
  ),
  "arrow-up": (
    <>
      <path d="M12 20V5" />
      <path d="M6.5 10.5 12 5l5.5 5.5" />
    </>
  ),
  "arrow-down": (
    <>
      <path d="M12 4v15" />
      <path d="M6.5 13.5 12 19l5.5-5.5" />
    </>
  ),
  sun: (
    <>
      <circle cx="12" cy="12" r="4.25" />
      <path d="M12 2.5v2.5M12 19v2.5M4.6 4.6l1.8 1.8M17.6 17.6l1.8 1.8M2.5 12H5M19 12h2.5M4.6 19.4l1.8-1.8M17.6 6.4l1.8-1.8" />
    </>
  ),
  moon: <path d="M20 14.5A8.5 8.5 0 0 1 9.5 4a8.5 8.5 0 1 0 10.5 10.5z" />,
  sparkles: (
    <>
      <path d="M12 3.5 13.6 9 19 10.5 13.6 12 12 17.5 10.4 12 5 10.5 10.4 9z" />
      <path d="M18.5 16.5l.7 2.3 2.3.7-2.3.7-.7 2.3-.7-2.3-2.3-.7 2.3-.7z" />
    </>
  ),
  message: (
    <>
      <path d="M4.5 5.5h15v11h-9L6 20.5v-4h-1.5z" />
      <path d="M9 10h6M9 13h4" />
    </>
  ),
  lightbulb: (
    <>
      <path d="M9 17a6 6 0 1 1 6 0v1.5H9z" />
      <path d="M9.75 21h4.5" />
    </>
  ),
  "trending-up": (
    <>
      <path d="M3.5 17 9.5 11l3.5 3.5 7-7" />
      <path d="M15.5 7.5H20V12" />
    </>
  ),
  "trending-down": (
    <>
      <path d="M3.5 7.5 9.5 13.5l3.5-3.5 7 7" />
      <path d="M15.5 17H20v-4.5" />
    </>
  ),
  link: (
    <>
      <path d="M10 14a4 4 0 0 0 5.7 0l3-3a4 4 0 1 0-5.7-5.7L11.5 6.8" />
      <path d="M14 10a4 4 0 0 0-5.7 0l-3 3A4 4 0 1 0 11 18.7l1.4-1.4" />
    </>
  ),
  columns: (
    <>
      <rect x="3.5" y="4.5" width="17" height="15" rx="1.5" />
      <path d="M9.5 4.5v15M15 4.5v15" />
    </>
  ),
  "sort-asc": (
    <>
      <path d="M7 20V5" />
      <path d="M3.5 8.5 7 5l3.5 3.5" />
      <path d="M13 8h8M13 13h6M13 18h4" />
    </>
  ),
  "sort-desc": (
    <>
      <path d="M7 5v15" />
      <path d="M3.5 16.5 7 20l3.5-3.5" />
      <path d="M13 8h4M13 13h6M13 18h8" />
    </>
  ),
  sort: (
    <>
      <path d="M8 4.5v15M4.5 8 8 4.5 11.5 8M4.5 16 8 19.5 11.5 16" />
    </>
  ),
  spark: (
    <>
      <path d="M4 16.5 8 11l3.5 3.5L15 8.5l5 6" />
      <circle cx="8" cy="11" r="1.1" fill="currentColor" stroke="none" />
    </>
  ),
};

/**
 * Inline SVG icon. Renders on `currentColor` so it inherits the theme; pass a
 * `title` only for icons that carry meaning on their own (an icon-only button
 * still needs its own aria-label / title attribute).
 */
export const Icon: React.FC<IconProps> = ({
  name,
  size = 16,
  className,
  strokeWidth = 1.5,
  title,
}) => (
  <svg
    className={className ? `icon ${className}` : "icon"}
    width={size}
    height={size}
    viewBox="0 0 24 24"
    fill="none"
    stroke="currentColor"
    strokeWidth={strokeWidth}
    strokeLinecap="round"
    strokeLinejoin="round"
    aria-hidden={title ? undefined : true}
    role={title ? "img" : undefined}
    focusable="false"
  >
    {title && <title>{title}</title>}
    {PATHS[name]}
  </svg>
);

// Map a data-source type to its icon. Unknown types fall back to the generic
// stacked-disc database mark rather than a blank space.
export function sourceIcon(sourceType: string): IconName {
  switch (sourceType) {
    case "postgresql":
      return "postgresql";
    case "mongodb":
      return "mongodb";
    case "elasticsearch":
      return "elasticsearch";
    case "mysql":
      return "mysql";
    case "trino":
      return "trino";
    case "zoho_books":
      return "zoho-books";
    case "tally":
      return "tally";
    default:
      return "database";
  }
}

// Short uppercase badge for a source type — mirrors sourceIcon so the two
// never disagree (the old code labelled Elasticsearch "DB").
export function sourceBadge(sourceType: string): string {
  switch (sourceType) {
    case "postgresql":
      return "PG";
    case "mongodb":
      return "MDB";
    case "elasticsearch":
      return "ES";
    case "mysql":
      return "SQL";
    case "trino":
      return "TRN";
    case "zoho_books":
      return "ZOHO";
    case "tally":
      return "TLY";
    default:
      return "DB";
  }
}

export default Icon;
