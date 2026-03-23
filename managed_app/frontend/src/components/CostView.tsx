import { useEffect, useMemo, useState } from "react";
import { useEvolutionStatus } from "../hooks/useEvolutionApi";
import { useSystemInfo } from "../hooks/useSystemInfo";

interface Setting {
  key: string;
  value: string;
  description: string | null;
  updated_at: string;
}

type ProviderKey = "anthropic" | "bedrock" | "openai";

const RUNTIME_KEYS = {
  chat: {
    provider: "chat_llm_provider",
    model: "chat_llm_model",
  },
  engine: {
    provider: "engine_llm_provider",
    model: "engine_llm_model",
  },
} as const;

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

function resolveRuntimeProvider(settings: Setting[], scope: keyof typeof RUNTIME_KEYS): ProviderKey {
  const scoped = settings.find((setting) => setting.key === RUNTIME_KEYS[scope].provider)?.value;
  const legacy = settings.find((setting) => setting.key === "llm_provider")?.value;
  return normalizeProvider(scoped || legacy);
}

function resolveRuntimeModel(
  settings: Setting[],
  scope: keyof typeof RUNTIME_KEYS,
  provider: ProviderKey,
): string {
  const scoped = settings.find((setting) => setting.key === RUNTIME_KEYS[scope].model)?.value?.trim();
  if (scoped) return scoped;

  const legacy = settings.find((setting) => setting.key === "llm_model")?.value?.trim();
  if (legacy) return legacy;

  return defaultModelForProvider(provider);
}

function CostStat({
  label,
  value,
  tone = "#e5e7eb",
}: {
  label: string;
  value: string;
  tone?: string;
}) {
  return (
    <div
      style={{
        padding: "14px 16px",
        borderRadius: 12,
        border: "1px solid rgba(255,255,255,0.08)",
        background: "rgba(255,255,255,0.03)",
      }}
    >
      <div style={{ fontSize: "0.75rem", color: "#8b8f97", marginBottom: 6 }}>{label}</div>
      <div style={{ fontSize: "1.45rem", fontWeight: 600, color: tone }}>{value}</div>
    </div>
  );
}

