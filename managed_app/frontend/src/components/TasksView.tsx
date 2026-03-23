import { useCallback, useEffect, useState } from "react";
import "./TasksView.css";

interface FileChange {
  file_path: string;
  action: "create" | "modify" | "delete";
  description: string;
  layer: string;
}

interface EventsJson {
  plan_changes?: FileChange[];
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

interface BacklogTask {
  id: string;
  task_key: string;
  title: string;
  description: string;
  status: "pending" | "in_progress" | "blocked" | "done" | "abandoned";
  priority: "high" | "normal" | "low";
  sequence: number;
  execution_request: string;
  acceptance_criteria: string[];
  depends_on: string[];
  blocked_reason: string | null;
  last_request_id: string | null;
  attempt_count: number;
  failure_streak: number;
  last_error: string | null;
  created_at: string;
  updated_at: string;
  retry_after: string | null;
  last_attempted_at: string | null;
  started_at: string | null;
  completed_at: string | null;
}

const ACTION_ICON: Record<string, string> = { create: "✨", modify: "✏️", delete: "🗑️" };
const ACTION_LABEL: Record<string, string> = { create: "created", modify: "modified", delete: "deleted" };
const LAYER_COLOR: Record<string, string> = {
  backend: "#818cf8",
  frontend: "#34d399",
  database: "#fb923c",
  config: "#94a3b8",
};

const EVENT_STATUS_ICON: Record<string, string> = {
  completed: "✅",
  failed: "❌",
};

const BACKLOG_STATUS_ICON: Record<BacklogTask["status"], string> = {
  pending: "🟡",
  in_progress: "⚙️",
  blocked: "⛔",
  done: "✅",
  abandoned: "🧊",
};

const RISK_COLOR: Record<string, string> = {
  low: "#22c55e",
  medium: "#f59e0b",
  high: "#ef4444",
};

const PRIORITY_COLOR: Record<BacklogTask["priority"], string> = {
  high: "#fca5a5",
  normal: "#fde68a",
  low: "#93c5fd",
};

async function fetchJson<T>(url: string): Promise<T> {
  const res = await fetch(url);
  if (!res.ok) {
    throw new Error(`HTTP ${res.status}`);
  }
  return await res.json() as T;
}

function useTaskData(intervalMs = 5000) {
  const [backlog, setBacklog] = useState<BacklogTask[]>([]);
  const [events, setEvents] = useState<EvolutionEvent[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    const [backlogResult, eventsResult] = await Promise.allSettled([
      fetchJson<BacklogTask[]>("/api/v1/evolution/backlog"),
      fetchJson<EvolutionEvent[]>("/api/v1/evolution/events?limit=30"),
    ]);

    let nextError: string | null = null;

    if (backlogResult.status === "fulfilled") {
      setBacklog(backlogResult.value);
    } else {
      nextError = backlogResult.reason instanceof Error ? backlogResult.reason.message : "Failed to load backlog";
    }

    if (eventsResult.status === "fulfilled") {
      setEvents(eventsResult.value);
    } else if (!nextError) {
      nextError = eventsResult.reason instanceof Error ? eventsResult.reason.message : "Failed to load history";
    }

    setError(nextError);
    setLoading(false);
  }, []);

  useEffect(() => {
    refresh();
    const t = setInterval(refresh, intervalMs);
    return () => clearInterval(t);
  }, [refresh, intervalMs]);

  return { backlog, events, loading, error };
}

function clip(text: string, limit = 180): string {
  if (text.length <= limit) return text;
  return `${text.slice(0, limit - 1).trimEnd()}…`;
}

function timeAgo(iso: string | null): string {
  if (!iso) return "";
  const diff = Date.now() - new Date(iso).getTime();
  const m = Math.floor(diff / 60000);
  if (m < 1) return "just now";
  if (m < 60) return `${m}m ago`;
  const h = Math.floor(m / 60);
  if (h < 24) return `${h}h ago`;
  return `${Math.floor(h / 24)}d ago`;
}

function duration(ev: EvolutionEvent): string {
  if (!ev.completed_at) return "";
  const ms = new Date(ev.completed_at).getTime() - new Date(ev.created_at).getTime();
  const s = Math.round(ms / 1000);
  if (s < 60) return `${s}s`;
  return `${Math.floor(s / 60)}m ${s % 60}s`;
}

