import { useEffect, useState } from "react";

interface Setting {
  key: string;
  value: string;
  description: string | null;
  updated_at: string;
}

type ProviderKey = "anthropic" | "bedrock" | "openai";
type RuntimeScope = "chat" | "engine";

const SECRET_KEYS = new Set(["anthropic_api_key", "openai_api_key"]);
const RUNTIME_KEYS: Record<RuntimeScope, { provider: string; model: string }> = {
  chat: {
    provider: "chat_llm_provider",
    model: "chat_llm_model",
  },
  engine: {
    provider: "engine_llm_provider",
    model: "engine_llm_model",
  },
};

function defaultModelForProvider(provider: ProviderKey): string {
  switch (provider) {
    case "bedrock":
      return "global.anthropic.claude-sonnet-4-20250514-v1:0";
    case "openai":
      return "gpt-5.2";
    default:
      return "claude-sonnet-4-20250514";
  }
}

function normalizeProvider(value: string | undefined): ProviderKey {
  if (value === "bedrock" || value === "openai") return value;
  return "anthropic";
}

function getRuntimeProvider(values: Record<string, string>, scope: RuntimeScope): ProviderKey {
  return normalizeProvider(values[RUNTIME_KEYS[scope].provider] || values.llm_provider);
}

function getRuntimeModel(
  values: Record<string, string>,
  scope: RuntimeScope,
  provider: ProviderKey,
): string {
  const scoped = (values[RUNTIME_KEYS[scope].model] || "").trim();
  if (scoped) return scoped;

  const legacy = (values.llm_model || "").trim();
  if (legacy) return legacy;

  return defaultModelForProvider(provider);
}

function runtimeTitle(scope: RuntimeScope): string {
  return scope === "chat" ? "Chat Runtime" : "Self-Evolution Runtime";
}

function runtimeDescription(scope: RuntimeScope): string {
  return scope === "chat"
    ? "Used by the Chat app immediately."
    : "Used by the autonomous engine on the next control-loop cycle.";
}

function runtimeProviderHelp(scope: RuntimeScope, provider: ProviderKey): string {
  if (provider === "bedrock") {
    return "Bedrock uses the instance IAM role. The model field expects a Bedrock model ID or inference profile.";
  }
  if (provider === "openai") {
    return scope === "chat"
      ? "Chat requests will use OpenAI with the shared key below when present; otherwise ENGINE_OPENAI_API_KEY."
      : "The engine will use OpenAI with the shared key below when present; otherwise ENGINE_OPENAI_API_KEY.";
  }
  return scope === "chat"
    ? "Chat requests will use Anthropic with the shared key below when present; otherwise ENGINE_ANTHROPIC_API_KEY."
    : "The engine will use Anthropic with the shared key below when present; otherwise ENGINE_ANTHROPIC_API_KEY.";
}

function IntervalDisplay({ minutes }: { minutes: number }) {
  if (minutes < 60) return <span style={{ color: "#888", fontSize: "0.72rem" }}>{minutes} min</span>;
  const h = Math.floor(minutes / 60);
  const m = minutes % 60;
  return <span style={{ color: "#888", fontSize: "0.72rem" }}>{h}h{m > 0 ? ` ${m}m` : ""}</span>;
}

function statusStyles(active: boolean) {
  return active
    ? {
        background: "rgba(34,197,94,0.15)",
        color: "#22c55e",
        borderColor: "rgba(34,197,94,0.3)",
      }
    : {};
}

