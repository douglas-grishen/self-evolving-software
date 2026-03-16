import { useState } from "react";
import { useInceptions, useSubmitInception } from "../../hooks/useEvolutionApi";
import { StatusBadge } from "./StatusBadge";

function InceptionForm({ onSubmitted }: { onSubmitted: () => void }) {
  const [directive, setDirective] = useState("");
  const [rationale, setRationale] = useState("");
  const { submit, submitting, error } = useSubmitInception();

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!directive.trim()) return;

    const result = await submit(directive, rationale);
    if (result) {
      setDirective("");
      setRationale("");
      onSubmitted();
    }
  };

  return (
    <form className="inception-form" onSubmit={handleSubmit}>
      <p className="form-description">
        An Inception changes the system's Purpose — the direction of all future evolution.
      </p>

      <label>
        Directive
        <textarea
          value={directive}
          onChange={(e) => setDirective(e.target.value)}
          placeholder="Describe how the system's Purpose should change..."
          rows={3}
          required
        />
      </label>

      <label>
        Rationale
        <textarea
          value={rationale}
          onChange={(e) => setRationale(e.target.value)}
          placeholder="Why should the Purpose change? (optional)"
          rows={2}
        />
      </label>

      {error && <div className="error-text">Error: {error}</div>}

      <button type="submit" disabled={submitting || !directive.trim()}>
        {submitting ? "Submitting..." : "Submit Inception"}
      </button>
    </form>
  );
}

function InceptionList() {
  const { inceptions, loading, error, refresh } = useInceptions();

  return (
    <div className="inception-list-container">
      <div className="window-toolbar">
        <button className="refresh-btn" onClick={refresh} disabled={loading}>
          {loading ? "Loading..." : "Refresh"}
        </button>
      </div>

      {error && <div className="error-text">Error: {error}</div>}

      {inceptions.length === 0 && !loading && (
        <p className="empty-state">No inceptions submitted yet.</p>
      )}

      <div className="inception-list">
        {inceptions.map((inception) => (
          <div key={inception.id} className="inception-card">
            <div className="inception-card-header">
              <StatusBadge status={inception.status} />
              <span className="inception-source">{inception.source}</span>
              <span className="inception-date">
                {new Date(inception.submitted_at).toLocaleString()}
              </span>
            </div>

            <p className="inception-directive">{inception.directive}</p>

            {inception.rationale && (
              <p className="inception-rationale">{inception.rationale}</p>
            )}

            {inception.changes_summary && (
              <div className="inception-changes">
                <span className="detail-label">Changes:</span>
                <span>{inception.changes_summary}</span>
              </div>
            )}

            {inception.new_purpose_version && (
              <div className="inception-versions">
                v{inception.previous_purpose_version} → v{inception.new_purpose_version}
              </div>
            )}
          </div>
        ))}
      </div>
    </div>
  );
}

interface InceptionPanelProps {
  mode?: "full" | "form-only" | "list-only";
}

export function InceptionPanel({ mode = "full" }: InceptionPanelProps) {
  const { refresh } = useInceptions();

  if (mode === "form-only") {
    return <InceptionForm onSubmitted={refresh} />;
  }

  if (mode === "list-only") {
    return <InceptionList />;
  }

  return (
    <div>
      <InceptionForm onSubmitted={refresh} />
      <InceptionList />
    </div>
  );
}
