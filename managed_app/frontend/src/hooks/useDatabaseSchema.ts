import { useEffect, useState } from "react";

export interface Column {
  name: string;
  type: string;
  nullable: boolean;
  default: string | null;
}

export interface DatabaseSchema {
  tables: string[];
  columns: Record<string, Column[]>;
}

interface UseDatabaseSchemaResult {
  schema: DatabaseSchema | null;
  loading: boolean;
  error: string | null;
}

/**
 * Fetches database schema including tables and columns.
 */
export function useDatabaseSchema(): UseDatabaseSchemaResult {
  const [schema, setSchema] = useState<DatabaseSchema | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;

    async function fetchSchema() {
      try {
        setLoading(true);
        const res = await fetch("/api/v1/database/tables");
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const data = await res.json();
        if (!cancelled) {
          if (data.error) {
            setError(data.error);
            setSchema(null);
          } else {
            setSchema({
              tables: data.tables || [],
              columns: data.columns || {}
            });
            setError(null);
          }
        }
      } catch (err) {
        if (!cancelled) {
          setError(err instanceof Error ? err.message : "Unknown error");
          setSchema(null);
        }
      } finally {
        if (!cancelled) setLoading(false);
      }
    }

    fetchSchema();

    return () => {
      cancelled = true;
    };
  }, []);

  return { schema, loading, error };
}
