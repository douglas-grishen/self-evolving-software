import { useEvolutionStatus } from "../../hooks/useEvolutionApi";
import { EvolutionTimeline } from "./EvolutionTimeline";
import { InceptionPanel } from "./InceptionPanel";
import { PurposeViewer } from "./PurposeViewer";
import { StatusBadge } from "./StatusBadge";

function StatusBar() {
  const { status, error } = useEvolutionStatus();

  if (error) {
    return (
      <div className="status-bar status-bar-error">
        <span>Engine unreachable — {error}</span>
      </div>
    );
  }

  if (!status) {
    return (
      <div className="status-bar">
        <span>Connecting...</span>
      </div>
    );
  }

  const engineState =
    status.active_evolutions > 0 ? "evolving" : "idle";

  return (
    <div className="status-bar">
      <div className="status-bar-item">
        <StatusBadge status={engineState} />
      </div>
      <div className="status-bar-item">
        <span className="status-label">Evolutions</span>
        <span className="status-value">{status.total_evolutions}</span>
      </div>
      <div className="status-bar-item">
        <span className="status-label">Completed</span>
        <span className="status-value status-success">{status.completed_evolutions}</span>
      </div>
      <div className="status-bar-item">
        <span className="status-label">Failed</span>
        <span className="status-value status-error">{status.failed_evolutions}</span>
      </div>
      <div className="status-bar-item">
        <span className="status-label">Purpose</span>
        <span className="status-value">
          {status.current_purpose_version != null ? `v${status.current_purpose_version}` : "—"}
        </span>
      </div>
      <div className="status-bar-item">
        <span className="status-label">Pending Inceptions</span>
        <span className="status-value">{status.pending_inceptions}</span>
      </div>
    </div>
  );
}

export function EvolutionDashboard() {
  return (
    <div className="evolution-dashboard">
      <StatusBar />

      <div className="dashboard-grid">
        <div className="dashboard-main">
          <EvolutionTimeline />
        </div>
        <div className="dashboard-side">
          <PurposeViewer />
          <InceptionPanel />
        </div>
      </div>
    </div>
  );
}
