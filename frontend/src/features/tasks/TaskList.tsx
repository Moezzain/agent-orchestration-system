import type { Task } from "../../api/types";
import { StatusBadge } from "./StatusBadge";
import "./tasks.css";

interface Props {
  tasks: Task[];
  selectedId: string | null;
  onSelect: (id: string) => void;
}

export function TaskList({ tasks, selectedId, onSelect }: Props) {
  if (tasks.length === 0) {
    return <p className="empty-state">No tasks yet — submit one above.</p>;
  }
  return (
    <ul className="task-list" aria-label="Tasks">
      {tasks.map((task) => (
        <li key={task.id}>
          <button
            className={`task-row ${task.id === selectedId ? "selected" : ""}`}
            onClick={() => onSelect(task.id)}
          >
            <span className="task-request">{task.request}</span>
            <span className="task-meta">
              <code className="task-id">{task.id.slice(0, 8)}</code>
              <StatusBadge status={task.status} />
            </span>
          </button>
        </li>
      ))}
    </ul>
  );
}
