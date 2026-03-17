import { useApp, Feature, CapabilityBrief } from "../hooks/useAppsApi";
import { StatusBadge } from "./evolution/StatusBadge";

function CapabilityTag({ cap }: { cap: CapabilityBrief }) {
  return (
    <span className={`cap-tag cap-${cap.status}`}>
      {cap.is_background && <span className="cap-bg-icon" title="Background capability">{"\u2699\ufe0f"}</span>}
      {cap.name}
    </span>
  );
}

function FeatureCard({ feature }: { feature: Feature }) {
  return (
    <div className="feature-card">
      <div className="feature-card-header">
        <span className="feature-name">{feature.name}</span>
        <StatusBadge status={feature.status} />
      </div>
      {feature.user_facing_description && (
        <p className="feature-user-desc">{feature.user_facing_description}</p>
      )}
      {feature.description && feature.description !== feature.user_facing_description && (
        <p className="feature-tech-desc">{feature.description}</p>
      )}
      {feature.capabilities.length > 0 && (
        <div className="feature-caps">
          <span className="feature-caps-label">Requires:</span>
          {feature.capabilities.map((cap) => (
            <CapabilityTag key={cap.id} cap={cap} />
          ))}
        </div>
      )}
    </div>
  );
}

interface AppViewerProps {
  appId: string;
}

export function AppViewer({ appId }: AppViewerProps) {
  const { app, loading, error } = useApp(appId);

  if (loading) return <div className="card">Loading app...</div>;
  if (error) return <div className="card error-text">Error: {error}</div>;
  if (!app) return <div className="card">App not found.</div>;

  const standaloneCaps = app.capabilities.filter(
    (cap) => !app.features.some((f) => f.capabilities.some((fc) => fc.id === cap.id))
  );

  return (
    <div className="card app-viewer">
      <div className="app-viewer-header">
        <span className="app-viewer-icon">{app.icon || "\u{1f4e6}"}</span>
        <div className="app-viewer-info">
          <h3>{app.name}</h3>
          <StatusBadge status={app.status} />
        </div>
      </div>

      {app.goal && (
        <div className="app-goal">
          <span className="app-goal-label">Goal:</span> {app.goal}
        </div>
      )}

      {app.description && (
        <p className="app-description">{app.description}</p>
      )}

      {/* Features */}
      <div className="app-section">
        <h4>Features ({app.features.length})</h4>
        {app.features.length === 0 ? (
          <p className="empty-state">No features defined yet.</p>
        ) : (
          <div className="features-list">
            {app.features.map((f) => (
              <FeatureCard key={f.id} feature={f} />
            ))}
          </div>
        )}
      </div>

      {/* Standalone Capabilities */}
      {standaloneCaps.length > 0 && (
        <div className="app-section">
          <h4>Standalone Capabilities ({standaloneCaps.length})</h4>
          <div className="caps-list">
            {standaloneCaps.map((cap) => (
              <div key={cap.id} className="cap-card">
                <CapabilityTag cap={cap} />
              </div>
            ))}
          </div>
        </div>
      )}

      <div className="app-meta">
        Created: {new Date(app.created_at).toLocaleString()}
        {app.created_by_evolution_id && (
          <> &middot; Created by evolution <code>{app.created_by_evolution_id.slice(0, 8)}</code></>
        )}
      </div>
    </div>
  );
}