export function CostView() {
  const { status } = useEvolutionStatus(15000);
  const { deploy_version, version } = useSystemInfo();
  const [settings, setSettings] = useState<Setting[]>([]);
  const [loadingSettings, setLoadingSettings] = useState(true);
  const [settingsError, setSettingsError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;

    fetch("/api/v1/settings")
      .then((res) => {
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        return res.json();
      })
      .then((data: Setting[]) => {
        if (cancelled) return;
        setSettings(data);
        setSettingsError(null);
      })
      .catch((err: Error) => {
        if (cancelled) return;
        setSettingsError(err.message);
        setSettings([]);
      })
      .finally(() => {
        if (!cancelled) setLoadingSettings(false);
      });

    return () => {
      cancelled = true;
    };
  }, []);

  const anthropicSetting = useMemo(
    () => settings.find((setting) => setting.key === "anthropic_api_key"),
    [settings],
  );
  const openaiSetting = useMemo(
    () => settings.find((setting) => setting.key === "openai_api_key"),
    [settings],
  );
  const anthropicConfigured = Boolean(anthropicSetting?.value);
  const openaiConfigured = Boolean(openaiSetting?.value);
  const chatProvider = useMemo(() => resolveRuntimeProvider(settings, "chat"), [settings]);
  const chatModel = useMemo(() => resolveRuntimeModel(settings, "chat", chatProvider), [chatProvider, settings]);
  const engineProvider = useMemo(() => resolveRuntimeProvider(settings, "engine"), [settings]);
  const engineModel = useMemo(() => resolveRuntimeModel(settings, "engine", engineProvider), [engineProvider, settings]);

  const failedRate = useMemo(() => {
    if (!status || status.total_evolutions === 0) return "0%";
    return `${Math.round((status.failed_evolutions / status.total_evolutions) * 100)}%`;
  }, [status]);

  const lastEvolutionText = useMemo(() => {
    if (!status?.last_evolution) return "No evolution recorded";
    const summary =
      status.last_evolution.plan_summary ||
      status.last_evolution.user_request ||
      status.last_evolution.status;
    return `${summary} · ${status.last_evolution.status}`;
  }, [status]);

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 18 }}>
      <div>
        <h2 style={{ margin: "0 0 6px 0", fontSize: "1.1rem", color: "#e5e7eb" }}>Cost & Usage</h2>
        <p style={{ margin: 0, color: "#8b8f97", fontSize: "0.82rem", lineHeight: 1.5 }}>
          This tab was restored as a runtime cost surface. The current open-source build does not
          expose dollar-denominated spend or token telemetry yet, so it shows the signals that do
          exist: release state, evolution volume, and runtime configuration.
        </p>
      </div>

      <div style={{ display: "grid", gridTemplateColumns: "repeat(3, minmax(0, 1fr))", gap: 12 }}>
        <CostStat label="Version" value={version || "0.0.0"} />
        <CostStat label="Deploy" value={`#${deploy_version ?? 0}`} />
        <CostStat
          label="Failed Evolution Rate"
          value={failedRate}
          tone={status && status.failed_evolutions > 0 ? "#fca5a5" : "#86efac"}
        />
      </div>

      <div style={{ display: "grid", gridTemplateColumns: "repeat(4, minmax(0, 1fr))", gap: 12 }}>
        <CostStat label="Total Evolutions" value={String(status?.total_evolutions ?? 0)} />
        <CostStat label="Completed" value={String(status?.completed_evolutions ?? 0)} tone="#86efac" />
        <CostStat label="Failed" value={String(status?.failed_evolutions ?? 0)} tone="#fca5a5" />
        <CostStat label="Active" value={String(status?.active_evolutions ?? 0)} tone="#93c5fd" />
      </div>

      <div
        style={{
          display: "grid",
          gridTemplateColumns: "1.2fr 0.8fr",
          gap: 14,
        }}
      >
        <div
          style={{
            padding: "16px 18px",
            borderRadius: 12,
            border: "1px solid rgba(255,255,255,0.08)",
            background: "rgba(255,255,255,0.03)",
          }}
        >
          <div style={{ fontSize: "0.85rem", fontWeight: 600, color: "#e5e7eb", marginBottom: 10 }}>
            Provider Configuration
          </div>
            <div style={{ display: "flex", flexDirection: "column", gap: 8, color: "#cbd5e1", fontSize: "0.82rem" }}>
              <div>
              Chat runtime:
              <strong style={{ marginLeft: 6, color: "#e5e7eb" }}>{chatProvider}</strong>
              <span style={{ marginLeft: 6, color: "#94a3b8" }}>{chatModel}</span>
            </div>
            <div>
              Engine runtime:
              <strong style={{ marginLeft: 6, color: "#e5e7eb" }}>{engineProvider}</strong>
              <span style={{ marginLeft: 6, color: "#94a3b8" }}>{engineModel}</span>
            </div>
            <div>
              Anthropic key:
              <strong style={{ color: anthropicConfigured ? "#86efac" : "#fca5a5", marginLeft: 6 }}>
                {loadingSettings ? "Checking…" : anthropicConfigured ? "Configured" : "Missing"}
              </strong>
            </div>
            <div>
              OpenAI key:
              <strong style={{ color: openaiConfigured ? "#86efac" : "#fca5a5", marginLeft: 6 }}>
                {loadingSettings ? "Checking…" : openaiConfigured ? "Configured" : "Missing"}
              </strong>
            </div>
            <div>
              Bedrock runtime:
              <strong style={{ marginLeft: 6, color: chatProvider === "bedrock" || engineProvider === "bedrock" ? "#86efac" : "#94a3b8" }}>
                {chatProvider === "bedrock" || engineProvider === "bedrock" ? "Selected" : "Standby"}
              </strong>
            </div>
            <div>
              Pending inceptions:
              <strong style={{ marginLeft: 6, color: "#e5e7eb" }}>{status?.pending_inceptions ?? 0}</strong>
            </div>
            <div>
              Purpose version:
              <strong style={{ marginLeft: 6, color: "#e5e7eb" }}>
                {status?.current_purpose_version ?? "None"}
              </strong>
            </div>
            {settingsError && (
              <div style={{ color: "#fca5a5" }}>Settings endpoint error: {settingsError}</div>
            )}
          </div>
        </div>

        <div
          style={{
            padding: "16px 18px",
            borderRadius: 12,
            border: "1px solid rgba(255,255,255,0.08)",
            background: "rgba(59,130,246,0.08)",
          }}
        >
          <div style={{ fontSize: "0.85rem", fontWeight: 600, color: "#dbeafe", marginBottom: 10 }}>
            Spend Telemetry
          </div>
          <div style={{ color: "#bfdbfe", fontSize: "0.82rem", lineHeight: 1.55 }}>
            Detailed provider spend is not instrumented in this build.
            <br />
            This view is intentionally honest: it restores the missing tab and surfaces the
            operational signals that are currently available.
          </div>
        </div>
      </div>

      <div
        style={{
          padding: "16px 18px",
          borderRadius: 12,
          border: "1px solid rgba(255,255,255,0.08)",
          background: "rgba(255,255,255,0.03)",
        }}
      >
        <div style={{ fontSize: "0.85rem", fontWeight: 600, color: "#e5e7eb", marginBottom: 8 }}>
          Latest Evolution
        </div>
        <div style={{ color: "#cbd5e1", fontSize: "0.82rem", lineHeight: 1.55 }}>{lastEvolutionText}</div>
      </div>
    </div>
  );
}
