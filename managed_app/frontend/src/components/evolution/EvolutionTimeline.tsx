import { useState } from "react";
import { useEvolutionEvents, EvolutionEvent } from "../../hooks/useEvolutionApi";
import { StatusBadge } from "./StatusBadge";

function TimelineCard({ event }: { event: EvolutionEvent }) {
  const [expanded, setExpanded] = useState(false);
  const date = new Date(event.created_at);

  return (
    <div className="timeline-card" onClick={() => setExpanded(!expanded)}>
      <div className="timeline-card-header">
        <div className="timeline-card-meta">
          <StatusBadge status={event.status} />
          <span className="timeline-source">{event.source}</span>
          {event.risk_level && (
            <span className={`risk-badge risk-${event.risk_level}`}>
              {event.risk_level}
            </span>
          )}
        </div>
        <span className="timeline-date">
          {date.toLocaleDateString()} {date.toLocaleTimeString()}
        </span>
      </div>

      <p className="timeline-request">
        {event.plan_summary || event.user_request.slice(0, 120)}
        {event.user_request.length > 120 && !event.plan_summary ? "..." : ""}
      </p>

      {expanded && (
        <div className="timeline-detail">
          <div className="detail-row">
            <span className="detail-label">Request ID</span>
            <code>{event.request_id.slice(0, 8)}</code>
          </div>

          {event.user_request && (
            <div className="detail-row">
              <span className="detail-label">Full Request</span>
              <span>{event.user_request}</span>
            </div>
          )}

          {event.validation_passed !== null && (
            <div className="detail-row">
              <span className="detail-label">Validation</span>
              <span>{event.validation_passed ? "Passed" : "Failed"}</span>
            </div>
          )}

          {event.deployment_success !== null && (
            <div className="detail-row">
              <span className="detail-label">Deployment</span>
              <span>{event.deployment_success ? "Success" : "Failed"}</span>
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
              <span>{event.error}</span>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

export function EvolutionTimeline() {
  const { events, loading, error, refresh } = useEvolutionEvents();

  return (
    <div className="card evolution-timeline">
      <div className="card-header">
        <h3>Evolution Timeline</h3>
        <button className="refresh-btn" onClick={refresh} disabled={loading}>
          {loading ? "Loading..." : "Refresh"}
        </button>
      </div>

      {error && <div className="error-text">Error: {error}</div>}

      {events.length === 0 && !loading && (
        <p className="empty-state">No evolution events yet.</p>
      )}

      <div className="timeline-list">
        {events.map((event) => (
          <TimelineCard key={event.id} event={event} />
        ))}
      </div>
    </div>
  );
}
