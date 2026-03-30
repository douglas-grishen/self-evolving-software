import { useCallback, useEffect, useState } from "react";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export interface CapabilityBrief {
  id: string;
  name: string;
  status: string;
  is_background: boolean;
}

export interface Feature {
  id: string;
  app_id: string;
  name: string;
  description: string;
  user_facing_description: string;
  status: string;
  created_at: string;
  capabilities: CapabilityBrief[];
}

export interface AppBrief {
  id: string;
  name: string;
  icon: string;
  status: string;
  goal: string;
  feature_count: number;
  capability_count: number;
}

export interface AppFull {
  id: string;
  name: string;
  description: string;
  icon: string;
  goal: string;
  status: string;
  created_at: string;
  updated_at: string;
  created_by_evolution_id: string | null;
  features: Feature[];
  capabilities: CapabilityBrief[];
  metadata_json: Record<string, unknown> | null;
}

function normalizeAppsPayload(payload: unknown): AppBrief[] {
  if (Array.isArray(payload)) return payload as AppBrief[];
  if (
    payload !== null &&
    typeof payload === "object" &&
    Array.isArray((payload as { apps?: unknown[] }).apps)
  ) {
    return (payload as { apps: AppBrief[] }).apps;
  }
  return [];
}

// ---------------------------------------------------------------------------
// Hooks
// ---------------------------------------------------------------------------

export function useApps(intervalMs = 10000) {
  const [apps, setApps] = useState<AppBrief[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    try {
      const res = await fetch("/api/v1/apps");
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = normalizeAppsPayload(await res.json());
      setApps(data);
      setError(null);
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    refresh();
    const timer = setInterval(refresh, intervalMs);
    return () => clearInterval(timer);
  }, [refresh, intervalMs]);

  return { apps, loading, error, refresh };
}

export function useApp(appId: string | null) {
  const [app, setApp] = useState<AppFull | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    if (!appId) return;
    setLoading(true);
    try {
      const res = await fetch(`/api/v1/apps/${appId}`);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data: AppFull = await res.json();
      setApp(data);
      setError(null);
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setLoading(false);
    }
  }, [appId]);

  useEffect(() => {
    refresh();
  }, [refresh]);

  return { app, loading, error, refresh };
}