function taskTitle(ev: EvolutionEvent): string {
  if (ev.plan_summary) return ev.plan_summary;
  const raw = ev.user_request.replace(/^\[Proactive[^\]]*\]\s*/i, "").trim();
  const cut = raw.match(/^[^.!?\n]{10,120}[.!?]/);
  return cut ? cut[0] : raw.slice(0, 120) + (raw.length > 120 ? "…" : "");
}

function backlogTimestamp(task: BacklogTask): string {
  return task.started_at || task.updated_at || task.created_at;
}

function sortBacklog(tasks: BacklogTask[]): BacklogTask[] {
  const priorityRank: Record<BacklogTask["priority"], number> = {
    high: 0,
    normal: 1,
    low: 2,
  };

  return [...tasks].sort((a, b) => {
    const priorityDelta = priorityRank[a.priority] - priorityRank[b.priority];
    if (priorityDelta !== 0) return priorityDelta;
    if (a.sequence !== b.sequence) return a.sequence - b.sequence;
    return new Date(backlogTimestamp(b)).getTime() - new Date(backlogTimestamp(a)).getTime();
  });
}

function retryLabel(retryAfter: string | null): string | null {
  if (!retryAfter) return null;
  const diffMs = new Date(retryAfter).getTime() - Date.now();
  if (diffMs <= 0) return "ready to retry";
  const mins = Math.ceil(diffMs / 60000);
  return mins <= 1 ? "retry in <1m" : `retry in ${mins}m`;
}

function BacklogCard({ task }: { task: BacklogTask }) {
  const [expanded, setExpanded] = useState(task.status !== "pending");
  const summary = task.description || task.execution_request;
  const retryStatus = retryLabel(task.retry_after);

  return (
    <div
      className={`task-card task-card--${task.status}`}
      onClick={() => setExpanded((current) => !current)}
    >
      <div className="task-card-header">
        <span className="task-status-icon">{BACKLOG_STATUS_ICON[task.status]}</span>
        <div className="task-backlog-copy">
          <div className="task-title">{task.title}</div>
          {summary && <div className="task-card-subtitle">{clip(summary, 220)}</div>}
        </div>
        <div className="task-meta">
          <span className="task-badge" style={{ color: PRIORITY_COLOR[task.priority] }}>
            {task.priority}
          </span>
          <span className="task-badge">#{task.sequence}</span>
          {task.attempt_count > 0 && <span className="task-badge">{task.attempt_count} attempt{task.attempt_count !== 1 ? "s" : ""}</span>}
          {retryStatus && <span className="task-badge">{retryStatus}</span>}
          <span className="task-source">⚡ backlog</span>
          <span className="task-time">{timeAgo(backlogTimestamp(task))}</span>
          <span className="task-chevron">{expanded ? "▲" : "▼"}</span>
        </div>
      </div>

      {task.status === "in_progress" && (
        <div className="task-progress-bar">
          <div className="task-progress-bar-inner" />
        </div>
      )}

      {expanded && (
        <div className="task-card-body">
          {task.blocked_reason && <div className="task-error">⚠️ {task.blocked_reason}</div>}
          {!task.blocked_reason && task.last_error && <div className="task-error">⚠️ {clip(task.last_error, 500)}</div>}

          {task.execution_request && (
            <div className="task-request">
              <strong>Execution request</strong>
              <p>{clip(task.execution_request, 520)}</p>
            </div>
          )}

          {task.acceptance_criteria.length > 0 && (
            <div className="task-criteria">
              <strong>Acceptance criteria</strong>
              <ul className="task-criteria-list">
                {task.acceptance_criteria.map((criterion) => (
                  <li key={criterion}>{criterion}</li>
                ))}
              </ul>
            </div>
          )}

          <div className="task-badges">
            {task.depends_on.length > 0 && (
              <span className="task-badge">depends on {task.depends_on.join(", ")}</span>
            )}
            {task.failure_streak > 0 && (
              <span className="task-badge">failure streak {task.failure_streak}</span>
            )}
            {task.retry_after && (
              <span className="task-badge">retry at {new Date(task.retry_after).toLocaleTimeString()}</span>
            )}
            {task.last_request_id && <span className="task-badge">request {task.last_request_id.slice(0, 8)}</span>}
          </div>
        </div>
      )}
    </div>
  );
}

