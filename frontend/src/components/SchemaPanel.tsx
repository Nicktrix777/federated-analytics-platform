import React, { useMemo, useState } from "react";
import type { DatasetMeta, DatasetColumn } from "../types";
import { Icon, sourceIcon, sourceBadge } from "./ui/Icon";

interface SchemaPanelProps {
  datasets: DatasetMeta[];
  onQueryTable: (trinoPath: string) => void;
  onInsertColumn: (trinoPath: string, columnName: string) => void;
}

// ── Column type vocabulary ────────────────────────────────────
// Trino surfaces composite types as full signatures — a nested Elasticsearch
// document arrives as `ROW(AGENCY ROW(NAME ROW(AR VARCHAR ...)))`. The old
// classifier only looked for "varchar" and so labelled every composite column
// STR, which told the reader the opposite of the truth.

type TypeClass = "text" | "number" | "time" | "boolean" | "object" | "list" | "other";

function typeClass(dataType: string): TypeClass {
  const t = (dataType || "").toLowerCase().trim();
  if (t.startsWith("array") || t.startsWith("map")) return "list";
  if (t.startsWith("row")) return "object";
  if (/(timestamp|date|time)/.test(t)) return "time";
  if (/(bool)/.test(t)) return "boolean";
  if (/(int|double|float|decimal|numeric|real|bigint|smallint|tinyint)/.test(t)) return "number";
  if (/(varchar|char|text|json|uuid|ipaddress)/.test(t)) return "text";
  return "other";
}

function shortType(dataType: string): string {
  const t = (dataType || "").toUpperCase().trim();
  if (t.startsWith("ARRAY")) return "LIST";
  if (t.startsWith("MAP")) return "MAP";
  if (t.startsWith("ROW")) return "OBJ";
  if (t.includes("TIMESTAMP")) return "TS";
  if (t.includes("DATE")) return "DATE";
  if (t.includes("TIME")) return "TIME";
  if (t.includes("BOOL")) return "BOOL";
  if (t.includes("VARCHAR") || t.includes("CHAR")) return "STR";
  if (t.includes("TEXT")) return "TEXT";
  if (t.includes("BIGINT") || t.includes("INT")) return "INT";
  if (t.includes("DOUBLE") || t.includes("FLOAT") || t.includes("DECIMAL") || t.includes("NUMERIC") || t.includes("REAL"))
    return "NUM";
  if (t.includes("JSON")) return "JSON";
  return t.slice(0, 4) || "?";
}

/** Count the immediate fields of a ROW(...) / ARRAY(ROW(...)) signature. */
function nestedFieldCount(dataType: string): number {
  const t = (dataType || "").trim();
  const open = t.indexOf("(");
  if (open < 0) return 0;
  let depth = 0;
  let fields = 0;
  let sawContent = false;
  for (let i = open; i < t.length; i++) {
    const c = t[i];
    if (c === "(") depth++;
    else if (c === ")") {
      depth--;
      if (depth === 0) break;
    } else if (c === "," && depth === 1) fields++;
    else if (depth >= 1 && c.trim()) sawContent = true;
  }
  return sawContent ? fields + 1 : 0;
}

// ── Dataset row ───────────────────────────────────────────────

interface DatasetRowProps {
  dataset: DatasetMeta;
  /** Lower-cased search term, "" when not filtering. */
  query: string;
  forceOpen: boolean;
  onQueryTable: (trinoPath: string) => void;
  onInsertColumn: (trinoPath: string, columnName: string) => void;
}

const DatasetRow: React.FC<DatasetRowProps> = ({
  dataset,
  query,
  forceOpen,
  onQueryTable,
  onInsertColumn,
}) => {
  const [open, setOpen] = useState(false);
  const expanded = forceOpen || open;

  const columns = dataset.columns ?? [];
  const visibleColumns = useMemo(() => {
    if (!query) return columns;
    return columns.filter((c) => c.column_name.toLowerCase().includes(query));
  }, [columns, query]);

  return (
    <div className="schema-dataset">
      <button
        className="schema-dataset-header"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={expanded}
      >
        <Icon
          name="chevron-right"
          size={12}
          className={`schema-dataset-chevron${expanded ? " schema-dataset-chevron--open" : ""}`}
        />
        <div className="schema-dataset-info">
          <div className="schema-dataset-name" title={dataset.name}>
            {dataset.name}
          </div>
          <div className="schema-dataset-path" title={dataset.trino_path}>
            {dataset.trino_path}
          </div>
        </div>
        <span className="schema-dataset-count">
          {columns.length}
          <span className="schema-dataset-count-unit">col</span>
        </span>
      </button>

      {expanded && (
        <>
          <div className="schema-columns">
            {visibleColumns.length > 0 ? (
              visibleColumns.map((col: DatasetColumn) => {
                const cls = typeClass(col.data_type);
                const nested = cls === "object" || cls === "list" ? nestedFieldCount(col.data_type) : 0;
                return (
                  <button
                    key={col.column_name}
                    className="schema-column"
                    onClick={() => onInsertColumn(dataset.trino_path, col.column_name)}
                    title={[
                      `${col.column_name} — ${col.data_type}`,
                      col.description || null,
                      col.sample_values ? `Samples: ${col.sample_values}` : null,
                      nested > 0
                        ? `Composite column with ${nested} top-level field${nested === 1 ? "" : "s"} — dereference a leaf, e.g. ${col.column_name}.field`
                        : null,
                    ]
                      .filter(Boolean)
                      .join("\n")}
                  >
                    <span className={`schema-column-type schema-column-type--${cls}`}>
                      {shortType(col.data_type)}
                    </span>
                    <span className="schema-column-name">{col.column_name}</span>
                    {nested > 0 && <span className="schema-column-nested">{nested}</span>}
                    {col.is_joinable && (
                      <Icon name="link" size={11} className="schema-column-key" title="Joinable column" />
                    )}
                  </button>
                );
              })
            ) : (
              <div className="schema-columns-empty">
                {query ? "No matching columns" : "No column metadata"}
              </div>
            )}
          </div>
          <button
            className="schema-query-btn"
            onClick={(e) => {
              e.stopPropagation();
              onQueryTable(dataset.trino_path);
            }}
          >
            <Icon name="play" size={11} />
            {/* Label the action, not an abbreviated SQL string — the query
                that actually runs uses the full quoted Trino path. */}
            <span>Preview 10 rows</span>
          </button>
        </>
      )}
    </div>
  );
};

