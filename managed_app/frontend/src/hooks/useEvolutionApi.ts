import { useCallback, useEffect, useState } from "react";
import { getAuthToken } from "./useAuth";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export interface EvolutionEvent {
  id: string;
  request_id: string;
  status: string;
  source: string;
  user_request: string;
  plan_summary: string | null;
  risk_level: string | null;
  validation_passed: boolean | null;
  deployment_success: boolean | null;
  commit_sha: string | null;
  branch: string | null;
  error: string | null;
  created_at: string;
  completed_at: string | null;
  events_json: Record<string, unknown> | null;
}

export interface Inception {
  id: string;
  source: string;
  directive: string;
  rationale: string;
  status: string;
  submitted_at: string;
  processed_at: string | null;
  previous_purpose_version: number | null;
  new_purpose_version: number | null;
  changes_summary: string | null;
}

export interface Purpose {
  id: string;
  version: number;
  content_yaml: string;
  created_at: string;
  inception_id: string | null;
}

export interface DashboardStatus {
  total_evolutions: number;
  active_evolutions: number;
  completed_evolutions: number;
  failed_evolutions: number;
  current_purpose_version: number | null;
  pending_inceptions: number;
  last_evolution: EvolutionEvent | null;
}

// ---------------------------------------------------------------------------
// Hooks
// ---------------------------------------------------------------------------

export function useEvolutionStatus(intervalMs = 5000) {
  const [status, setStatus] = useState<DashboardStatus | null>(null);
  const [error, setError] = useState<string | null>(null);

  const poll = useCallback(async () => {
    try {
      const res = await fetch("/api/v1/evolution/status");
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data: DashboardStatus = await res.json();
      setStatus(data);
      setError(null);
    } catch (err) {
      setError((err as Error).message);
    }
  }, []);

  useEffect(() => {
    poll();
    const timer = setInterval(poll, intervalMs);
    return () => clearInterval(timer);
  }, [poll, intervalMs]);

  return { status, error, refresh: poll };
}

export function useEvolutionEvents() {
  const [events, setEvents] = useState<EvolutionEvent[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    try {
      const res = await fetch("/api/v1/evolution/events?limit=50");
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data: EvolutionEvent[] = await res.json();
      setEvents(data);
      setError(null);
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    refresh();
  }, [refresh]);

  return { events, loading, error, refresh };
}

export function usePurpose() {
  const [purpose, setPurpose] = useState<Purpose | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    (async () => {
      try {
        const res = await fetch("/api/v1/evolution/purpose");
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const data = await res.json();
        setPurpose(data);
        setError(null);
      } catch (err) {
        setError((err as Error).message);
      } finally {
        setLoading(false);
      }
    })();
  }, []);

  return { purpose, loading, error };
}

export function useInceptions() {
  const [inceptions, setInceptions] = useState<Inception[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    try {
      const res = await fetch("/api/v1/evolution/inceptions?limit=50");
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data: Inception[] = await res.json();
      setInceptions(data);
      setError(null);
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    refresh();
  }, [refresh]);

  return { inceptions, loading, error, refresh };
}

export function useSubmitInception() {
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const submit = useCallback(
    async (directive: string, rationale: string, source = "human") => {
      setSubmitting(true);
      setError(null);
      try {
        const token = getAuthToken();
        const headers: Record<string, string> = {
          "Content-Type": "application/json",
        };
        if (token) headers["Authorization"] = `Bearer ${token}`;
        const res = await fetch("/api/v1/evolution/inceptions", {
          method: "POST",
          headers,
          body: JSON.stringify({ source, directive, rationale }),
        });
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        return await res.json();
      } catch (err) {
        setError((err as Error).message);
        return null;
      } finally {
        setSubmitting(false);
      }
    },
    [],
  );

  return { submit, submitting, error };
}
