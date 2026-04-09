import { usePurpose, useTriggerAnalysis } from "../../hooks/useEvolutionApi";

const PLAIN_TEXT_PURPOSE_NAME = "User-Defined Purpose";

interface PurposeData {
  version: number;
  updated_at: string;
  identity: { name: string; description: string };
  functional_requirements: string[];
  technical_requirements: string[];
  security_requirements: string[];
  constraints: string[];
  evolution_directives: string[];
}

function stripYamlQuotes(value: string): string {
  return value.replace(/['"]/g, "");
}

function parsePurposeYaml(yaml: string): PurposeData | null {
  // Simple YAML-like parser for the structured purpose format
  // This handles the specific structure we produce
  try {
    const lines = yaml.split("\n");
    const data: Record<string, unknown> = {
      functional_requirements: [],
      technical_requirements: [],
      security_requirements: [],
      constraints: [],
      evolution_directives: [],
    };
    let currentKey = "";
    let currentList: string[] = [];
    let inIdentity = false;
    let currentIdentityField: "name" | "description" | "" = "";
    const identity: Record<string, string> = { name: "", description: "" };

    for (const line of lines) {
      const indent = line.length - line.trimStart().length;
      const trimmed = line.trim();
      if (!trimmed || trimmed.startsWith("#")) continue;

      if (trimmed.startsWith("version:")) {
        data.version = parseInt(trimmed.split(":")[1].trim());
        currentIdentityField = "";
      } else if (trimmed.startsWith("updated_at:")) {
        data.updated_at = stripYamlQuotes(trimmed.split(": ", 2)[1] || "");
        currentIdentityField = "";
      } else if (trimmed === "identity:") {
        inIdentity = true;
        currentIdentityField = "";
      } else if (inIdentity && trimmed.startsWith("name:")) {
        identity.name = stripYamlQuotes(trimmed.split(": ", 2)[1] || "");
        currentIdentityField = "name";
      } else if (inIdentity && trimmed.startsWith("description:")) {
        identity.description = stripYamlQuotes(trimmed.split(": ", 2)[1] || "");
        currentIdentityField = "description";
      } else if (inIdentity && currentIdentityField && indent >= 4 && !trimmed.includes(":")) {
        identity[currentIdentityField] = `${identity[currentIdentityField]} ${stripYamlQuotes(trimmed)}`.trim();
      } else if (trimmed.endsWith(":") && !trimmed.startsWith("-")) {
        if (currentKey && currentList.length > 0) {
          data[currentKey] = currentList;
        }
        currentKey = trimmed.slice(0, -1);
        currentList = [];
        inIdentity = false;
        currentIdentityField = "";
      } else if (trimmed.endsWith(": []")) {
        const key = trimmed.slice(0, -4);
        data[key] = [];
        currentKey = "";
        currentList = [];
        inIdentity = false;
        currentIdentityField = "";
      } else if (trimmed.startsWith("- ")) {
        currentList.push(stripYamlQuotes(trimmed.slice(2)));
      } else {
        currentIdentityField = "";
      }
    }
    if (currentKey && currentList.length > 0) {
      data[currentKey] = currentList;
    }
    data.identity = identity;

    return data as unknown as PurposeData;
  } catch {
    return null;
  }
}

function RequirementsList({ title, items }: { title: string; items: string[] }) {
  if (!items || items.length === 0) return null;
  return (
    <div className="purpose-section">
      <h4>{title}</h4>
      <ul>
        {items.map((item, i) => (
          <li key={i}>{item}</li>
        ))}
      </ul>
    </div>
  );
}

function cleanedPurposeDescription(parsed: PurposeData): string {
  const description = (parsed.identity?.description || "").trim();
  const name = (parsed.identity?.name || "").trim();
  if (!description || !name) return description;

  const normalizedDescription = description.toLowerCase();
  const normalizedName = name.toLowerCase();
  if (normalizedDescription === normalizedName) {
    return "";
  }
  if (normalizedDescription.startsWith(`${normalizedName} `)) {
    return description.slice(name.length).trimStart();
  }
  return description;
}

function isPlainTextPurpose(parsed: PurposeData | null): boolean {
  if (!parsed) return false;
  return (
    parsed.identity?.name === PLAIN_TEXT_PURPOSE_NAME &&
    parsed.functional_requirements.length === 0 &&
    parsed.technical_requirements.length === 0 &&
    parsed.security_requirements.length === 0 &&
    parsed.constraints.length === 0 &&
    parsed.evolution_directives.length === 0
  );
}

export function PurposeViewer() {
  const { purpose, loading, error } = usePurpose();
  const { trigger, triggering, triggered, error: triggerError } = useTriggerAnalysis();

  if (loading) return <div className="card">Loading purpose...</div>;
  if (error) return <div className="card error-text">Error: {error}</div>;
  if (!purpose) return <div className="card">No purpose defined yet.</div>;

  const parsed = parsePurposeYaml(purpose.content_yaml);
  const plainTextPurpose = isPlainTextPurpose(parsed);
  const description = parsed ? cleanedPurposeDescription(parsed) : "";

  return (
    <div className="card purpose-viewer">
      <div className="card-header">
        <h3>System Purpose</h3>
        <span className="version-badge">v{purpose.version}</span>
      </div>

      {parsed ? (
        <>
          <div className="purpose-identity">
            {!plainTextPurpose && <strong>{parsed.identity?.name}</strong>}
            {description && <p>{description}</p>}
          </div>
          {!plainTextPurpose && (
            <>
              <RequirementsList title="Functional Requirements" items={parsed.functional_requirements} />
              <RequirementsList title="Technical Requirements" items={parsed.technical_requirements} />
              <RequirementsList title="Security Requirements" items={parsed.security_requirements} />
              <RequirementsList title="Constraints" items={parsed.constraints} />
              <RequirementsList title="Evolution Directives" items={parsed.evolution_directives} />
            </>
          )}
        </>
      ) : (
        <pre className="purpose-raw">{purpose.content_yaml}</pre>
      )}

      <div className="purpose-actions">
        <button
          className="run-analysis-btn"
          onClick={trigger}
          disabled={triggering}
        >
          {triggering ? "Triggering..." : triggered ? "Analysis Triggered!" : "Run Analysis Now"}
        </button>
        {triggerError && <span className="error-text" style={{ fontSize: "0.85rem" }}>Error: {triggerError}</span>}
        {triggered && <span className="success-text" style={{ fontSize: "0.85rem" }}>Engine will run proactive analysis on next cycle.</span>}
      </div>

      <div className="purpose-meta">
        Created: {new Date(purpose.created_at).toLocaleString()}
      </div>
    </div>
  );
}
