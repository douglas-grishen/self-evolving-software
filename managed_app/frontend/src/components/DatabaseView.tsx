import { useEffect, useState } from "react";
import { useDatabaseSchema } from "../hooks/useDatabaseSchema";
import { useDatabaseTableRows } from "../hooks/useDatabaseTableRows";
import "./DatabaseView.css";

function renderCellValue(value: unknown) {
  if (value === null || value === undefined) {
    return <span className="database-null">NULL</span>;
  }

  if (typeof value === "object") {
    return <code>{JSON.stringify(value)}</code>;
  }

  if (typeof value === "boolean") {
    return value ? "true" : "false";
  }

  return String(value);
}

export function DatabaseView() {
  const { schema, loading, error } = useDatabaseSchema();
  const [selectedTable, setSelectedTable] = useState<string | null>(null);
  const [page, setPage] = useState(1);
  const currentTable = schema ? selectedTable || schema.tables[0] || null : null;
  const columns = currentTable && schema ? schema.columns[currentTable] || [] : [];
  const {
    data: tableRows,
    loading: rowsLoading,
    error: rowsError,
  } = useDatabaseTableRows(currentTable, page);

  useEffect(() => {
    setPage(1);
  }, [currentTable]);

  if (loading) {
    return (
      <div className="database-view">
        <div className="database-loading">Loading database schema...</div>
      </div>
    );
  }

  if (error) {
    return (
      <div className="database-view">
        <div className="database-error">Error: {error}</div>
      </div>
    );
  }

  if (!schema || schema.tables.length === 0) {
    return (
      <div className="database-view">
        <div className="database-empty">No tables found in the database.</div>
      </div>
    );
  }

  return (
    <div className="database-view">
      <div className="database-tables-panel">
        <div className="database-panel-header">Tables ({schema.tables.length})</div>
        <div className="database-tables-list">
          {schema.tables.map((tableName) => (
            <button
              key={tableName}
              className={`database-table-item ${currentTable === tableName ? "active" : ""}`}
              onClick={() => setSelectedTable(tableName)}
            >
              <span className="database-table-icon">📋</span>
              {tableName}
            </button>
          ))}
        </div>
      </div>

      <div className="database-columns-panel">
        <div className="database-section">
          <div className="database-panel-header">
            Columns: <code>{currentTable}</code> ({columns.length})
          </div>
          <div className="database-columns-list">
            {columns.length === 0 ? (
              <div className="database-empty">No columns found.</div>
            ) : (
              <table className="database-columns-table">
                <thead>
                  <tr>
                    <th>Name</th>
                    <th>Type</th>
                    <th>Nullable</th>
                    <th>Default</th>
                  </tr>
                </thead>
                <tbody>
                  {columns.map((col) => (
                    <tr key={col.name}>
                      <td className="column-name">
                        <code>{col.name}</code>
                      </td>
                      <td className="column-type">{col.type}</td>
                      <td className="column-nullable">{col.nullable ? "✓" : "—"}</td>
                      <td className="column-default">
                        {col.default ? <code>{col.default}</code> : "—"}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </div>
        </div>

        <div className="database-section database-rows-section">
          <div className="database-panel-header database-panel-header-split">
            <div>
              Rows: <code>{currentTable}</code> ({tableRows?.totalRows ?? 0})
            </div>
            <div className="database-pagination">
              <button
                className="database-page-button"
                onClick={() => setPage((currentPage) => Math.max(1, currentPage - 1))}
                disabled={page === 1 || rowsLoading}
              >
                Previous
              </button>
              <span className="database-page-indicator">
                Page {tableRows?.page ?? page} / {tableRows?.totalPages ?? 1}
              </span>
              <button
                className="database-page-button"
                onClick={() =>
                  setPage((currentPage) =>
                    Math.min(tableRows?.totalPages ?? currentPage, currentPage + 1)
                  )
                }
                disabled={rowsLoading || page >= (tableRows?.totalPages ?? 1)}
              >
                Next
              </button>
            </div>
          </div>
          <div className="database-rows-list">
            {rowsLoading ? (
              <div className="database-empty">Loading rows...</div>
            ) : rowsError ? (
              <div className="database-error">Error: {rowsError}</div>
            ) : !tableRows || tableRows.rows.length === 0 ? (
              <div className="database-empty">No rows found in this table.</div>
            ) : (
              <table className="database-columns-table database-rows-table">
                <thead>
                  <tr>
                    {columns.map((col) => (
                      <th key={col.name}>
                        <code>{col.name}</code>
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {tableRows.rows.map((row, index) => (
                    <tr key={`${currentTable}-${page}-${index}`}>
                      {columns.map((col) => (
                        <td key={col.name} className="database-row-cell">
                          {renderCellValue(row[col.name])}
                        </td>
                      ))}
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
