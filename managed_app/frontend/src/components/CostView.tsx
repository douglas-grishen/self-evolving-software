import { useEffect, useMemo, useState } from "react";
import { useEvolutionStatus } from "../hooks/useEvolutionApi";
import { useSystemInfo } from "../hooks/useSystemInfo";

interface Setting {
  key: string;
  value: string;
  description: string | null;
  updated_at: string;
}

interface EngineUsageSnapshot {
  date: string;
  updated_at: string;
  llm_calls: number;
  input_tokens: number;
  output_tokens: number;
  proactive_runs: number;
  failed_evolutions: number;
  task_attempts: Record<string, number>;
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

const BUDGET_DEFAULTS = {
  engine_daily_llm_calls_limit: 60,
  engine_daily_input_tokens_limit: 500000,
  engine_daily_output_tokens_limit: 120000,
  engine_daily_proactive_runs_limit: 24,
  engine_daily_failed_evolutions_limit: 10,
  engine_daily_task_attempt_limit: 3,
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

function parseIntegerSetting(settings: Setting[], key: keyof typeof BUDGET_DEFAULTS): number {
  const raw = settings.find((setting) => setting.key === key)?.value;
  const parsed = raw ? parseInt(raw, 10) : NaN;
  return Number.isFinite(parsed) && parsed > 0 ? parsed : BUDGET_DEFAULTS[key];
}

function parseUsageSnapshot(settings: Setting[]): EngineUsageSnapshot | null {
  const raw = settings.find((setting) => setting.key === "engine_daily_usage_snapshot")?.value;
  if (!raw) return null;

  try {
    const data = JSON.parse(raw) as Partial<EngineUsageSnapshot>;
    return {
      date: data.date || "",
      updated_at: data.updated_at || "",
      llm_calls: Number(data.llm_calls || 0),
      input_tokens: Number(data.input_tokens || 0),
      output_tokens: Number(data.output_tokens || 0),
      proactive_runs: Number(data.proactive_runs || 0),
      failed_evolutions: Number(data.failed_evolutions || 0),
      task_attempts: data.task_attempts || {},
    };
  } catch {
    return null;
  }
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
  const usageSnapshot = useMemo(() => parseUsageSnapshot(settings), [settings]);
  const llmCallsLimit = useMemo(() => parseIntegerSetting(settings, "engine_daily_llm_calls_limit"), [settings]);
  const inputTokensLimit = useMemo(() => parseIntegerSetting(settings, "engine_daily_input_tokens_limit"), [settings]);
  const outputTokensLimit = useMemo(() => parseIntegerSetting(settings, "engine_daily_output_tokens_limit"), [settings]);
  const proactiveRunsLimit = useMemo(() => parseIntegerSetting(settings, "engine_daily_proactive_runs_limit"), [settings]);
  const failedRunsLimit = useMemo(() => parseIntegerSetting(settings, "engine_daily_failed_evolutions_limit"), [settings]);
  const taskAttemptsLimit = useMemo(() => parseIntegerSetting(settings, "engine_daily_task_attempt_limit"), [settings]);
  const topTaskAttempt = useMemo(() => {
    if (!usageSnapshot) return null;
    const entries = Object.entries(usageSnapshot.task_attempts || {});
    if (entries.length === 0) return null;
    entries.sort((a, b) => b[1] - a[1]);
    return entries[0];
  }, [usageSnapshot]);

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
          This tab now shows engine-side token and call telemetry plus the daily budgets that keep
          autonomous self-evolution from looping too aggressively. Dollar-denominated provider
          spend is still not instrumented in this open-source build.
        </p>
      </div>

      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(160px, 1fr))", gap: 12 }}>
        <CostStat label="Version" value={version || "0.0.0"} />
        <CostStat label="Deploy" value={`#${deploy_version ?? 0}`} />
        <CostStat
          label="Failed Evolution Rate"
          value={failedRate}
          tone={status && status.failed_evolutions > 0 ? "#fca5a5" : "#86efac"}
        />
      </div>

      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(150px, 1fr))", gap: 12 }}>
        <CostStat label="Total Evolutions" value={String(status?.total_evolutions ?? 0)} />
        <CostStat label="Completed" value={String(status?.completed_evolutions ?? 0)} tone="#86efac" />
        <CostStat label="Failed" value={String(status?.failed_evolutions ?? 0)} tone="#fca5a5" />
        <CostStat label="Active" value={String(status?.active_evolutions ?? 0)} tone="#93c5fd" />
      </div>

      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(170px, 1fr))", gap: 12 }}>
        <CostStat
          label="Engine LLM Calls Today"
          value={`${usageSnapshot?.llm_calls ?? 0} / ${llmCallsLimit}`}
          tone={usageSnapshot && usageSnapshot.llm_calls >= llmCallsLimit ? "#fca5a5" : "#e5e7eb"}
        />
        <CostStat
          label="Input Tokens Today"
          value={`${usageSnapshot?.input_tokens ?? 0} / ${inputTokensLimit}`}
          tone={usageSnapshot && usageSnapshot.input_tokens >= inputTokensLimit ? "#fca5a5" : "#e5e7eb"}
        />
        <CostStat
          label="Output Tokens Today"
          value={`${usageSnapshot?.output_tokens ?? 0} / ${outputTokensLimit}`}
          tone={usageSnapshot && usageSnapshot.output_tokens >= outputTokensLimit ? "#fca5a5" : "#e5e7eb"}
        />
        <CostStat
          label="Proactive Runs Today"
          value={`${usageSnapshot?.proactive_runs ?? 0} / ${proactiveRunsLimit}`}
          tone={usageSnapshot && usageSnapshot.proactive_runs >= proactiveRunsLimit ? "#fca5a5" : "#e5e7eb"}
        />
        <CostStat
          label="Failed Runs Today"
          value={`${usageSnapshot?.failed_evolutions ?? 0} / ${failedRunsLimit}`}
          tone={usageSnapshot && usageSnapshot.failed_evolutions >= failedRunsLimit ? "#fca5a5" : "#e5e7eb"}
        />
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
            {usageSnapshot ? (
              <>
                Usage ledger day:
                <strong style={{ marginLeft: 6, color: "#eff6ff" }}>{usageSnapshot.date || "UTC today"}</strong>
                <br />
                Updated:
                <strong style={{ marginLeft: 6, color: "#eff6ff" }}>
                  {usageSnapshot.updated_at ? new Date(usageSnapshot.updated_at).toLocaleString() : "Unknown"}
                </strong>
                <br />
                Same-task cap:
                <strong style={{ marginLeft: 6, color: "#eff6ff" }}>{taskAttemptsLimit}</strong>
                {topTaskAttempt && (
                  <>
                    <br />
                    Hottest task:
                    <strong style={{ marginLeft: 6, color: "#eff6ff" }}>
                      {topTaskAttempt[0]} ({topTaskAttempt[1]} starts today)
                    </strong>
                  </>
                )}
              </>
            ) : (
              <>
                The engine has not published a daily usage snapshot yet.
                <br />
                Budgets are configured, but token/call telemetry will appear after the next engine loop.
              </>
            )}
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
