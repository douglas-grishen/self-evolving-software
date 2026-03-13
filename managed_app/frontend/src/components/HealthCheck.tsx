import { useEffect, useState } from "react";

interface HealthData {
  status: string;
  app: string;
  version: string;
  environment: string;
}

export function HealthCheck() {
  const [health, setHealth] = useState<HealthData | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    fetch("/api/v1/health")
      .then((res) => {
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        return res.json();
      })
      .then((data: HealthData) => {
        setHealth(data);
        setError(null);
      })
      .catch((err: Error) => {
        setError(err.message);
        setHealth(null);
      })
      .finally(() => setLoading(false));
  }, []);

  const dotClass = loading ? "loading" : error ? "error" : "";
  const statusText = loading
    ? "Checking..."
    : error
      ? `Unreachable — ${error}`
      : `${health?.status?.toUpperCase()} — ${health?.app} v${health?.version}`;

  return (
    <div className="health-card">
      <h2>Backend Health</h2>
      <div className="health-status">
        <span className={`health-dot ${dotClass}`} />
        <span>{statusText}</span>
      </div>
      {health && (
        <div className="health-meta">
          Environment: {health.environment}
        </div>
      )}
    </div>
  );
}
