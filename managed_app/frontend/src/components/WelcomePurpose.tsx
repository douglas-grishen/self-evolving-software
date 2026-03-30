import { useState } from "react";
import { AppWindow } from "./AppWindow";
import { fetchWithAuth } from "../hooks/useAuth";

interface WelcomePurposeProps {
  onSaved: () => void;
}

const PURPOSE_LIST_KEYS = [
  "functional_requirements",
  "technical_requirements",
  "security_requirements",
  "constraints",
  "evolution_directives",
] as const;

type PurposeListKey = (typeof PURPOSE_LIST_KEYS)[number];

interface ParsedPurpose {
  version: number | null;
  identity: { name: string; description: string };
  lists: Record<PurposeListKey, string[]>;
}

function createEmptyPurposeLists(): Record<PurposeListKey, string[]> {
  return {
    functional_requirements: [],
    technical_requirements: [],
    security_requirements: [],
    constraints: [],
    evolution_directives: [],
  };
}

function buildPurposeTemplate(): string {
  return `version: 1
updated_at: "${new Date().toISOString()}"

identity:
  name: ""
  description: ""

functional_requirements:
  - ""

technical_requirements: []

security_requirements: []

constraints: []

evolution_directives: []
`;
}

function unquoteYamlScalar(value: string): string {
  const trimmed = value.trim();
  if (
    (trimmed.startsWith('"') && trimmed.endsWith('"')) ||
    (trimmed.startsWith("'") && trimmed.endsWith("'"))
  ) {
    return trimmed.slice(1, -1);
  }
  return trimmed;
}

function parsePurposeYaml(yaml: string): ParsedPurpose {
  const parsed: ParsedPurpose = {
    version: null,
    identity: { name: "", description: "" },
    lists: createEmptyPurposeLists(),
  };

  let currentListKey: PurposeListKey | null = null;
  let inIdentity = false;

  for (const rawLine of yaml.split("\n")) {
    const trimmed = rawLine.trim();
    if (!trimmed || trimmed.startsWith("#")) continue;

    if (trimmed.startsWith("version:")) {
      const version = Number.parseInt(trimmed.slice("version:".length).trim(), 10);
      parsed.version = Number.isFinite(version) ? version : null;
      currentListKey = null;
      inIdentity = false;
      continue;
    }

    if (trimmed.startsWith("updated_at:")) {
      currentListKey = null;
      inIdentity = false;
      continue;
    }

    if (trimmed === "identity:") {
      inIdentity = true;
      currentListKey = null;
      continue;
    }

    if (inIdentity && trimmed.startsWith("name:")) {
      parsed.identity.name = unquoteYamlScalar(trimmed.slice("name:".length));
      continue;
    }

    if (inIdentity && trimmed.startsWith("description:")) {
      parsed.identity.description = unquoteYamlScalar(trimmed.slice("description:".length));
      continue;
    }

    const listMatch = trimmed.match(
      /^(functional_requirements|technical_requirements|security_requirements|constraints|evolution_directives):\s*(\[\])?$/
    );
    if (listMatch) {
      currentListKey = listMatch[1] as PurposeListKey;
      inIdentity = false;
      if (listMatch[2] === "[]") {
        parsed.lists[currentListKey] = [];
      }
      continue;
    }

    if (trimmed.startsWith("- ") && currentListKey) {
      parsed.lists[currentListKey].push(unquoteYamlScalar(trimmed.slice(2)));
      continue;
    }

    if (trimmed.endsWith(":")) {
      currentListKey = null;
      inIdentity = false;
    }
  }

  return parsed;
}

function validatePurposeYaml(yaml: string): { version: number; error: null } | { version: null; error: string } {
  const parsed = parsePurposeYaml(yaml);

  if (parsed.version === null || parsed.version < 1) {
    return { version: null, error: "Purpose block must include a numeric version >= 1." };
  }
  if (!parsed.identity.name.trim()) {
    return { version: null, error: "Purpose block must define identity.name." };
  }
  if (!parsed.identity.description.trim()) {
    return { version: null, error: "Purpose block must define identity.description." };
  }
  if (!parsed.lists.functional_requirements.some((item) => item.trim())) {
    return { version: null, error: "Purpose block must include at least one functional requirement." };
  }

  return { version: parsed.version, error: null };
}