// ── Panel ─────────────────────────────────────────────────────

interface SourceGroup {
  sourceType: string;
  catalog: string;
  datasets: DatasetMeta[];
}

/** Catalog is the first segment of the Trino path — the connected source. */
function catalogOf(path: string): string {
  const first = (path || "").split(".")[0] ?? "";
  return first.replace(/"/g, "") || "unknown";
}

const SchemaPanel: React.FC<SchemaPanelProps> = ({
  datasets,
  onQueryTable,
  onInsertColumn,
}) => {
  const [search, setSearch] = useState("");
  const [collapsedGroups, setCollapsedGroups] = useState<Record<string, boolean>>({});
  const query = search.trim().toLowerCase();

  // Filter on dataset name, Trino path AND column names, so searching
  // "salary" surfaces the tables that actually have it.
  const filtered = useMemo(() => {
    if (!query) return datasets;
    return datasets.filter(
      (d) =>
        d.name.toLowerCase().includes(query) ||
        d.trino_path.toLowerCase().includes(query) ||
        (d.columns ?? []).some((c) => c.column_name.toLowerCase().includes(query))
    );
  }, [datasets, query]);

  // Group by connected source so the panel answers "what am I connected to?"
  // before it answers "what tables exist?".
  const groups = useMemo<SourceGroup[]>(() => {
    const byCatalog = new Map<string, SourceGroup>();
    for (const d of filtered) {
      const catalog = catalogOf(d.trino_path);
      const existing = byCatalog.get(catalog);
      if (existing) existing.datasets.push(d);
      else byCatalog.set(catalog, { catalog, sourceType: d.source_type, datasets: [d] });
    }
    return Array.from(byCatalog.values())
      .map((g) => ({ ...g, datasets: g.datasets.slice().sort((a, b) => a.name.localeCompare(b.name)) }))
      .sort((a, b) => a.catalog.localeCompare(b.catalog));
  }, [filtered]);

  const totalColumns = useMemo(
    () => datasets.reduce((n, d) => n + (d.columns?.length ?? 0), 0),
    [datasets]
  );

  if (datasets.length === 0) {
    return (
      <div className="schema-panel">
        <div className="schema-empty">
          <Icon name="database" size={24} className="schema-empty-icon" />
          <div>No datasets registered</div>
          <div className="schema-empty-hint">
            Connect a data source or upload a CSV, then run Sync Catalogs.
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="schema-panel">
      <div className="schema-search">
        <Icon name="search" size={13} className="schema-search-icon" />
        <input
          className="schema-search-input"
          type="search"
          value={search}
          placeholder="Search tables and columns"
          onChange={(e) => setSearch(e.target.value)}
          aria-label="Search schema"
        />
        {search && (
          <button className="schema-search-clear" onClick={() => setSearch("")} aria-label="Clear search">
            <Icon name="close" size={12} />
          </button>
        )}
      </div>

      <div className="schema-summary">
        {datasets.length} table{datasets.length === 1 ? "" : "s"} · {totalColumns} columns ·{" "}
        {groups.length || 1} source{(groups.length || 1) === 1 ? "" : "s"}
      </div>

      <div className="schema-scroll">
        {groups.length === 0 && (
          <div className="schema-columns-empty">No tables match “{search}”.</div>
        )}

        {groups.map((group) => {
          const collapsed = collapsedGroups[group.catalog] ?? false;
          return (
            <section key={group.catalog} className="schema-group">
              <button
                className="schema-group-header"
                onClick={() =>
                  setCollapsedGroups((prev) => ({ ...prev, [group.catalog]: !collapsed }))
                }
                aria-expanded={!collapsed}
              >
                <Icon
                  name="chevron-down"
                  size={12}
                  className={`schema-group-chevron${collapsed ? " schema-group-chevron--closed" : ""}`}
                />
                <Icon name={sourceIcon(group.sourceType)} size={14} className="schema-group-icon" />
                <span className="schema-group-name">{group.catalog}</span>
                <span className={`schema-group-badge schema-group-badge--${group.sourceType}`}>
                  {sourceBadge(group.sourceType)}
                </span>
                <span className="schema-group-dot" title="Connected" aria-label="Connected" />
              </button>

              {!collapsed && (
                <div className="schema-group-body">
                  {group.datasets.map((ds) => (
                    <DatasetRow
                      key={ds.id}
                      dataset={ds}
                      query={query}
                      // While searching, open the matches — otherwise the hit
                      // is hidden behind a collapsed row.
                      forceOpen={
                        query.length > 0 &&
                        (ds.columns ?? []).some((c) => c.column_name.toLowerCase().includes(query))
                      }
                      onQueryTable={onQueryTable}
                      onInsertColumn={onInsertColumn}
                    />
                  ))}
                </div>
              )}
            </section>
          );
        })}
      </div>
    </div>
  );
};

export default SchemaPanel;
