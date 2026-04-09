import { useState } from "react";
import { AppWindow } from "./components/AppWindow";
import { AppViewer } from "./components/AppViewer";
import { ArchitectureView } from "./components/ArchitectureView";
import { DatabaseView } from "./components/DatabaseView";
import { DesktopIcon } from "./components/DesktopIcon";
import { HealthCheck } from "./components/HealthCheck";
import { LoginScreen } from "./components/LoginScreen";
import { NotificationsView } from "./components/NotificationsView";
import { SettingsView } from "./components/SettingsView";
import { ChatView } from "./components/ChatView";
import { CostView } from "./components/CostView";
import { TasksView } from "./components/TasksView";
import { WelcomePurpose } from "./components/WelcomePurpose";
import { EvolutionTimeline } from "./components/evolution/EvolutionTimeline";
import { InceptionPanel } from "./components/evolution/InceptionPanel";
import { PurposeViewer } from "./components/evolution/PurposeViewer";
import { RealTimeView } from "./components/evolution/RealTimeView";
import { useAuth } from "./hooks/useAuth";
import { useApps } from "./hooks/useAppsApi";
import { useEvolutionStatus } from "./hooks/useEvolutionApi";
import { useSystemInfo } from "./hooks/useSystemInfo";
import "./App.css";

type SystemWindowId =
  | "inceptions"
  | "realtime"
  | "timeline"
  | "purpose"
  | "architecture"
  | "health"
  | "settings"
  | "tasks"
  | "chat"
  | "database"
  | "cost"
  | "notifications";
type WindowId = SystemWindowId | `app:${string}`;

function ToolbarStatus() {
  const { status } = useEvolutionStatus();
  if (!status) return <span className="toolbar-status">...</span>;

  const engineState = status.active_evolutions > 0 ? "evolving" : "idle";
  const dotColor = engineState === "evolving" ? "#3b82f6" : "#22c55e";

  return (
    <span className="toolbar-status">
      <span className="toolbar-dot" style={{ background: dotColor }} />
      {engineState === "evolving" ? "Evolving" : "Idle"}
      {status.pending_inceptions > 0 && (
        <span className="toolbar-badge">{status.pending_inceptions}</span>
      )}
    </span>
  );
}