function EventCard({ ev }: { ev: EvolutionEvent }) {
  const [expanded, setExpanded] = useState(false);
  const changes = ev.events_json?.plan_changes ?? [];

  return (
    <div className={`task-card task-card--${ev.status}`} onClick={() => setExpanded((x) => !x)}>
      <div className="task-card-header">
        <span className="task-status-icon">{EVENT_STATUS_ICON[ev.status] ?? "🔄"}</span>
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

      {expanded && (
        <div className="task-card-body">
          {ev.error && <div className="task-error">⚠️ {ev.error}</div>}

          {changes.length > 0 ? (
            <div className="task-files">
              <div className="task-files-title">Files</div>
              <ul className="task-files-list">
                {changes.map((fc, i) => (
                  <li key={`${fc.file_path}-${i}`} className="task-file-item">
                    <span className="task-file-action">{ACTION_ICON[fc.action]}</span>
                    <span className="task-file-layer" style={{ color: LAYER_COLOR[fc.layer] ?? "#94a3b8" }}>
                      {fc.layer}
                    </span>
                    <span className="task-file-path">{fc.file_path}</span>
                    <span className="task-file-verb">{ACTION_LABEL[fc.action]}</span>
                  </li>
                ))}
              </ul>
            </div>
          ) : (
            <div className="task-request">
              <strong>Execution summary</strong>
              <p>{clip(ev.user_request.replace(/^\[Proactive[^\]]*\]\s*/i, "").trim(), 520)}</p>
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

export function TasksView() {
  const { backlog, events, loading, error } = useTaskData(5000);

  const inProgress = sortBacklog(backlog.filter((task) => task.status === "in_progress"));
  const pending = sortBacklog(backlog.filter((task) => task.status === "pending"));
  const blocked = sortBacklog(backlog.filter((task) => task.status === "blocked"));
  const failed = events.filter((event) => event.status === "failed");
  const completed = events.filter((event) => event.status === "completed");

  if (loading) return <div className="tasks-loading">Loading tasks…</div>;

  return (
    <div className="tasks-view">
      {error && <div className="task-error">⚠️ {error}</div>}

      {inProgress.length > 0 && (
        <section className="tasks-section">
          <h3 className="tasks-section-title">
            <span className="tasks-section-dot tasks-section-dot--active" />
            In Progress ({inProgress.length})
          </h3>
          {inProgress.map((task) => <BacklogCard key={task.id} task={task} />)}
        </section>
      )}

      {pending.length > 0 && (
        <section className="tasks-section">
          <h3 className="tasks-section-title">
            <span className="tasks-section-dot tasks-section-dot--pending" />
            Pending Backlog ({pending.length})
          </h3>
          {pending.map((task) => <BacklogCard key={task.id} task={task} />)}
        </section>
      )}

      {blocked.length > 0 && (
        <section className="tasks-section">
          <h3 className="tasks-section-title">
            <span className="tasks-section-dot tasks-section-dot--blocked" />
            Blocked ({blocked.length})
          </h3>
          {blocked.map((task) => <BacklogCard key={task.id} task={task} />)}
        </section>
      )}

      {failed.length > 0 && (
        <section className="tasks-section">
          <h3 className="tasks-section-title">
            <span className="tasks-section-dot tasks-section-dot--failed" />
            Recent Failed Runs ({failed.length})
          </h3>
          {failed.map((ev) => <EventCard key={ev.id} ev={ev} />)}
        </section>
      )}

      {completed.length > 0 && (
        <section className="tasks-section">
          <h3 className="tasks-section-title">
            <span className="tasks-section-dot tasks-section-dot--done" />
            Recent Completed Runs ({completed.length})
          </h3>
          {completed.map((ev) => <EventCard key={ev.id} ev={ev} />)}
        </section>
      )}

      {backlog.length === 0 && events.length === 0 && (
        <div className="tasks-empty">
          <p>No evolution tasks yet.</p>
          <p className="tasks-empty-sub">The engine will start working shortly.</p>
        </div>
      )}

      {backlog.length === 0 && events.length > 0 && (
        <div className="tasks-empty">
          <p>No pending backlog items.</p>
          <p className="tasks-empty-sub">The engine is idle until the next proactive slice or a manual trigger.</p>
        </div>
      )}
    </div>
  );
}
