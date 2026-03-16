import { useState } from "react";
import { AppWindow } from "./components/AppWindow";
import { HealthCheck } from "./components/HealthCheck";
import { LoginScreen } from "./components/LoginScreen";
import { WelcomePurpose } from "./components/WelcomePurpose";
import { EvolutionTimeline } from "./components/evolution/EvolutionTimeline";
import { InceptionPanel } from "./components/evolution/InceptionPanel";
import { PurposeViewer } from "./components/evolution/PurposeViewer";
import { useAuth } from "./hooks/useAuth";
import { useEvolutionStatus } from "./hooks/useEvolutionApi";
import "./App.css";

type WindowId = "inception" | "inceptions" | "timeline" | "purpose" | "health";

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
  const [activeWindow, setActiveWindow] = useState<WindowId | null>(null);
  const [purposeDismissed, setPurposeDismissed] = useState(false);

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

  return (
    <div className="desktop">
      {/* macOS-style menu bar */}
      <div className="menubar">
        <div className="menubar-left">
          <span className="menubar-brand">Self-Evolving Software</span>
          <ToolbarStatus />
        </div>
        <div className="menubar-center">
          <button className="menubar-item" onClick={() => toggle("inception")}>
            New Inception
          </button>
          <button className="menubar-item" onClick={() => toggle("inceptions")}>
            Inceptions
          </button>
          <button className="menubar-item" onClick={() => toggle("timeline")}>
            Timeline
          </button>
          <button className="menubar-item" onClick={() => toggle("purpose")}>
            Purpose
          </button>
          <button className="menubar-item" onClick={() => toggle("health")}>
            Health
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

      {/* Desktop area — just the wallpaper */}
      <div className="desktop-area" />

      {/* Welcome wizard — shown automatically when no Purpose is defined */}
      {noPurpose && !purposeDismissed && (
        <WelcomePurpose
          onSaved={() => {
            refreshStatus();
            setPurposeDismissed(true);
          }}
        />
      )}

      {/* Window — only one at a time */}
      {activeWindow === "inception" && (
        <AppWindow title="New Inception" onClose={close} width="560px" height="420px">
          <InceptionPanel mode="form-only" />
        </AppWindow>
      )}

      {activeWindow === "inceptions" && (
        <AppWindow title="Inception History" onClose={close} width="640px" height="560px">
          <InceptionPanel mode="list-only" />
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

      {activeWindow === "health" && (
        <AppWindow title="System Health" onClose={close} width="480px" height="280px">
          <HealthCheck />
        </AppWindow>
      )}
    </div>
  );
}

export default App;
