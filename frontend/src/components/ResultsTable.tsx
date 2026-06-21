import React, { useState, useMemo } from "react";

interface ResultsTableProps {
  columns: string[];
  rows: unknown[][];
  rowCount: number;
}

type SortDirection = "asc" | "desc" | null;

const ResultsTable: React.FC<ResultsTableProps> = ({
  columns,
  rows,
  rowCount,
}) => {
  const [sortCol, setSortCol] = useState<number | null>(null);
  const [sortDir, setSortDir] = useState<SortDirection>(null);
  const [page, setPage] = useState(0);
  const PAGE_SIZE = 25;

  const sortedRows = useMemo(() => {
    if (sortCol === null || sortDir === null) return rows;
    return [...rows].sort((a, b) => {
      const av = a[sortCol];
      const bv = b[sortCol];
      if (av === null || av === undefined) return 1;
      if (bv === null || bv === undefined) return -1;
      const cmp = av < bv ? -1 : av > bv ? 1 : 0;
      return sortDir === "asc" ? cmp : -cmp;
    });
  }, [rows, sortCol, sortDir]);

  const pagedRows = sortedRows.slice(page * PAGE_SIZE, (page + 1) * PAGE_SIZE);
  const totalPages = Math.ceil(rows.length / PAGE_SIZE);

  const handleSort = (colIdx: number) => {
    if (sortCol === colIdx) {
      setSortDir((d) => (d === "asc" ? "desc" : d === "desc" ? null : "asc"));
      if (sortDir === "desc") setSortCol(null);
    } else {
      setSortCol(colIdx);
      setSortDir("asc");
    }
  };

  const formatValue = (val: unknown): string => {
    if (val === null || val === undefined) return "—";
    if (typeof val === "number") {
      return Number.isInteger(val) ? val.toLocaleString() : val.toFixed(2);
    }
    return String(val);
  };

  const isNumericColumn = (colIdx: number): boolean => {
    const sample = rows
      .slice(0, 5)
      .map((r) => r[colIdx])
      .filter((v) => v !== null);
    return sample.length > 0 && sample.every((v) => typeof v === "number");
  };

  if (columns.length === 0 || rows.length === 0) {
    return (
      <div className="card">
        <div className="empty-state">
          <span className="empty-icon">📋</span>
          <span>No results returned</span>
        </div>
      </div>
    );
  }

  return (
    <div className="card results-table-card">
      <div className="card-header">
        <div className="card-title">
          <span className="section-title-icon">📋</span>
          <span className="section-title">Results</span>
          <span className="badge">{rowCount.toLocaleString()} rows</span>
        </div>
        {totalPages > 1 && (
          <div className="pagination">
            <button
              id="prev-page-btn"
              className="page-btn"
              onClick={() => setPage((p) => Math.max(0, p - 1))}
              disabled={page === 0}
            >
              ‹
            </button>
            <span>
              {page + 1} / {totalPages}
            </span>
            <button
              id="next-page-btn"
              className="page-btn"
              onClick={() => setPage((p) => Math.min(totalPages - 1, p + 1))}
              disabled={page === totalPages - 1}
            >
              ›
            </button>
          </div>
        )}
      </div>

      <div className="table-wrapper">
        <table className="results-table">
          <thead>
            <tr>
              {columns.map((col, i) => (
                <th
                  key={i}
                  className={`
                    ${isNumericColumn(i) ? "col-numeric" : ""}
                    ${sortCol === i ? "col-sorted" : ""}
                  `}
                  onClick={() => handleSort(i)}
                >
                  <span>{col}</span>
                  <span className="sort-indicator">
                    {sortCol === i && sortDir === "asc"
                      ? "↑"
                      : sortCol === i && sortDir === "desc"
                        ? "↓"
                        : "↕"}
                  </span>
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {pagedRows.map((row, ri) => (
              <tr key={ri} className={ri % 2 === 0 ? "row-even" : "row-odd"}>
                {row.map((val, ci) => (
                  <td
                    key={ci}
                    className={isNumericColumn(ci) ? "col-numeric" : ""}
                  >
                    {formatValue(val)}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
};

export default ResultsTable;
