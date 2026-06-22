import React, { useState } from "react";
import type { DatasetMeta, DatasetColumn } from "../types";

interface SchemaPanelProps {
  datasets: DatasetMeta[];
  onQueryTable: (trinoPath: string) => void;
  onInsertColumn: (trinoPath: string, columnName: string) => void;
}

function getTypeClass(dataType: string): string {
  const t = dataType.toLowerCase();
  if (t.includes("varchar") || t.includes("text") || t.includes("char")) return "varchar";
  if (t.includes("bigint") || t.includes("int") || t.includes("serial")) return "bigint";
  if (t.includes("double") || t.includes("float") || t.includes("decimal") || t.includes("numeric") || t.includes("real")) return "bigint";
  if (t.includes("date") || t.includes("time") || t.includes("timestamp")) return "date";
  if (t.includes("bool")) return "boolean";
  return "other";
}

function shortType(dataType: string): string {
  const t = dataType.toUpperCase();
  if (t.includes("VARCHAR")) return "STR";
  if (t.includes("BIGINT") || t.includes("INT")) return "INT";
  if (t.includes("DOUBLE") || t.includes("FLOAT") || t.includes("DECIMAL") || t.includes("NUMERIC")) return "NUM";
  if (t.includes("TIMESTAMP")) return "TS";
  if (t.includes("DATE")) return "DATE";
  if (t.includes("BOOL")) return "BOOL";
  if (t.includes("TEXT")) return "TEXT";
  return t.slice(0, 4);
}

function sourceIcon(sourceType: string): string {
  if (sourceType === "postgresql") return "🐘";
  if (sourceType === "mongodb") return "🍃";
  return "🗄";
}

interface DatasetRowProps {
  dataset: DatasetMeta;
  onQueryTable: (trinoPath: string) => void;
  onInsertColumn: (trinoPath: string, columnName: string) => void;
}

const DatasetRow: React.FC<DatasetRowProps> = ({ dataset, onQueryTable, onInsertColumn }) => {
  const [expanded, setExpanded] = useState(false);

  return (
    <div className="schema-dataset">
      <div
        className="schema-dataset-header"
        onClick={() => setExpanded((v) => !v)}
      >
        <span className={`schema-dataset-chevron ${expanded ? "schema-dataset-chevron--open" : ""}`}>
          ▶
        </span>
        <span className="schema-dataset-icon">{sourceIcon(dataset.source_type)}</span>
        <div className="schema-dataset-info">
          <div className="schema-dataset-name">{dataset.name}</div>
          <div className="schema-dataset-path">{dataset.trino_path}</div>
        </div>
        <span className={`schema-dataset-badge schema-dataset-badge--${dataset.source_type}`}>
          {dataset.source_type === "postgresql" ? "PG" : dataset.source_type === "mongodb" ? "MDB" : "DB"}
        </span>
      </div>

      {expanded && (
        <>
          <div className="schema-columns">
            {dataset.columns && dataset.columns.length > 0 ? (
              dataset.columns.map((col: DatasetColumn) => (
                <div
                  key={col.column_name}
                  className="schema-column"
                  onClick={() => onInsertColumn(dataset.trino_path, col.column_name)}
                  title={`${col.description || col.column_name}${col.sample_values ? `\nSamples: ${col.sample_values}` : ""}`}
                >
                  <span className={`schema-column-type schema-column-type--${getTypeClass(col.data_type)}`}>
                    {shortType(col.data_type)}
                  </span>
                  <span className="schema-column-name">{col.column_name}</span>
                  {col.is_joinable && <span className="schema-column-key" title="Joinable column">⟷</span>}
                </div>
              ))
            ) : (
              <div style={{ padding: "8px 10px", fontSize: "11px", color: "var(--text-muted)" }}>
                No column metadata
              </div>
            )}
          </div>
          <button
            className="schema-query-btn"
            onClick={(e) => { e.stopPropagation(); onQueryTable(dataset.trino_path); }}
          >
            <span>▶</span>
            <span>SELECT * FROM {dataset.trino_path} LIMIT 10</span>
          </button>
        </>
      )}
    </div>
  );
};

const SchemaPanel: React.FC<SchemaPanelProps> = ({
  datasets,
  onQueryTable,
  onInsertColumn,
}) => {
  return (
    <div className="schema-panel">
      {datasets.length === 0 ? (
        <div className="schema-empty">
          <div style={{ fontSize: "22px", opacity: 0.3 }}>🗄</div>
          <div>No datasets registered</div>
          <div style={{ fontSize: "10.5px", color: "var(--text-faint)", marginTop: "2px" }}>
            Upload a CSV or connect a data source
          </div>
        </div>
      ) : (
        datasets.map((ds) => (
          <DatasetRow
            key={ds.id}
            dataset={ds}
            onQueryTable={onQueryTable}
            onInsertColumn={onInsertColumn}
          />
        ))
      )}
    </div>
  );
};

export default SchemaPanel;
