import { AppBrief } from "../hooks/useAppsApi";

interface DesktopIconProps {
  app: AppBrief;
  onClick: () => void;
}

const statusColors: Record<string, string> = {
  planned: "#94a3b8",   // gray
  building: "#3b82f6",  // blue
  active: "#22c55e",    // green
  archived: "#6b7280",  // dim gray
};

export function DesktopIcon({ app, onClick }: DesktopIconProps) {
  const dotColor = statusColors[app.status] || "#94a3b8";

  return (
    <div
      className="desktop-icon"
      onClick={onClick}
      onDoubleClick={onClick}
      title={app.goal || app.name}
    >
      <div className="desktop-icon-img">
        <span className="desktop-icon-emoji">{app.icon || "\u{1f4e6}"}</span>
        <span className="desktop-icon-dot" style={{ background: dotColor }} />
      </div>
      <span className="desktop-icon-label">{app.name}</span>
    </div>
  );
}
