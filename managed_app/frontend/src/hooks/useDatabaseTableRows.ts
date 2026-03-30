import { useEffect, useState } from "react";

export interface DatabaseTableRows {
  table: string;
  page: number;
  pageSize: number;
  totalRows: number;
  totalPages: number;
  rows: Record<string, unknown>[];
}

interface UseDatabaseTableRowsResult {
  data: DatabaseTableRows | null;
  loading: boolean;
  error: string | null;
}

/**
 * Fetches paginated rows for a selected database table.
 */
export function useDatabaseTableRows(
  tableName: string | null,
  page: number
): UseDatabaseTableRowsResult {
  const [data, setData] = useState<DatabaseTableRows | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!tableName) {
      setData(null);
      setLoading(false);
      setError(null);
      return;
    }

    let cancelled = false;
    const requestedTable = tableName;

    async function fetchRows() {
      try {
        setLoading(true);
        setError(null);
        setData(null);
        const res = await fetch(
          `/api/v1/database/tables/${encodeURIComponent(requestedTable)}/rows?page=${page}`
        );
        const payload = await res.json();

        if (!res.ok) {
          throw new Error(payload.detail ?? `HTTP ${res.status}`);
        }
        if (payload.error) {
          throw new Error(payload.error);
        }

        if (!cancelled) {
          setData({
            table: payload.table ?? requestedTable,
            page: payload.page ?? page,
            pageSize: payload.page_size ?? 20,
            totalRows: payload.total_rows ?? 0,
            totalPages: payload.total_pages ?? 1,
            rows: payload.rows ?? [],
          });
        }
      } catch (err) {
        if (!cancelled) {
          setError(err instanceof Error ? err.message : "Unknown error");
          setData(null);
        }
      } finally {
        if (!cancelled) {
          setLoading(false);
        }
      }
    }

    fetchRows();

    return () => {
      cancelled = true;
    };
  }, [page, tableName]);

  return { data, loading, error };
}
