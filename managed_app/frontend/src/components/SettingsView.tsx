import { useState, useEffect } from "react";

interface Setting {
  key: string;
  value: string;
  description: string | null;
  updated_at: string;
}

function IntervalDisplay({ minutes }: { minutes: number }) {
  if (minutes < 60) return <span style={{ color: "#888", fontSize: "0.72rem" }}>{minutes} min</span>;
  const h = Math.floor(minutes / 60);
  const m = minutes % 60;
  return <span style={{ color: "#888", fontSize: "0.72rem" }}>{h}h{m > 0 ? ` ${m}m` : ""}</span>;
}

export function SettingsView() {
  const [settings, setSettings] = useState<Setting[]>([]);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState<Record<string, boolean>>({});
  const [saved, setSaved] = useState<Record<string, boolean>>({});
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [values, setValues] = useState<Record<string, string>>({});

  useEffect(() => {
    fetch("/api/v1/settings")
      .then(r => r.json())
      .then((data: Setting[]) => {
        setSettings(data);
        const v: Record<string, string> = {};
        data.forEach(s => { v[s.key] = s.value; });
        setValues(v);
      })
      .finally(() => setLoading(false));
  }, []);

  const save = async (key: string) => {
    setSaving(p => ({ ...p, [key]: true }));
    setErrors(p => ({ ...p, [key]: "" }));
    try {
      const res = await fetch(`/api/v1/settings/${key}`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ value: values[key] }),
      });
      if (!res.ok) {
        const data = await res.json();
        setErrors(p => ({ ...p, [key]: data.detail || "Failed to save" }));
      } else {
        setSaved(p => ({ ...p, [key]: true }));
        setTimeout(() => setSaved(p => ({ ...p, [key]: false })), 2500);
      }
    } catch {
      setErrors(p => ({ ...p, [key]: "Network error" }));
    } finally {
      setSaving(p => ({ ...p, [key]: false }));
    }
  };

  if (loading) return <div className="empty-state">Loading settings…</div>;

  const intervalMinutes = parseInt(values["proactive_interval_minutes"] || "60", 10);

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 20 }}>
      <p style={{ margin: 0, fontSize: "0.8rem", color: "#888", lineHeight: 1.5 }}>
        Runtime configuration for the self-evolving engine.
        Changes to the interval take effect on the next engine cycle.
      </p>

      {/* Proactive interval */}
      <div style={{ background: "rgba(255,255,255,0.03)", border: "1px solid rgba(255,255,255,0.07)", borderRadius: 10, padding: "14px 16px" }}>
        <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 8 }}>
          <div>
            <div style={{ fontSize: "0.85rem", color: "#e0e0e0", fontWeight: 500 }}>Proactive Evolution Interval</div>
            <div style={{ fontSize: "0.75rem", color: "#666", marginTop: 2 }}>How often the engine autonomously analyzes and evolves the system.</div>
          </div>
          <IntervalDisplay minutes={isNaN(intervalMinutes) ? 60 : intervalMinutes} />
        </div>
        <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
          <input
            type="range"
            min={5}
            max={240}
            step={5}
            value={isNaN(intervalMinutes) ? 60 : intervalMinutes}
            onChange={e => setValues(p => ({ ...p, proactive_interval_minutes: e.target.value }))}
            style={{ flex: 1, accentColor: "#3b82f6" }}
          />
          <input
            type="number"
            min={5}
            max={1440}
            value={values["proactive_interval_minutes"] || "60"}
            onChange={e => setValues(p => ({ ...p, proactive_interval_minutes: e.target.value }))}
            style={{ width: 60, background: "rgba(0,0,0,0.25)", border: "1px solid rgba(255,255,255,0.1)", borderRadius: 6, color: "#e0e0e0", padding: "5px 8px", fontFamily: "inherit", fontSize: "0.82rem", textAlign: "center" }}
          />
          <span style={{ fontSize: "0.78rem", color: "#666" }}>min</span>
          <button
            className="refresh-btn"
            style={{ padding: "4px 12px", background: saved["proactive_interval_minutes"] ? "rgba(34,197,94,0.15)" : undefined, color: saved["proactive_interval_minutes"] ? "#22c55e" : undefined, borderColor: saved["proactive_interval_minutes"] ? "rgba(34,197,94,0.3)" : undefined }}
            onClick={() => save("proactive_interval_minutes")}
            disabled={saving["proactive_interval_minutes"]}
          >
            {saved["proactive_interval_minutes"] ? "✓ Saved" : saving["proactive_interval_minutes"] ? "…" : "Save"}
          </button>
        </div>
        {errors["proactive_interval_minutes"] && (
          <div style={{ marginTop: 6, fontSize: "0.75rem", color: "#ef4444" }}>{errors["proactive_interval_minutes"]}</div>
        )}
      </div>

      {/* API Key */}
      <div style={{ background: "rgba(255,255,255,0.03)", border: "1px solid rgba(255,255,255,0.07)", borderRadius: 10, padding: "14px 16px" }}>
        <div style={{ marginBottom: 8 }}>
          <div style={{ fontSize: "0.85rem", color: "#e0e0e0", fontWeight: 500 }}>Anthropic API Key</div>
          <div style={{ fontSize: "0.75rem", color: "#666", marginTop: 2 }}>
            Override the ENGINE_ANTHROPIC_API_KEY env var.{" "}
            <span style={{ color: "#f59e0b" }}>Requires engine restart to apply.</span>
          </div>
        </div>
        <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
          <input
            type="password"
            placeholder="sk-ant-… (leave blank to use env var)"
            value={values["anthropic_api_key"] || ""}
            onChange={e => setValues(p => ({ ...p, anthropic_api_key: e.target.value }))}
            style={{ flex: 1, background: "rgba(0,0,0,0.25)", border: "1px solid rgba(255,255,255,0.1)", borderRadius: 6, color: "#e0e0e0", padding: "6px 10px", fontFamily: "ui-monospace, monospace", fontSize: "0.82rem" }}
          />
          <button
            className="refresh-btn"
            style={{ padding: "4px 12px", background: saved["anthropic_api_key"] ? "rgba(34,197,94,0.15)" : undefined, color: saved["anthropic_api_key"] ? "#22c55e" : undefined }}
            onClick={() => save("anthropic_api_key")}
            disabled={saving["anthropic_api_key"]}
          >
            {saved["anthropic_api_key"] ? "✓ Saved" : saving["anthropic_api_key"] ? "…" : "Save"}
          </button>
        </div>
        {errors["anthropic_api_key"] && (
          <div style={{ marginTop: 6, fontSize: "0.75rem", color: "#ef4444" }}>{errors["anthropic_api_key"]}</div>
        )}
      </div>

      <div style={{ fontSize: "0.72rem", color: "#555", textAlign: "right" }}>
        {settings.length > 0 && `Last updated: ${new Date(settings[0].updated_at).toLocaleString()}`}
      </div>
    </div>
  );
}
