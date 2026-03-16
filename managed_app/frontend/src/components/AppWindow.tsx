import { useEffect, useRef } from "react";

interface AppWindowProps {
  title: string;
  onClose: () => void;
  children: React.ReactNode;
  width?: string;
  height?: string;
}

export function AppWindow({
  title,
  onClose,
  children,
  width = "680px",
  height = "520px",
}: AppWindowProps) {
  const overlayRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const handleEsc = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", handleEsc);
    return () => window.removeEventListener("keydown", handleEsc);
  }, [onClose]);

  const handleOverlayClick = (e: React.MouseEvent) => {
    if (e.target === overlayRef.current) onClose();
  };

  return (
    <div className="window-overlay" ref={overlayRef} onClick={handleOverlayClick}>
      <div className="window" style={{ width, maxHeight: height }}>
        <div className="window-titlebar">
          <div className="window-controls">
            <button
              className="window-btn window-btn-close"
              onClick={onClose}
              aria-label="Close"
            />
            <span className="window-btn window-btn-minimize" />
            <span className="window-btn window-btn-maximize" />
          </div>
          <span className="window-title">{title}</span>
          <div className="window-controls-spacer" />
        </div>
        <div className="window-body">{children}</div>
      </div>
    </div>
  );
}
