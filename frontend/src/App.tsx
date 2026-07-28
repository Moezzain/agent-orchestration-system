import { useCallback, useEffect, useState } from "react";
import { api } from "./api/client";
import type { Task } from "./api/types";
import { ApprovalQueue } from "./features/approvals/ApprovalQueue";
import { NewTaskForm } from "./features/tasks/NewTaskForm";
import { TaskDetail } from "./features/tasks/TaskDetail";
import { TaskList } from "./features/tasks/TaskList";

const REFRESH_MS = 4000;

export default function App() {
  const [tasks, setTasks] = useState<Task[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    try {
      setTasks(await api.listTasks());
      setLoadError(null);
    } catch (e) {
      setLoadError(e instanceof Error ? e.message : "failed to load tasks");
    }
  }, []);

  // polling keeps the list fresh; per-task live detail arrives via SSE (step 34)
  useEffect(() => {
    void refresh();
    const timer = setInterval(() => void refresh(), REFRESH_MS);
    return () => clearInterval(timer);
  }, [refresh]);

  return (
    <main>
      <header style={{ marginBottom: "var(--space-4)" }}>
        <h1>agent orchestrator</h1>
        <p style={{ color: "var(--text-dim)", margin: 0 }}>
          supervisor · specialists · reviewer · human-in-the-loop
        </p>
      </header>

      <NewTaskForm
        onCreated={(task) => {
          setTasks((prev) => [task, ...prev]);
          setSelectedId(task.id);
        }}
      />

      {loadError && <p className="form-error">api unreachable: {loadError}</p>}

      <ApprovalQueue onDecided={() => void refresh()} />

      <TaskList tasks={tasks} selectedId={selectedId} onSelect={setSelectedId} />

      {selectedId && <TaskDetail key={selectedId} taskId={selectedId} />}
    </main>
  );
}
