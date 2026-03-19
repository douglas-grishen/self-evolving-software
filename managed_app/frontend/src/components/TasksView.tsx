import { useEffect, useState, useCallback } from "react";
import "./TasksView.css";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

interface FileChange {
  file_path: string;
  action: "create" | "modify" | "delete";
  description: string;
  layer: string;
}

interface PipelineStep {
  timestamp: string;
  agent: string;
  action: string;
  status: string;
  details: string;
}

interface EventsJson {
  plan_changes?: FileChange[];
  history?: PipelineStep[];
}

interface EvolutionEvent {
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
  error: string | null;
  created_at: string;
  completed_at: string | null;
  events_json: EventsJson | null;
}

// ---------------------------------------------------------------------------
// Hook
// ---------------------------------------------------------------------------

function useEvolutionEvents(intervalMs = 5000) {
  const [events, setEvents] = useState<EvolutionEvent[]>([]);
  const [loading, setLoading] = useState(true);

  const refresh = useCallback(async () => {
    try {
      const res = await fetch("/api/v1/evolution/events?limit=50");
      if (!res.ok) return;
      const data = await res.json();
      setEvents(data);
    } catch {
      // ignore
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    refresh();
    const t = setInterval(refresh, intervalMs);
    return () => clearInterval(t);
  }, [refresh, intervalMs]);

  return { events, loading };
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function taskTitle(ev: EvolutionEvent): string {
  if (ev.plan_summary) return ev.plan_summary;
  const raw = ev.user_request.replace(/^\[Proactive[^\]]*\]\s*/i, "").trim();
  const cut = raw.match(/^[^.!?\n]{10,120}[.!?]/);
  return cut ? cut[0] : raw.slice(0, 120) + (raw.length > 120 ? "…" : "");
}

function duration(ev: EvolutionEvent): string {
  if (!ev.completed_at) return "";
  const ms = new Date(ev.completed_at).getTime() - new Date(ev.created_at).getTime();
  const s = Math.round(ms / 1000);
  if (s < 60) return `${s}s`;
  return `${Math.floor(s / 60)}m ${s % 60}s`;
}

function timeAgo(iso: string): string {
  const diff = Date.now() - new Date(iso).getTime();
  const m = Math.floor(diff / 60000);
  if (m < 1) return "just now";
  if (m < 60) return `${m}m ago`;
  const h = Math.floor(m / 60);
  if (h < 24) return `${h}h ago`;
  return `${Math.floor(h / 24)}d ago`;
}

const ACTION_ICON: Record<string, string> = { create: "✨", modify: "✏️", delete: "🗑️" };
const ACTION_LABEL: Record<string, string> = { create: "created", modify: "modified", delete: "deleted" };
const LAYER_COLOR: Record<string, string> = {
  backend: "#818cf8",
  frontend: "#34d399",
  database: "#fb923c",
  config: "#94a3b8",
};

const STATUS_ICON: Record<string, string> = {
  completed: "✅",
  failed: "❌",
  received: "⏳",
  running: "⚙️",
};

const RISK_COLOR: Record<string, string> = {
  low: "#22c55e",
  medium: "#f59e0b",
  high: "#ef4444",
};

// ---------------------------------------------------------------------------
// TaskCard
// ---------------------------------------------------------------------------

function TaskCard({ ev }: { ev: EvolutionEvent }) {
  const [expanded, setExpanded] = useState(ev.status === "received" || ev.status === "running");
  const icon = STATUS_ICON[ev.status] ?? "🔄";
  const isActive = ev.status === "received" || ev.status === "running";
  const changes = ev.events_json?.plan_changes ?? [];

  return (
    <div
      className={`task-card task-card--${ev.status}`}
      onClick={() => setExpanded((x) => !x)}
    >
      <div className="task-card-header">
        <span className="task-status-icon">{icon}</span>
        <span className="task-title">{taskTitle(ev)}</span>
        <div className="task-meta">
          {ev.risk_level && (
            <span className="task-risk" style={{ color: RISK_COLOR[ev.risk_level] ?? "#94a3b8" }}>
              {ev.risk_level}
            </span>
          )}
          {changes.length > 0 && (
            <span className="task-files-badge">{changes.length} file{changes.length !== 1 ? "s" : ""}</span>
          )}
          <span className="task-source">{ev.source === "monitor" ? "⚡ auto" : "👤 user"}</span>
          <span className="task-time">{timeAgo(ev.created_at)}</span>
          {ev.completed_at && <span className="task-duration">{duration(ev)}</span>}
          <span className="task-chevron">{expanded ? "▲" : "▼"}</span>
        </div>
      </div>

      {isActive && (
        <div className="task-progress-bar">
          <div className="task-progress-bar-inner" />
        </div>
      )}

      {expanded && (
        <div className="task-card-body">
          {ev.error && <div className="task-error">⚠️ {ev.error}</div>}

          {/* File changes — the main thing users want to see */}
          {changes.length > 0 && (
            <div className="task-files">
              <div className="task-files-title">Files</div>
              <ul className="task-files-list">
                {changes.map((fc, i) => (
                  <li key={i} className="task-file-item">
                    <span className="task-file-action">{ACTION_ICON[fc.action]}</span>
                    <span className="task-file-layer" style={{ color: LAYER_COLOR[fc.layer] ?? "#94a3b8" }}>
                      {fc.layer}
                    </span>
                    <span className="task-file-path">{fc.file_path}</span>
                    <span className="task-file-verb">{ACTION_LABEL[fc.action]}</span>
                    {fc.description && (
                      <span className="task-file-desc">{fc.description}</span>
                    )}
                  </li>
                ))}
              </ul>
            </div>
          )}

          {/* Fallback: show request text if no plan_changes yet */}
          {changes.length === 0 && (
            <div className="task-request">
              <strong>What the engine will do:</strong>
              <p>{ev.user_request.replace(/^\[Proactive[^\]]*\]\s*/i, "").slice(0, 500)}
                {ev.user_request.length > 500 ? "…" : ""}
              </p>
            </div>
          )}

          {ev.commit_sha && (
            <div className="task-commit">
              🔀 <code>{ev.commit_sha.slice(0, 7)}</code>
              {ev.deployment_success === true && " deployed"}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// TasksView
// ---------------------------------------------------------------------------

export function TasksView() {
  const { events, loading } = useEvolutionEvents(5000);

  const active = events.filter((e) => e.status === "received" || e.status === "running");
  const done   = events.filter((e) => e.status === "completed");
  const failed = events.filter((e) => e.status === "failed");

  if (loading) return <div className="tasks-loading">Loading tasks…</div>;

  return (
    <div className="tasks-view">
      {active.length > 0 && (
        <section className="tasks-section">
          <h3 className="tasks-section-title">
            <span className="tasks-section-dot tasks-section-dot--active" />
            In Progress ({active.length})
          </h3>
          {active.map((ev) => <TaskCard key={ev.id} ev={ev} />)}
        </section>
      )}

      {failed.length > 0 && (
        <section className="tasks-section">
          <h3 className="tasks-section-title">
            <span className="tasks-section-dot tasks-section-dot--failed" />
            Failed ({failed.length})
          </h3>
          {failed.map((ev) => <TaskCard key={ev.id} ev={ev} />)}
        </section>
      )}

      {done.length > 0 && (
        <section className="tasks-section">
          <h3 className="tasks-section-title">
            <span className="tasks-section-dot tasks-section-dot--done" />
            Completed ({done.length})
          </h3>
          {done.map((ev) => <TaskCard key={ev.id} ev={ev} />)}
        </section>
      )}

      {events.length === 0 && (
        <div className="tasks-empty">
          <p>No evolution tasks yet.</p>
          <p className="tasks-empty-sub">The engine will start working shortly.</p>
        </div>
      )}
    </div>
  );
}
