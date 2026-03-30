import { useEffect, useMemo } from "react";
import { useEvolutionEvents, useEvolutionStatus } from "../../hooks/useEvolutionApi";
import { StatusBadge } from "./StatusBadge";
import {
  getActiveEvolutionEvent,
  parseExecutionHistory,
  parsePlanChanges,
  summarizeEvolutionEvent,
} from "./eventDetails";

function formatElapsed(createdAt: string, completedAt: string | null): string {
  const end = completedAt ? new Date(completedAt).getTime() : Date.now();
  const start = new Date(createdAt).getTime();
  const totalSeconds = Math.max(0, Math.round((end - start) / 1000));

  if (totalSeconds < 60) return `${totalSeconds}s`;
  const minutes = Math.floor(totalSeconds / 60);
  const seconds = totalSeconds % 60;
  return `${minutes}m ${seconds}s`;
}

function formatLayer(layer: string): string {
  return layer.replace(/_/g, " ");
}

export function RealTimeView() {
  const { status, error: statusError, refresh: refreshStatus } = useEvolutionStatus(2000);
  const { events, loading, error: eventsError, refresh: refreshEvents } = useEvolutionEvents();

  useEffect(() => {
    const timer = setInterval(() => {
      void refreshEvents();
    }, 2000);
    return () => clearInterval(timer);
  }, [refreshEvents]);

  const activeEvent = useMemo(() => getActiveEvolutionEvent(events), [events]);
  const focusEvent = activeEvent ?? status?.last_evolution ?? events[0] ?? null;
  const executionHistory = useMemo(() => parseExecutionHistory(focusEvent), [focusEvent]);
  const planChanges = useMemo(() => parsePlanChanges(focusEvent), [focusEvent]);

  const refreshAll = () => {
    void refreshStatus();
    void refreshEvents();
  };

  const panelStatus = activeEvent ? activeEvent.status : status?.active_evolutions ? "evolving" : "idle";
  const currentSummary = summarizeEvolutionEvent(focusEvent);
  const combinedError = statusError || eventsError;

  return (
    <div className="card realtime-view">
      <div className="card-header">
        <h3>Evolution Real Time</h3>
        <div className="card-header-actions">
          <span className="event-count">Auto-refresh 2s</span>
          <button className="refresh-btn" onClick={refreshAll} disabled={loading}>
            {loading ? "Loading..." : "Refresh"}
          </button>
        </div>
      </div>

      {combinedError && <div className="error-text">Error: {combinedError}</div>}

      <div className="realtime-summary-grid">
        <div className="realtime-stat">
          <span className="realtime-stat-label">Engine</span>
          <div className="realtime-stat-value">
            <StatusBadge status={panelStatus} />
          </div>
        </div>
        <div className="realtime-stat">
          <span className="realtime-stat-label">Active Evolutions</span>
          <strong className="realtime-stat-number">{status?.active_evolutions ?? 0}</strong>
        </div>
        <div className="realtime-stat">
          <span className="realtime-stat-label">Pending Inceptions</span>
          <strong className="realtime-stat-number">{status?.pending_inceptions ?? 0}</strong>
        </div>
        <div className="realtime-stat">
          <span className="realtime-stat-label">Purpose</span>
          <strong className="realtime-stat-number">
            {status?.current_purpose_version != null ? `v${status.current_purpose_version}` : "-"}
          </strong>
        </div>
      </div>

      {!focusEvent ? (
        <p className="empty-state">No evolution activity recorded yet.</p>
      ) : (
        <>
          <div className="realtime-primary-grid">
            <section className="realtime-panel">
              <div className="realtime-panel-header">
                <h4>{activeEvent ? "Current Cycle" : "Latest Cycle"}</h4>
                <span className="realtime-panel-meta">
                  {new Date(focusEvent.created_at).toLocaleString()}
                </span>
              </div>

              <p className="realtime-summary">{currentSummary}</p>

              <div className="realtime-meta-grid">
                <div className="realtime-meta-item">
                  <span className="realtime-meta-label">Source</span>
                  <span className="realtime-meta-value">{focusEvent.source}</span>
                </div>
                <div className="realtime-meta-item">
                  <span className="realtime-meta-label">Status</span>
                  <span className="realtime-meta-value">
                    <StatusBadge status={focusEvent.status} />
                  </span>
                </div>
                <div className="realtime-meta-item">
                  <span className="realtime-meta-label">Elapsed</span>
                  <span className="realtime-meta-value">
                    {formatElapsed(focusEvent.created_at, focusEvent.completed_at)}
                  </span>
                </div>
                <div className="realtime-meta-item">
                  <span className="realtime-meta-label">Request</span>
                  <code className="realtime-request-id">{focusEvent.request_id.slice(0, 8)}</code>
                </div>
              </div>

              {focusEvent.risk_level && (
                <div className="realtime-inline-row">
                  <span className="detail-label">Risk</span>
                  <span className={`risk-badge risk-${focusEvent.risk_level}`}>
                    {focusEvent.risk_level}
                  </span>
                </div>
              )}

              {focusEvent.branch && (
                <div className="realtime-inline-row">
                  <span className="detail-label">Branch</span>
                  <code>{focusEvent.branch}</code>
                </div>
              )}

              {focusEvent.error && (
                <div className="realtime-inline-row realtime-inline-row--stacked">
                  <span className="detail-label">Error</span>
                  <pre className="detail-pre error-pre">{focusEvent.error}</pre>
                </div>
              )}
            </section>

            <section className="realtime-panel">
              <div className="realtime-panel-header">
                <h4>Execution Feed</h4>
                <span className="realtime-panel-meta">
                  {executionHistory.length} step{executionHistory.length === 1 ? "" : "s"}
                </span>
              </div>

              {executionHistory.length === 0 ? (
                <p className="empty-state realtime-empty-state">
                  The engine has not posted step-by-step history for this cycle yet.
                </p>
              ) : (
                <div className="steps-list realtime-steps-list">
                  {executionHistory.map((step, index) => {
                    const stepClass =
                      step.status === "completed"
                        ? "step-ok"
                        : step.status === "failed"
                          ? "step-fail"
                          : "step-info";

                    return (
                      <div key={`${step.timestamp}-${index}`} className={`step-row ${stepClass}`}>
                        <span className="step-time">
                          {new Date(step.timestamp).toLocaleTimeString()}
                        </span>
                        <span className="step-agent">{step.agent}</span>
                        <span className="step-action">{step.action}</span>
                        <StatusBadge status={step.status} />
                        {step.details && (
                          <span className="step-details" title={step.details}>
                            {step.details}
                          </span>
                        )}
                      </div>
                    );
                  })}
                </div>
              )}
            </section>
          </div>

          <section className="realtime-panel">
            <div className="realtime-panel-header">
              <h4>Planned Changes</h4>
              <span className="realtime-panel-meta">
                {planChanges.length} item{planChanges.length === 1 ? "" : "s"}
              </span>
            </div>

            {planChanges.length === 0 ? (
              <p className="empty-state realtime-empty-state">
                No planned file changes were attached to this cycle.
              </p>
            ) : (
              <div className="realtime-change-list">
                {planChanges.map((change, index) => (
                  <div key={`${change.file_path}-${index}`} className="realtime-change-card">
                    <div className="realtime-change-topline">
                      <span className={`realtime-change-action realtime-change-action--${change.action}`}>
                        {change.action}
                      </span>
                      <span className="realtime-change-path">{change.file_path}</span>
                      <span className="realtime-change-layer">{formatLayer(change.layer)}</span>
                    </div>
                    {change.description && (
                      <p className="realtime-change-description">{change.description}</p>
                    )}
                  </div>
                ))}
              </div>
            )}
          </section>
        </>
      )}
    </div>
  );
}
