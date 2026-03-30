import { useState } from "react";
import { useNotifications } from "../hooks/useEvolutionApi";
import "./NotificationsView.css";

function formatTimestamp(iso: string): string {
  return new Intl.DateTimeFormat(undefined, {
    dateStyle: "medium",
    timeStyle: "short",
  }).format(new Date(iso));
}

function severityLabel(severity: string): string {
  return severity.split("_").join(" ");
}

export function NotificationsView() {
  const { notifications, loading, error, acknowledge } = useNotifications();
  const [busyId, setBusyId] = useState<string | null>(null);

  const handleAcknowledge = async (notificationId: string) => {
    try {
      setBusyId(notificationId);
      await acknowledge(notificationId);
    } finally {
      setBusyId(null);
    }
  };

  if (loading) {
    return <div className="notifications-view notifications-view--empty">Loading notifications…</div>;
  }

  if (error) {
    return <div className="notifications-view notifications-view--empty">Failed to load notifications: {error}</div>;
  }

  if (notifications.length === 0) {
    return (
      <div className="notifications-view notifications-view--empty">
        <div>No active notifications.</div>
        <div className="notifications-empty-sub">Severe blockers will appear here until acknowledged.</div>
      </div>
    );
  }

  return (
    <div className="notifications-view">
      {notifications.map((notification) => (
        <article key={notification.id} className={`notification-card notification-card--${notification.severity}`}>
          <div className="notification-card-header">
            <div>
              <div className="notification-card-title">{severityLabel(notification.severity)}</div>
              <div className="notification-card-meta">
                Created {formatTimestamp(notification.created_at)}
              </div>
              <div className="notification-card-meta">
                Updated {formatTimestamp(notification.updated_at)}
              </div>
            </div>
            <div className="notification-card-side">
              <span className="notification-update-count">
                {notification.update_count} update{notification.update_count === 1 ? "" : "s"}
              </span>
              <button
                className="notification-ack-button"
                disabled={busyId === notification.id}
                onClick={() => handleAcknowledge(notification.id)}
              >
                {busyId === notification.id ? "Acknowledging…" : "Acknowledge"}
              </button>
            </div>
          </div>
          <p className="notification-message">{notification.message}</p>
        </article>
      ))}
    </div>
  );
}
