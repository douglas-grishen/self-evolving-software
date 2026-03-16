import { useState } from "react";
import { AppWindow } from "./AppWindow";
import { getAuthToken } from "../hooks/useAuth";

interface WelcomePurposeProps {
  onSaved: () => void;
}

// Build a purpose YAML string from structured fields
function buildPurposeYaml(fields: PurposeFields): string {
  const now = new Date().toISOString().split("T")[0];

  const list = (items: string[]) =>
    items
      .map((s) => s.trim())
      .filter(Boolean)
      .map((s) => `  - "${s}"`)
      .join("\n");

  return `version: 1
updated_at: "${now}"

identity:
  name: "${fields.name}"
  description: "${fields.description}"

functional_requirements:
${list(fields.functional)}

technical_requirements:
${list(fields.technical)}

security_requirements:
${list(fields.security)}

constraints:
${list(fields.constraints)}

evolution_directives:
${list(fields.directives)}
`;
}

interface PurposeFields {
  name: string;
  description: string;
  functional: string[];
  technical: string[];
  security: string[];
  constraints: string[];
  directives: string[];
}

function ListEditor({
  label,
  hint,
  items,
  onChange,
}: {
  label: string;
  hint: string;
  items: string[];
  onChange: (items: string[]) => void;
}) {
  const update = (i: number, val: string) => {
    const next = [...items];
    next[i] = val;
    onChange(next);
  };
  const add = () => onChange([...items, ""]);
  const remove = (i: number) => onChange(items.filter((_, idx) => idx !== i));

  return (
    <div className="wp-list-editor">
      <div className="wp-field-label">
        {label}
        <span className="wp-field-hint">{hint}</span>
      </div>
      {items.map((item, i) => (
        <div key={i} className="wp-list-row">
          <input
            type="text"
            value={item}
            onChange={(e) => update(i, e.target.value)}
            placeholder={`Item ${i + 1}…`}
          />
          <button
            type="button"
            className="wp-remove-btn"
            onClick={() => remove(i)}
            aria-label="Remove"
          >
            ×
          </button>
        </div>
      ))}
      <button type="button" className="wp-add-btn" onClick={add}>
        + Add
      </button>
    </div>
  );
}

export function WelcomePurpose({ onSaved }: WelcomePurposeProps) {
  const [step, setStep] = useState<"welcome" | "form">("welcome");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const [fields, setFields] = useState<PurposeFields>({
    name: "",
    description: "",
    functional: [""],
    technical: [""],
    security: [""],
    constraints: [""],
    directives: [""],
  });

  const set = (key: keyof PurposeFields, val: string | string[]) =>
    setFields((f) => ({ ...f, [key]: val }));

  const handleSave = async () => {
    if (!fields.name.trim() || !fields.description.trim()) {
      setError("Name and description are required.");
      return;
    }
    setSaving(true);
    setError(null);
    try {
      const token = getAuthToken();
      const yaml = buildPurposeYaml(fields);
      const res = await fetch("/api/v1/evolution/purpose", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          ...(token ? { Authorization: `Bearer ${token}` } : {}),
        },
        body: JSON.stringify({ version: 1, content_yaml: yaml }),
      });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body.detail || `HTTP ${res.status}`);
      }
      onSaved();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setSaving(false);
    }
  };

  const canSave =
    fields.name.trim() &&
    fields.description.trim() &&
    fields.functional.some((s) => s.trim());

  return (
    <AppWindow
      title={step === "welcome" ? "Welcome to Self-Evolving Software" : "Define Your Purpose"}
      onClose={() => {}}
      width="660px"
      height="580px"
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
            Define Purpose →
          </button>
        </div>
      ) : (
        <div className="wp-form">
          <p className="wp-form-intro">
            Fill in as much as you can. You can always refine it later with an Inception.
          </p>

          <div className="wp-field">
            <label>System name <span className="wp-required">*</span></label>
            <input
              type="text"
              value={fields.name}
              onChange={(e) => set("name", e.target.value)}
              placeholder="e.g. My SaaS Platform"
            />
          </div>

          <div className="wp-field">
            <label>Description <span className="wp-required">*</span></label>
            <textarea
              value={fields.description}
              onChange={(e) => set("description", e.target.value)}
              rows={2}
              placeholder="What does this system do and who is it for?"
            />
          </div>

          <ListEditor
            label="Functional Requirements"
            hint="What must the system do?"
            items={fields.functional}
            onChange={(v) => set("functional", v)}
          />
          <ListEditor
            label="Technical Requirements"
            hint="Tech stack, performance, scalability…"
            items={fields.technical}
            onChange={(v) => set("technical", v)}
          />
          <ListEditor
            label="Security Requirements"
            hint="Auth, encryption, compliance…"
            items={fields.security}
            onChange={(v) => set("security", v)}
          />
          <ListEditor
            label="Constraints"
            hint="Budget, deadlines, stack limitations…"
            items={fields.constraints}
            onChange={(v) => set("constraints", v)}
          />
          <ListEditor
            label="Evolution Directives"
            hint="How should the engine prioritize when evolving?"
            items={fields.directives}
            onChange={(v) => set("directives", v)}
          />

          {error && <div className="wp-error">{error}</div>}

          <div className="wp-form-actions">
            <button className="wp-back-btn" type="button" onClick={() => setStep("welcome")}>
              ← Back
            </button>
            <button
              className="wp-save-btn"
              type="button"
              onClick={handleSave}
              disabled={saving || !canSave}
            >
              {saving ? "Saving…" : "Save Purpose"}
            </button>
          </div>
        </div>
      )}
    </AppWindow>
  );
}
