import { useState } from "react";
import { useDatabaseSchema } from "../hooks/useDatabaseSchema";
import "./DatabaseView.css";

export function DatabaseView() {
  const { schema, loading, error } = useDatabaseSchema();
  const [selectedTable, setSelectedTable] = useState<string | null>(null);

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

  const currentTable = selectedTable || schema.tables[0];
  const columns = schema.columns[currentTable] || [];

  return (
    <div className="database-view">
      {/* Left panel: Table list */}
      <div className="database-tables-panel">
        <div className="database-panel-header">Tables ({schema.tables.length})</div>
        <div className="database-tables-list">
          {schema.tables.map((tableName) => (
            <button
              key={tableName}
              className={`database-table-item ${
                selectedTable === tableName ? "active" : ""
              }`}
              onClick={() => setSelectedTable(tableName)}
            >
              <span className="database-table-icon">📋</span>
              {tableName}
            </button>
          ))}
        </div>
      </div>

      {/* Right panel: Columns */}
      <div className="database-columns-panel">
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
                    <td className="column-nullable">
                      {col.nullable ? "✓" : "—"}
                    </td>
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
    </div>
  );
}
