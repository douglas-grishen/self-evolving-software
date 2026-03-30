import type { EvolutionEvent } from "../../hooks/useEvolutionApi";

export interface EventHistoryEntry {
  timestamp: string;
  agent: string;
  action: string;
  status: string;
  details?: string;
}

export interface EventPlanChange {
  file_path: string;
  action: string;
  description: string;
  layer: string;
}

const TERMINAL_STATUSES = new Set(["completed", "failed"]);

export function getActiveEvolutionEvent(events: EvolutionEvent[]): EvolutionEvent | null {
  return events.find((event) => !TERMINAL_STATUSES.has(event.status)) ?? null;
}

export function parseExecutionHistory(event: EvolutionEvent | null | undefined): EventHistoryEntry[] {
  const payload = event?.events_json;
  if (!payload || typeof payload !== "object") return [];

  const history = (payload as Record<string, unknown>).history;
  if (!Array.isArray(history)) return [];

  return history.flatMap((entry) => {
    if (!entry || typeof entry !== "object") return [];
    const raw = entry as Record<string, unknown>;
    return [
      {
        timestamp: String(raw.timestamp ?? ""),
        agent: String(raw.agent ?? "engine"),
        action: String(raw.action ?? "update"),
        status: String(raw.status ?? "received"),
        details: raw.details ? String(raw.details) : undefined,
      },
    ];
  });
}

export function parsePlanChanges(event: EvolutionEvent | null | undefined): EventPlanChange[] {
  const payload = event?.events_json;
  if (!payload || typeof payload !== "object") return [];

  const planChanges = (payload as Record<string, unknown>).plan_changes;
  if (!Array.isArray(planChanges)) return [];

  return planChanges.flatMap((change) => {
    if (!change || typeof change !== "object") return [];
    const raw = change as Record<string, unknown>;
    return [
      {
        file_path: String(raw.file_path ?? ""),
        action: String(raw.action ?? "modify"),
        description: String(raw.description ?? ""),
        layer: String(raw.layer ?? "unknown"),
      },
    ];
  });
}

export function summarizeEvolutionEvent(event: EvolutionEvent | null | undefined): string {
  if (!event) return "No evolution cycle selected.";
  if (event.plan_summary?.trim()) return event.plan_summary.trim();

  const request = (event.user_request || "")
    .replace(/^\[Proactive[^\]]*\]\s*/i, "")
    .trim();

  if (!request) return "No request summary recorded.";
  if (request.length <= 180) return request;
  return `${request.slice(0, 179).trimEnd()}...`;
}