function App() {
  const auth = useAuth();
  const { status, refresh: refreshStatus } = useEvolutionStatus();
  const { apps } = useApps();
  const { deploy_version, version } = useSystemInfo();
  const [activeWindow, setActiveWindow] = useState<WindowId | null>(null);
  const [purposeDismissed, setPurposeDismissed] = useState(false);

  if (auth.isLoading && auth.token) {
    return (
      <div className="login-screen">
        <div className="login-card">
          <div className="login-header">
            <h1 className="login-title">Self-Evolving Software</h1>
            <p className="login-subtitle">Validating session...</p>
          </div>
        </div>
      </div>
    );
  }

  if (!auth.isAuthenticated) {
    return (
      <LoginScreen
        onLogin={auth.login}
        isLoading={auth.isLoading}
        error={auth.error}
      />
    );
  }

  const noPurpose = status !== null && status.current_purpose_version === null;

  const toggle = (id: WindowId) => {
    setActiveWindow((prev) => (prev === id ? null : id));
  };

  const close = () => {
    setActiveWindow(null);
  };

  // Parse app window ID
  const activeAppId = activeWindow?.startsWith("app:") ? activeWindow.slice(4) : null;
  const activeApp = activeAppId ? apps.find((a) => a.id === activeAppId) : null;

  return (
    <div className="desktop">
      {/* macOS-style menu bar */}
      <div className="menubar">
        <div className="menubar-left">
          <span className="menubar-brand">Self-Evolving Software</span>
          <ToolbarStatus />
        </div>
        <div className="menubar-center">
          <button className="menubar-item" onClick={() => toggle("inceptions")}>
            Inceptions
          </button>
          <button className="menubar-item" onClick={() => toggle("realtime")}>
            Real Time
          </button>
          <button className="menubar-item" onClick={() => toggle("timeline")}>
            Timeline
          </button>
          <button className="menubar-item" onClick={() => toggle("purpose")}>
            Purpose
          </button>
          <button className="menubar-item" onClick={() => toggle("tasks")}>
            Tasks
          </button>
          <button className="menubar-item menubar-item--with-badge" onClick={() => toggle("notifications")}>
            Notifications
            {status && status.active_notifications > 0 && (
              <span className="menubar-item-badge">{status.active_notifications}</span>
            )}
          </button>
          <button className="menubar-item" onClick={() => toggle("chat")}>
            Chat
          </button>
          <button className="menubar-item" onClick={() => toggle("architecture")}>
            Architecture
          </button>
          <button className="menubar-item" onClick={() => toggle("database")}>
            Database
          </button>
          <button className="menubar-item" onClick={() => toggle("cost")}>
            Cost
          </button>
          <button className="menubar-item" onClick={() => toggle("health")}>
            Health
          </button>
          <button className="menubar-item" onClick={() => toggle("settings")}>
            Settings
          </button>
          <button
            className="menubar-item"
            onClick={() => window.open("https://self-evolving.org/?help", "_blank")}
          >
            Help
          </button>
        </div>
        <div className="menubar-right">
          <span className="menubar-user">{auth.user?.username}</span>
          <button className="menubar-logout" onClick={auth.logout}>
            Logout
          </button>
        </div>
      </div>

      {/* Desktop area with app icons */}
      <div className="desktop-area">
        {apps.length > 0 && (
          <div className="desktop-icons-grid">
            {apps.map((app) => (
              <DesktopIcon
                key={app.id}
                app={app}
                onClick={() => toggle(`app:${app.id}`)}
              />
            ))}
          </div>
        )}

        {/* Subtle version — bottom-right corner */}
        <span
          className="desktop-version"
          title={`Version ${version}${deploy_version ? ` (deploy #${deploy_version})` : ''}`}
        >
          v.{version}
        </span>
      </div>

      {/* Welcome wizard — shown automatically when no Purpose is defined */}
      {noPurpose && !purposeDismissed && (
        <WelcomePurpose
          onSaved={() => {
            refreshStatus();
            setPurposeDismissed(true);
          }}
        />
      )}

      {/* System windows */}
      {activeWindow === "inceptions" && (
        <AppWindow title="Inceptions" onClose={close} width="640px" height="560px">
          <InceptionPanel mode="list-only" />
        </AppWindow>
      )}

      {activeWindow === "realtime" && (
        <AppWindow title="Evolution Real Time" onClose={close} width="760px" height="620px">
          <RealTimeView />
        </AppWindow>
      )}

      {activeWindow === "timeline" && (
        <AppWindow title="Evolution Timeline" onClose={close} width="720px" height="600px">
          <EvolutionTimeline />
        </AppWindow>
      )}

      {activeWindow === "purpose" && (
        <AppWindow title="System Purpose" onClose={close} width="640px" height="560px">
          <PurposeViewer />
        </AppWindow>
      )}

      {activeWindow === "tasks" && (
        <AppWindow title="Evolution Tasks" onClose={close} width="680px" height="580px">
          <TasksView />
        </AppWindow>
      )}

      {activeWindow === "notifications" && (
        <AppWindow title="System Notifications" onClose={close} width="640px" height="520px">
          <NotificationsView />
        </AppWindow>
      )}

      {activeWindow === "chat" && (
        <AppWindow title="✦ Chat with the System" onClose={close} width="640px" height="560px">
          <ChatView />
        </AppWindow>
      )}

      {activeWindow === "architecture" && (
        <AppWindow title="System Architecture" onClose={close} width="760px" height="580px">
          <ArchitectureView />
        </AppWindow>
      )}

      {activeWindow === "database" && (
        <AppWindow title="Database Schema" onClose={close} width="800px" height="580px">
          <DatabaseView />
        </AppWindow>
      )}

      {activeWindow === "cost" && (
        <AppWindow title="Cost & Usage" onClose={close} width="720px" height="520px">
          <CostView />
        </AppWindow>
      )}

      {activeWindow === "health" && (
        <AppWindow title="System Health" onClose={close} width="480px" height="280px">
          <HealthCheck />
        </AppWindow>
      )}

      {activeWindow === "settings" && (
        <AppWindow title="Settings" onClose={close} width="920px" height="680px">
          <SettingsView />
        </AppWindow>
      )}

      {/* App windows — dynamically opened from desktop icons */}
      {activeAppId && activeApp && (
        <AppWindow
          title={`${activeApp.icon} ${activeApp.name}`}
          onClose={close}
          width="680px"
          height="560px"
        >
          <AppViewer appId={activeAppId} />
        </AppWindow>
      )}
    </div>
  );
}

export default App;