export function WelcomePurpose({ onSaved }: WelcomePurposeProps) {
  const [step, setStep] = useState<"welcome" | "form">("welcome");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [purposeYaml, setPurposeYaml] = useState(() => buildPurposeTemplate());
  const validation = validatePurposeYaml(purposeYaml);

  const handleSave = async () => {
    if (validation.error) {
      setError(validation.error);
      return;
    }

    setSaving(true);
    setError(null);
    try {
      const yaml = `${purposeYaml.trimEnd()}\n`;
      const res = await fetchWithAuth("/api/v1/evolution/purpose", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
        },
        body: JSON.stringify({ version: validation.version, content_yaml: yaml }),
      });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(
          res.status === 401
            ? "Session expired. Sign in again."
            : body.detail || `HTTP ${res.status}`,
        );
      }
      onSaved();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setSaving(false);
    }
  };

  return (
    <AppWindow
      title={step === "welcome" ? "Welcome to Self-Evolving Software" : "Define Your Purpose"}
      onClose={() => {}}
      width="720px"
      height="620px"
    >
      {step === "welcome" ? (
        <div className="wp-welcome">
          <div className="wp-icon">⚙️</div>
          <h2 className="wp-title">Your system has no Purpose yet.</h2>
          <p className="wp-lead">
            <strong>Purpose</strong> is the foundational specification that guides every
            autonomous evolution this system will ever make. Without it, the engine has
            no direction.
          </p>

          <div className="wp-section">
            <h3>What is a Purpose?</h3>
            <p>
              A Purpose defines <em>what</em> your system must achieve and <em>how</em> it
              must behave — now and as it evolves. It acts as a constitution: the engine
              reads it before every decision, and all generated code must conform to it.
            </p>
          </div>

          <div className="wp-section">
            <h3>Why does it matter?</h3>
            <ul>
              <li>🎯 <strong>Direction</strong> — every evolution is evaluated against your Purpose</li>
              <li>🛡️ <strong>Safety</strong> — constraints in the Purpose prevent harmful changes</li>
              <li>🔄 <strong>Continuity</strong> — even as code changes, intent stays consistent</li>
              <li>📜 <strong>Auditability</strong> — a versioned record of why the system evolves</li>
            </ul>
          </div>

          <div className="wp-tips">
            <h3>Tips for a great Purpose</h3>
            <div className="wp-tips-grid">
              <div className="wp-tip">
                <span className="wp-tip-icon">✏️</span>
                <span>Be specific about <strong>what the software does</strong>, not how it does it</span>
              </div>
              <div className="wp-tip">
                <span className="wp-tip-icon">🚧</span>
                <span>List real <strong>constraints</strong>: budget, tech stack, compliance</span>
              </div>
              <div className="wp-tip">
                <span className="wp-tip-icon">🔒</span>
                <span>Include at least one <strong>security requirement</strong> — don't skip this</span>
              </div>
              <div className="wp-tip">
                <span className="wp-tip-icon">🧭</span>
                <span>Add <strong>evolution directives</strong> to guide the engine's priorities</span>
              </div>
              <div className="wp-tip">
                <span className="wp-tip-icon">📐</span>
                <span>Keep requirements <strong>measurable</strong> — "respond in &lt;200ms" beats "be fast"</span>
              </div>
              <div className="wp-tip">
                <span className="wp-tip-icon">🔁</span>
                <span>You can always <strong>evolve the Purpose</strong> later via an Inception</span>
              </div>
            </div>
          </div>

          <button className="wp-cta" onClick={() => setStep("form")}>
            Edit Purpose Block →
          </button>
        </div>
      ) : (
        <div className="wp-form">
          <p className="wp-form-intro">
            Edit the full YAML block exactly as it will be stored in the database.
          </p>

          <div className="wp-field">
            <label>Purpose YAML <span className="wp-required">*</span></label>
            <p className="wp-field-help">
              This block is stored verbatim as <code>content_yaml</code>. The UI only
              validates the minimum required structure before saving.
            </p>
            <textarea
              className="wp-purpose-editor"
              value={purposeYaml}
              onChange={(e) => {
                setPurposeYaml(e.target.value);
                if (error) setError(null);
              }}
              rows={18}
              spellCheck={false}
            />
          </div>

          {error && <div className="wp-error">{error}</div>}

          <div className="wp-form-actions">
            <button className="wp-back-btn" type="button" onClick={() => setStep("welcome")}>
              ← Back
            </button>
            <button
              className="wp-save-btn"
              type="button"
              onClick={handleSave}
              disabled={saving || validation.error !== null}
            >
              {saving ? "Saving…" : "Save Purpose Block"}
            </button>
          </div>
        </div>
      )}
    </AppWindow>
  );
}
