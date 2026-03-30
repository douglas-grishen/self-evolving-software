import { useState, useEffect } from "react";
import { useApps } from "../hooks/useAppsApi";

const EVOLUTION_NODES = [
  { id: "engine", label: "Evolution Engine", icon: "🔁", color: "#3b82f6", desc: "MAPE-K loop · Claude LLM" },
];

const OPERATIONAL_NODES = [
  { id: "frontend", label: "Frontend", icon: "⚡", color: "#8b5cf6", desc: "React 19 · Vite" },
  { id: "backend", label: "Backend API", icon: "🌐", color: "#22c55e", desc: "FastAPI · SQLAlchemy" },
  { id: "database", label: "Database", icon: "🗄️", color: "#f59e0b", desc: "PostgreSQL 16" },
];

const PLANE_SECTIONS = [
  {
    id: "evolution-plane",
    title: "Evolution Plane",
    hint: "Observes runtime behavior and ships validated changes",
    nodes: EVOLUTION_NODES,
  },
  {
    id: "operational-plane",
    title: "Operational Plane",
    hint: "Serves users, APIs, and persistent business data",
    nodes: OPERATIONAL_NODES,
  },
] as const;

const STATUS_COLORS: Record<string, string> = {
  planned: "#94a3b8",
  building: "#3b82f6",
  active: "#22c55e",
  archived: "#6b7280",
};

// Helper to convert hex color to rgb for rgba()
function hexToRgb(hex: string): string {
  const r = parseInt(hex.slice(1, 3), 16);
  const g = parseInt(hex.slice(3, 5), 16);
  const b = parseInt(hex.slice(5, 7), 16);
  return `${r}, ${g}, ${b}`;
}

export function ArchitectureView() {
  const { apps, loading, refresh } = useApps(15_000);
  const [lastUpdated, setLastUpdated] = useState<Date>(new Date());

  // Update time on refresh
  useEffect(() => { setLastUpdated(new Date()); }, [apps]);

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 20, height: "100%" }}>
      {/* Header */}
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
        <h3 style={{ margin: 0, fontSize: "0.9rem", color: "#e0e0e0", fontWeight: 500 }}>
          Operational / Evolution Planes
        </h3>
        <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
          <span style={{ fontSize: "0.72rem", color: "#555" }}>
            Updated {lastUpdated.toLocaleTimeString()}
          </span>
          <button className="refresh-btn" onClick={refresh}>↻ Refresh</button>
        </div>
      </div>

      {PLANE_SECTIONS.map((section) => (
        <div key={section.id}>
          <div style={{ display: "flex", alignItems: "baseline", justifyContent: "space-between", gap: 12, marginBottom: 10 }}>
            <div style={{ fontSize: "0.7rem", color: "#666", textTransform: "uppercase", letterSpacing: "0.05em", fontWeight: 500 }}>
              {section.title}
            </div>
            <div style={{ fontSize: "0.68rem", color: "#555", textAlign: "right" }}>
              {section.hint}
            </div>
          </div>
          <div style={{ display: "flex", alignItems: "center", gap: 0 }}>
            {section.nodes.map((node, i) => (
              <div key={node.id} style={{ display: "flex", alignItems: "center", flex: 1, minWidth: 0 }}>
                <div style={{
                  flex: 1,
                  background: `rgba(${hexToRgb(node.color)}, 0.08)`,
                  border: `1px solid rgba(${hexToRgb(node.color)}, 0.3)`,
                  borderRadius: 10,
                  padding: "10px 12px",
                  textAlign: "center",
                  minWidth: 0,
                }}>
                  <div style={{ fontSize: "1.3rem", lineHeight: 1, marginBottom: 4 }}>{node.icon}</div>
                  <div style={{ fontSize: "0.78rem", color: "#e0e0e0", fontWeight: 500, marginBottom: 2 }}>{node.label}</div>
                  <div style={{ fontSize: "0.65rem", color: "#777", whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>{node.desc}</div>
                </div>
                {i < section.nodes.length - 1 && (
                  <div style={{ color: "#444", fontSize: "0.9rem", padding: "0 4px", flexShrink: 0 }}>→</div>
                )}
              </div>
            ))}
          </div>
        </div>
      ))}

      {/* Apps Layer */}
      <div style={{ flex: 1, overflow: "auto" }}>
        <div style={{ fontSize: "0.7rem", color: "#666", textTransform: "uppercase", letterSpacing: "0.05em", marginBottom: 10, fontWeight: 500, display: "flex", alignItems: "center", gap: 8 }}>
          Operational Apps
          <span style={{ background: "rgba(255,255,255,0.08)", borderRadius: 999, padding: "0 7px", fontSize: "0.65rem", color: "#888", fontWeight: 400, lineHeight: "18px" }}>
            {apps.length}
          </span>
        </div>
        {loading && apps.length === 0 ? (
          <div className="empty-state">Loading…</div>
        ) : apps.length === 0 ? (
          <div className="empty-state">No apps yet — the evolution plane will create them autonomously.</div>
        ) : (
          <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(200px, 1fr))", gap: 10 }}>
            {apps.map((app) => (
              <div key={app.id} style={{
                background: "rgba(255,255,255,0.03)",
                border: "1px solid rgba(255,255,255,0.08)",
                borderRadius: 10,
                padding: "12px 14px",
                borderLeft: `3px solid ${STATUS_COLORS[app.status] || "#666"}`,
              }}>
                {/* App header */}
                <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 8 }}>
                  <span style={{ fontSize: "1.2rem", lineHeight: 1 }}>{app.icon || "📦"}</span>
                  <div style={{ flex: 1, minWidth: 0 }}>
                    <div style={{ fontSize: "0.82rem", color: "#e0e0e0", fontWeight: 500, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{app.name}</div>
                    <div style={{ fontSize: "0.68rem", color: STATUS_COLORS[app.status] || "#666", textTransform: "capitalize" }}>{app.status}</div>
                  </div>
                </div>
                {/* Counts */}
                <div style={{ display: "flex", gap: 8 }}>
                  <span style={{ fontSize: "0.68rem", color: "#666", background: "rgba(255,255,255,0.04)", borderRadius: 4, padding: "2px 6px" }}>
                    {app.feature_count} features
                  </span>
                  <span style={{ fontSize: "0.68rem", color: "#666", background: "rgba(255,255,255,0.04)", borderRadius: 4, padding: "2px 6px" }}>
                    {app.capability_count} capabilities
                  </span>
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
