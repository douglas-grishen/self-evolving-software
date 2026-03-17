import { useEffect, useState } from "react";
import { useEvolutionEvents, EvolutionEvent } from "../../hooks/useEvolutionApi";
import { StatusBadge } from "./StatusBadge";

interface EventLogEntry {
  timestamp: string;
  agent: string;
  action: string;
  status: string;
  details?: string;
}

function ExecutionSteps({ events }: { events: EventLogEntry[] }) {
  if (!events || events.length === 0) return null;
  return (
    <div className="execution-steps">
      <span className="detail-label">Execution Steps</span>
      <div className="steps-list">
        {events.map((step, i) => {
          const time = new Date(step.timestamp);
          const statusClass =
            step.status === "completed" ? "step-ok" :
            step.status === "failed" ? "step-fail" : "step-info";
          return (
            <div key={i} className={`step-row ${statusClass}`}>
              <span className="step-time">{time.toLocaleTimeString()}</span>
              <span className="step-agent">{step.agent}</span>
              <span className="step-action">{step.action}</span>
              <StatusBadge status={step.status} />
              {step.details && <span className="step-details">{step.details}</span>}
            </div>
          );
        })}
      </div>
    </div>
  );
}

function TimelineCard({ event }: { event: EvolutionEvent }) {
  const [expanded, setExpanded] = useState(false);
  const date = new Date(event.created_at);
  const isProactive = event.user_request?.startsWith("[Proactive");
  const duration = event.completed_at
    ? Math.round((new Date(event.completed_at).getTime() - date.getTime()) / 1000)
    : null;

  // Parse events_json — it can be an array of step objects
  const executionEvents: EventLogEntry[] = Array.isArray(event.events_json)
    ? (event.events_json as unknown as EventLogEntry[])
    : [];

  return (
    <div className={`timeline-card ${isProactive ? "timeline-proactive" : ""}`} onClick={() => setExpanded(!expanded)}>
      <div className="timeline-card-header">
        <div className="timeline-card-meta">
          <StatusBadge status={event.status} />
          <span className="timeline-source">{isProactive ? "proactive" : event.source}</span>
          {event.risk_level && (
            <span className={`risk-badge risk-${event.risk_level}`}>
              {event.risk_level}
            </span>
          )}
          {duration !== null && (
            <span className="timeline-duration">{duration}s</span>
          )}
        </div>
        <span className="timeline-date">
          {date.toLocaleDateString()} {date.toLocaleTimeString()}
        </span>
      </div>

      {/* Plan summary shown prominently if available */}
      {event.plan_summary && (
        <div className="timeline-plan-summary">
          <span className="plan-label">Plan:</span> {event.plan_summary}
        </div>
      )}

      <p className="timeline-request">
        {event.user_request?.replace("[Proactive \u2014 Purpose-driven] ", "").slice(0, 200)}
        {(event.user_request?.length ?? 0) > 200 ? "..." : ""}
      </p>

      {expanded && (
        <div className="timeline-detail">
          <div className="detail-row">
            <span className="detail-label">Request ID</span>
            <code>{event.request_id.slice(0, 8)}</code>
          </div>

          {event.user_request && (
            <div className="detail-row detail-full-request">
              <span className="detail-label">Full Request</span>
              <pre className="detail-pre">{event.user_request}</pre>
            </div>
          )}

          {event.validation_passed !== null && (
            <div className="detail-row">
              <span className="detail-label">Validation</span>
              <span className={event.validation_passed ? "success-text" : "error-text"}>
                {event.validation_passed ? "Passed" : "Failed"}
              </span>
            </div>
          )}

          {event.deployment_success !== null && (
            <div className="detail-row">
              <span className="detail-label">Deployment</span>
              <span className={event.deployment_success ? "success-text" : "error-text"}>
                {event.deployment_success ? "Success" : "Failed"}
              </span>
            </div>
          )}

          {event.commit_sha && (
            <div className="detail-row">
              <span className="detail-label">Commit</span>
              <code>{event.commit_sha.slice(0, 8)}</code>
            </div>
          )}

          {event.branch && (
            <div className="detail-row">
              <span className="detail-label">Branch</span>
              <code>{event.branch}</code>
            </div>
          )}

          {event.error && (
            <div className="detail-row error-text">
              <span className="detail-label">Error</span>
              <pre className="detail-pre error-pre">{event.error}</pre>
            </div>
          )}

          <ExecutionSteps events={executionEvents} />
        </div>
      )}
    </div>
  );
}

export function EvolutionTimeline() {
  const { events, loading, error, refresh } = useEvolutionEvents();

  // Auto-refresh every 15 seconds to catch new evolution events
  useEffect(() => {
    const timer = setInterval(refresh, 15000);
    return () => clearInterval(timer);
  }, [refresh]);

  return (
    <div className="card evolution-timeline">
      <div className="card-header">
        <h3>Evolution Timeline</h3>
        <div className="card-header-actions">
          <span className="event-count">{events.length} events</span>
          <button className="refresh-btn" onClick={refresh} disabled={loading}>
            {loading ? "Loading..." : "Refresh"}
          </button>
        </div>
      </div>

      {error && <div className="error-text">Error: {error}</div>}

      {events.length === 0 && !loading && (
        <p className="empty-state">No evolution events yet. Define a Purpose and click "Run Analysis" to start.</p>
      )}

      <div className="timeline-list">
        {events.map((event) => (
          <TimelineCard key={event.id} event={event} />
        ))}
      </div>
    </div>
  );
}
