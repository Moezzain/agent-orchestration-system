import type { TaskStatus } from "../../api/types";
import "./tasks.css";

const STATUS_TONE: Record<TaskStatus, string> = {
  pending: "dim",
  planning: "info",
  running: "accent",
  awaiting_approval: "warn",
  completed: "accent",
  failed: "danger",
  cancelled: "dim",
};

export function StatusBadge({ status }: { status: TaskStatus }) {
  return (
    <span className={`status-badge tone-${STATUS_TONE[status]}`} data-status={status}>
      {status === "awaiting_approval" ? "needs human" : status.replace("_", " ")}
    </span>
  );
}
