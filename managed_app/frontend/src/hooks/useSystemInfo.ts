import { useEffect, useState } from "react";

interface SystemInfo {
  deploy_version: number;
  version?: string;
}

/**
 * Polls /api/v1/system/info every 30 seconds to get the current version.
 */
export function useSystemInfo(): SystemInfo {
  const [info, setInfo] = useState<SystemInfo>({ deploy_version: 0, version: "0.0.0" });

  useEffect(() => {
    let cancelled = false;

    async function fetch() {
      try {
        const res = await window.fetch("/api/v1/system/info");
        if (!res.ok) return;
        const data: SystemInfo = await res.json();
        if (!cancelled) setInfo(data);
      } catch {
        // silently ignore — version badge is non-critical
      }
    }

    fetch();
    const id = setInterval(fetch, 30_000);
    return () => {
      cancelled = true;
      clearInterval(id);
    };
  }, []);

  return info;
}