export function SettingsView() {
  const [settings, setSettings] = useState<Setting[]>([]);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState<Record<string, boolean>>({});
  const [saved, setSaved] = useState<Record<string, boolean>>({});
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [values, setValues] = useState<Record<string, string>>({});

  useEffect(() => {
    let cancelled = false;

    fetch("/api/v1/settings")
      .then((r) => r.json())
      .then((data: Setting[]) => {
        if (cancelled) return;
        setSettings(data);
        const nextValues: Record<string, string> = {};
        data.forEach((setting) => {
          nextValues[setting.key] = SECRET_KEYS.has(setting.key) ? "" : setting.value;
        });
        setValues(nextValues);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });

    return () => {
      cancelled = true;
    };
  }, []);

  const updateSettingState = (updated: Setting, overrideValue?: string) => {
    setSettings((current) => {
      const next = current.filter((setting) => setting.key !== updated.key);
      return [...next, updated].sort((a, b) => a.key.localeCompare(b.key));
    });
    setValues((current) => ({
      ...current,
      [updated.key]: SECRET_KEYS.has(updated.key) ? "" : overrideValue ?? updated.value,
    }));
  };

  const persistSetting = async (key: string, value: string): Promise<boolean> => {
    const response = await fetch(`/api/v1/settings/${key}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ value }),
    });

    if (!response.ok) {
      const data = await response.json().catch(() => ({ detail: "Failed to save" }));
      throw new Error(data.detail || "Failed to save");
    }

    const updated = (await response.json()) as Setting;
    updateSettingState(updated, value);
    return true;
  };

  const runSave = async (id: string, fn: () => Promise<void>) => {
    setSaving((current) => ({ ...current, [id]: true }));
    setErrors((current) => ({ ...current, [id]: "" }));
    try {
      await fn();
      setSaved((current) => ({ ...current, [id]: true }));
      window.setTimeout(() => {
        setSaved((current) => ({ ...current, [id]: false }));
      }, 2500);
    } catch (error) {
      const message = error instanceof Error ? error.message : "Network error";
      setErrors((current) => ({ ...current, [id]: message }));
    } finally {
      setSaving((current) => ({ ...current, [id]: false }));
    }
  };

  const saveRuntime = async (scope: RuntimeScope, provider: ProviderKey, model: string) => {
    const keys = RUNTIME_KEYS[scope];
    await persistSetting(keys.provider, provider);
    await persistSetting(keys.model, model.trim() || defaultModelForProvider(provider));
  };

  const updateRuntimeProvider = (scope: RuntimeScope, nextProvider: ProviderKey) => {
    const keys = RUNTIME_KEYS[scope];
    setValues((current) => {
      const currentProvider = getRuntimeProvider(current, scope);
      const currentModel = getRuntimeModel(current, scope, currentProvider);
      const nextModel = !currentModel || currentModel === defaultModelForProvider(currentProvider)
        ? defaultModelForProvider(nextProvider)
        : currentModel;
      return {
        ...current,
        [keys.provider]: nextProvider,
        [keys.model]: nextModel,
      };
    });
  };

  const lastUpdated = (() => {
    if (settings.length === 0) return null;
    return settings.reduce((latest, setting) => (
      new Date(setting.updated_at).getTime() > new Date(latest.updated_at).getTime() ? setting : latest
    )).updated_at;
  })();

  if (loading) return <div className="empty-state">Loading settings…</div>;

  const chatProvider = getRuntimeProvider(values, "chat");
  const chatModel = getRuntimeModel(values, "chat", chatProvider);
  const engineProvider = getRuntimeProvider(values, "engine");
  const engineModel = getRuntimeModel(values, "engine", engineProvider);
  const intervalMinutes = parseInt(values.proactive_interval_minutes || "60", 10);
  const anthropicMasked = settings.find((setting) => setting.key === "anthropic_api_key")?.value || "";
  const openaiMasked = settings.find((setting) => setting.key === "openai_api_key")?.value || "";

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 20 }}>
      <p style={{ margin: 0, fontSize: "0.8rem", color: "#888", lineHeight: 1.5 }}>
        Chat and self-evolution now have separate runtime selection. You can choose different
        providers and models for each one while reusing the same provider API keys below.
      </p>

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
            onChange={(e) => setValues((current) => ({ ...current, proactive_interval_minutes: e.target.value }))}
            style={{ flex: 1, accentColor: "#3b82f6" }}
          />
          <input
            type="number"
            min={5}
            max={1440}
            value={values.proactive_interval_minutes || "60"}
            onChange={(e) => setValues((current) => ({ ...current, proactive_interval_minutes: e.target.value }))}
            style={{ width: 60, background: "rgba(0,0,0,0.25)", border: "1px solid rgba(255,255,255,0.1)", borderRadius: 6, color: "#e0e0e0", padding: "5px 8px", fontFamily: "inherit", fontSize: "0.82rem", textAlign: "center" }}
          />
          <span style={{ fontSize: "0.78rem", color: "#666" }}>min</span>
          <button
            className="refresh-btn"
            style={{ padding: "4px 12px", ...statusStyles(Boolean(saved.proactive_interval_minutes)) }}
            onClick={() => runSave("proactive_interval_minutes", () => persistSetting("proactive_interval_minutes", values.proactive_interval_minutes || "60").then(() => undefined))}
            disabled={saving.proactive_interval_minutes}
          >
            {saved.proactive_interval_minutes ? "✓ Saved" : saving.proactive_interval_minutes ? "…" : "Save"}
          </button>
        </div>
        {errors.proactive_interval_minutes && (
          <div style={{ marginTop: 6, fontSize: "0.75rem", color: "#ef4444" }}>{errors.proactive_interval_minutes}</div>
        )}
      </div>

      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(360px, 1fr))", gap: 14 }}>
        {([
          ["chat", chatProvider, chatModel],
          ["engine", engineProvider, engineModel],
        ] as [RuntimeScope, ProviderKey, string][]).map(([scope, provider, model]) => (
          <div
            key={scope}
            style={{
              background: "rgba(255,255,255,0.03)",
              border: "1px solid rgba(255,255,255,0.07)",
              borderRadius: 10,
              padding: "14px 16px",
            }}
          >
            <div style={{ marginBottom: 10 }}>
              <div style={{ fontSize: "0.85rem", color: "#e0e0e0", fontWeight: 500 }}>{runtimeTitle(scope)}</div>
              <div style={{ fontSize: "0.75rem", color: "#666", marginTop: 2 }}>
                {runtimeDescription(scope)}
              </div>
            </div>

            <div style={{ display: "flex", flexWrap: "wrap", gap: 10, alignItems: "end" }}>
              <label style={{ display: "flex", flexDirection: "column", gap: 6 }}>
                <span style={{ fontSize: "0.75rem", color: "#8b8b8b" }}>Provider</span>
                <select
                  value={provider}
                  onChange={(e) => updateRuntimeProvider(scope, normalizeProvider(e.target.value))}
                  style={{ minWidth: 200, background: "rgba(0,0,0,0.25)", border: "1px solid rgba(255,255,255,0.1)", borderRadius: 6, color: "#e0e0e0", padding: "7px 10px", fontFamily: "inherit", fontSize: "0.82rem" }}
                >
                  <option value="anthropic">Anthropic</option>
                  <option value="bedrock">Amazon Bedrock</option>
                  <option value="openai">OpenAI</option>
                </select>
              </label>

              <label style={{ display: "flex", flexDirection: "column", gap: 6, flex: "1 1 280px" }}>
                <span style={{ fontSize: "0.75rem", color: "#8b8b8b" }}>Model</span>
                <input
                  type="text"
                  value={values[RUNTIME_KEYS[scope].model] ?? model}
                  placeholder={defaultModelForProvider(provider)}
                  onChange={(e) => setValues((current) => ({ ...current, [RUNTIME_KEYS[scope].model]: e.target.value }))}
                  style={{ minWidth: 0, width: "100%", background: "rgba(0,0,0,0.25)", border: "1px solid rgba(255,255,255,0.1)", borderRadius: 6, color: "#e0e0e0", padding: "6px 10px", fontFamily: "ui-monospace, monospace", fontSize: "0.82rem" }}
                />
              </label>

              <button
                className="refresh-btn"
                style={{ padding: "6px 14px", minWidth: 88, ...statusStyles(Boolean(saved[`${scope}_runtime`])) }}
                onClick={() => runSave(`${scope}_runtime`, () => saveRuntime(scope, provider, values[RUNTIME_KEYS[scope].model] || model))}
                disabled={saving[`${scope}_runtime`]}
              >
                {saved[`${scope}_runtime`] ? "✓ Saved" : saving[`${scope}_runtime`] ? "…" : "Save"}
              </button>
            </div>

            <div style={{ marginTop: 10, fontSize: "0.75rem", color: "#666", lineHeight: 1.5 }}>
              {runtimeProviderHelp(scope, provider)}
            </div>

            {errors[`${scope}_runtime`] && (
              <div style={{ marginTop: 6, fontSize: "0.75rem", color: "#ef4444" }}>{errors[`${scope}_runtime`]}</div>
            )}
          </div>
        ))}
      </div>

      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(360px, 1fr))", gap: 14 }}>
        <div style={{ background: "rgba(255,255,255,0.03)", border: "1px solid rgba(255,255,255,0.07)", borderRadius: 10, padding: "14px 16px" }}>
          <div style={{ marginBottom: 8 }}>
            <div style={{ fontSize: "0.85rem", color: "#e0e0e0", fontWeight: 500 }}>Anthropic API Key</div>
            <div style={{ fontSize: "0.75rem", color: "#666", marginTop: 2 }}>
              {anthropicMasked ? `Stored locally as ${anthropicMasked}. Enter a new key to replace it.` : "No local override saved. Leave blank to use ENGINE_ANTHROPIC_API_KEY."}
            </div>
          </div>
          <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
            <input
              type="password"
              placeholder="sk-ant-…"
              value={values.anthropic_api_key || ""}
              onChange={(e) => setValues((current) => ({ ...current, anthropic_api_key: e.target.value }))}
              style={{ flex: "1 1 260px", minWidth: 0, background: "rgba(0,0,0,0.25)", border: "1px solid rgba(255,255,255,0.1)", borderRadius: 6, color: "#e0e0e0", padding: "6px 10px", fontFamily: "ui-monospace, monospace", fontSize: "0.82rem" }}
            />
            <button
              className="refresh-btn"
              style={{ padding: "4px 12px", minWidth: 84, ...statusStyles(Boolean(saved.anthropic_api_key)) }}
              onClick={() => runSave("anthropic_api_key", () => persistSetting("anthropic_api_key", (values.anthropic_api_key || "").trim()).then(() => undefined))}
              disabled={saving.anthropic_api_key || !(values.anthropic_api_key || "").trim()}
            >
              {saved.anthropic_api_key ? "✓ Saved" : saving.anthropic_api_key ? "…" : "Update"}
            </button>
            <button
              className="refresh-btn"
              style={{ padding: "4px 12px", minWidth: 84 }}
              onClick={() => runSave("anthropic_api_key", () => persistSetting("anthropic_api_key", "").then(() => undefined))}
              disabled={saving.anthropic_api_key || !anthropicMasked}
            >
              Clear
            </button>
          </div>
          {errors.anthropic_api_key && (
            <div style={{ marginTop: 6, fontSize: "0.75rem", color: "#ef4444" }}>{errors.anthropic_api_key}</div>
          )}
        </div>

        <div style={{ background: "rgba(255,255,255,0.03)", border: "1px solid rgba(255,255,255,0.07)", borderRadius: 10, padding: "14px 16px" }}>
          <div style={{ marginBottom: 8 }}>
            <div style={{ fontSize: "0.85rem", color: "#e0e0e0", fontWeight: 500 }}>OpenAI API Key</div>
            <div style={{ fontSize: "0.75rem", color: "#666", marginTop: 2 }}>
              {openaiMasked ? `Stored locally as ${openaiMasked}. Enter a new key to replace it.` : "No local override saved. Leave blank to use ENGINE_OPENAI_API_KEY."}
            </div>
          </div>
          <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
            <input
              type="password"
              placeholder="sk-proj-…"
              value={values.openai_api_key || ""}
              onChange={(e) => setValues((current) => ({ ...current, openai_api_key: e.target.value }))}
              style={{ flex: "1 1 260px", minWidth: 0, background: "rgba(0,0,0,0.25)", border: "1px solid rgba(255,255,255,0.1)", borderRadius: 6, color: "#e0e0e0", padding: "6px 10px", fontFamily: "ui-monospace, monospace", fontSize: "0.82rem" }}
            />
            <button
              className="refresh-btn"
              style={{ padding: "4px 12px", minWidth: 84, ...statusStyles(Boolean(saved.openai_api_key)) }}
              onClick={() => runSave("openai_api_key", () => persistSetting("openai_api_key", (values.openai_api_key || "").trim()).then(() => undefined))}
              disabled={saving.openai_api_key || !(values.openai_api_key || "").trim()}
            >
              {saved.openai_api_key ? "✓ Saved" : saving.openai_api_key ? "…" : "Update"}
            </button>
            <button
              className="refresh-btn"
              style={{ padding: "4px 12px", minWidth: 84 }}
              onClick={() => runSave("openai_api_key", () => persistSetting("openai_api_key", "").then(() => undefined))}
              disabled={saving.openai_api_key || !openaiMasked}
            >
              Clear
            </button>
          </div>
          {errors.openai_api_key && (
            <div style={{ marginTop: 6, fontSize: "0.75rem", color: "#ef4444" }}>{errors.openai_api_key}</div>
          )}
        </div>
      </div>

      <div style={{ fontSize: "0.72rem", color: "#555", textAlign: "right" }}>
        {lastUpdated && `Last updated: ${new Date(lastUpdated).toLocaleString()}`}
      </div>
    </div>
  );
}
