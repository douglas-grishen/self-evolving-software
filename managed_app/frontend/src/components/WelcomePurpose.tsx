import { useState } from "react";
import { AppWindow } from "./AppWindow";
import { fetchWithAuth } from "../hooks/useAuth";

interface WelcomePurposeProps {
  onSaved: () => void;
}

export function WelcomePurpose({ onSaved }: WelcomePurposeProps) {
  const [purposeText, setPurposeText] = useState("");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const normalizedPurpose = purposeText.trim();

  const handleSave = async () => {
    if (!normalizedPurpose) {
      setError("Purpose is required.");
      return;
    }

    setSaving(true);
    setError(null);
    try {
      const res = await fetchWithAuth("/api/v1/evolution/purpose", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
        },
        body: JSON.stringify({ version: 1, purpose_text: normalizedPurpose }),
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
    <AppWindow title="Define Your Purpose" onClose={() => {}} width="720px" height="560px">
      <div className="wp-form">
        <div className="wp-welcome">
          <div className="wp-section">
            <h3>One text only</h3>
            <p>
              Describe in plain language what this system is for, who it serves,
              and any important constraints it must respect. No YAML and no
              separate requirement lists.
            </p>
          </div>

          <div className="wp-tips">
            <h3>What to include</h3>
            <div className="wp-tips-grid">
              <div className="wp-tip">
                <span>What outcome the software should create.</span>
              </div>
              <div className="wp-tip">
                <span>Who the main users or operators are.</span>
              </div>
              <div className="wp-tip">
                <span>Important limits like safety, budget, or compliance.</span>
              </div>
              <div className="wp-tip">
                <span>You can refine it later through Inceptions.</span>
              </div>
            </div>
          </div>
        </div>

        <div className="wp-field">
          <label>
            Purpose <span className="wp-required">*</span>
          </label>
          <p className="wp-field-help">
            Example: This system should help independent clinics manage patient
            scheduling and billing with strong auditability, clear operator
            controls, and strict protection of sensitive health data.
          </p>
          <textarea
            className="wp-purpose-editor"
            value={purposeText}
            onChange={(e) => {
              setPurposeText(e.target.value);
              if (error) setError(null);
            }}
            placeholder="Describe the system in one text block..."
            rows={12}
            spellCheck
            style={{
              fontFamily: "inherit",
              lineHeight: 1.6,
              minHeight: "260px",
              whiteSpace: "pre-wrap",
            }}
          />
        </div>

        {error && <div className="wp-error">{error}</div>}

        <div className="wp-form-actions">
          <span className="wp-field-help">
            The platform will convert this brief into the structured Purpose it
            uses internally.
          </span>
          <button
            className="wp-save-btn"
            type="button"
            onClick={handleSave}
            disabled={saving || !normalizedPurpose}
          >
            {saving ? "Saving…" : "Save Purpose"}
          </button>
        </div>
      </div>
    </AppWindow>
  );
}
