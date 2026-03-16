import { usePurpose } from "../../hooks/useEvolutionApi";

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

function parsePurposeYaml(yaml: string): PurposeData | null {
  // Simple YAML-like parser for the structured purpose format
  // This handles the specific structure we produce
  try {
    const lines = yaml.split("\n");
    const data: Record<string, unknown> = {};
    let currentKey = "";
    let currentList: string[] = [];
    let inIdentity = false;
    const identity: Record<string, string> = {};

    for (const line of lines) {
      const trimmed = line.trim();
      if (!trimmed || trimmed.startsWith("#")) continue;

      if (trimmed.startsWith("version:")) {
        data.version = parseInt(trimmed.split(":")[1].trim());
      } else if (trimmed.startsWith("updated_at:")) {
        data.updated_at = trimmed.split(": ", 2)[1]?.replace(/['"]/g, "") || "";
      } else if (trimmed === "identity:") {
        inIdentity = true;
      } else if (inIdentity && trimmed.startsWith("name:")) {
        identity.name = trimmed.split(": ", 2)[1]?.replace(/['"]/g, "") || "";
      } else if (inIdentity && trimmed.startsWith("description:")) {
        identity.description = trimmed.split(": ", 2)[1]?.replace(/['"]/g, "") || "";
      } else if (trimmed.endsWith(":") && !trimmed.startsWith("-")) {
        if (currentKey && currentList.length > 0) {
          data[currentKey] = currentList;
        }
        currentKey = trimmed.slice(0, -1);
        currentList = [];
        inIdentity = false;
      } else if (trimmed.startsWith("- ")) {
        currentList.push(trimmed.slice(2).replace(/['"]/g, ""));
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

export function PurposeViewer() {
  const { purpose, loading, error } = usePurpose();

  if (loading) return <div className="card">Loading purpose...</div>;
  if (error) return <div className="card error-text">Error: {error}</div>;
  if (!purpose) return <div className="card">No purpose defined yet.</div>;

  const parsed = parsePurposeYaml(purpose.content_yaml);

  return (
    <div className="card purpose-viewer">
      <div className="card-header">
        <h3>System Purpose</h3>
        <span className="version-badge">v{purpose.version}</span>
      </div>

      {parsed ? (
        <>
          <div className="purpose-identity">
            <strong>{parsed.identity?.name}</strong>
            <p>{parsed.identity?.description}</p>
          </div>
          <RequirementsList title="Functional Requirements" items={parsed.functional_requirements} />
          <RequirementsList title="Technical Requirements" items={parsed.technical_requirements} />
          <RequirementsList title="Security Requirements" items={parsed.security_requirements} />
          <RequirementsList title="Constraints" items={parsed.constraints} />
          <RequirementsList title="Evolution Directives" items={parsed.evolution_directives} />
        </>
      ) : (
        <pre className="purpose-raw">{purpose.content_yaml}</pre>
      )}

      <div className="purpose-meta">
        Created: {new Date(purpose.created_at).toLocaleString()}
      </div>
    </div>
  );
}
