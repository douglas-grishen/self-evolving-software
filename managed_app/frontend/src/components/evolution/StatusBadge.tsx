interface StatusBadgeProps {
  status: string;
}

const STATUS_COLORS: Record<string, string> = {
  completed: "#22c55e",
  applied: "#22c55e",
  failed: "#ef4444",
  rejected: "#ef4444",
  pending: "#eab308",
  received: "#3b82f6",
  analyzing: "#3b82f6",
  generating: "#3b82f6",
  validating: "#8b5cf6",
  deploying: "#f97316",
  processing: "#3b82f6",
};

export function StatusBadge({ status }: StatusBadgeProps) {
  const color = STATUS_COLORS[status] || "#6b7280";

  return (
    <span
      className="status-badge"
      style={{
        backgroundColor: `${color}20`,
        color,
        border: `1px solid ${color}40`,
      }}
    >
      {status}
    </span>
  );
}
