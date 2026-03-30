import { useState } from "react";
import { useInceptions, useSubmitInception } from "../../hooks/useEvolutionApi";
import { StatusBadge } from "./StatusBadge";

function InceptionForm({
  onSubmitted,
  onCancel,
}: {
  onSubmitted: () => void;
  onCancel?: () => void;
}) {
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

      <div className="inception-form-actions">
        {onCancel && (
          <button
            type="button"
            className="inception-cancel-btn"
            onClick={onCancel}
            disabled={submitting}
          >
            Cancel
          </button>
        )}
        <button type="submit" disabled={submitting || !directive.trim()}>
          {submitting ? "Submitting..." : "Submit Inception"}
        </button>
      </div>
    </form>
  );
}

function InceptionList({
  inceptions,
  loading,
  error,
  refresh,
  allowComposerToggle,
  showComposer,
  onToggleComposer,
  onSubmitted,
}: {
  inceptions: ReturnType<typeof useInceptions>["inceptions"];
  loading: boolean;
  error: string | null;
  refresh: () => Promise<void>;
  allowComposerToggle: boolean;
  showComposer: boolean;
  onToggleComposer: () => void;
  onSubmitted: () => void;
}) {
  const handleSubmitted = () => {
    onSubmitted();
  };

  return (
    <div className="inception-list-container">
      <div className="window-toolbar inception-toolbar">
        {allowComposerToggle && (
          <button className="inception-new-btn" onClick={onToggleComposer}>
            {showComposer ? "Hide New Inception" : "New Inception"}
          </button>
        )}
        <button className="refresh-btn" onClick={refresh} disabled={loading}>
          {loading ? "Loading..." : "Refresh"}
        </button>
      </div>

      {error && <div className="error-text">Error: {error}</div>}

      {showComposer && (
        <div className="inception-compose-card">
          <InceptionForm onSubmitted={handleSubmitted} onCancel={onToggleComposer} />
        </div>
      )}

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
  const { inceptions, loading, error, refresh } = useInceptions();
  const [showComposer, setShowComposer] = useState(mode === "full");

  const handleSubmitted = () => {
    void refresh();
    if (mode !== "full") {
      setShowComposer(false);
    }
  };

  if (mode === "form-only") {
    return <InceptionForm onSubmitted={refresh} />;
  }

  if (mode === "list-only") {
    return (
      <InceptionList
        inceptions={inceptions}
        loading={loading}
        error={error}
        refresh={refresh}
        allowComposerToggle={true}
        showComposer={showComposer}
        onToggleComposer={() => setShowComposer((prev) => !prev)}
        onSubmitted={handleSubmitted}
      />
    );
  }

  return (
    <div>
      <InceptionForm onSubmitted={refresh} />
      <InceptionList
        inceptions={inceptions}
        loading={loading}
        error={error}
        refresh={refresh}
        allowComposerToggle={false}
        showComposer={false}
        onToggleComposer={() => {}}
        onSubmitted={handleSubmitted}
      />
    </div>
  );
}
