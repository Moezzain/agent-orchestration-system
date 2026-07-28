import { useEffect, useRef, useState } from "react";
import { api } from "../../api/client";
import type { Task, TaskEvent, TaskEventType } from "../../api/types";

/** All SSE event names we subscribe to (server sends named events). */
const EVENT_TYPES: TaskEventType[] = [
  "task_started",
  "plan_created",
  "dispatching",
  "subtask_completed",
  "escalation_raised",
  "task_resumed",
  "task_finished",
  "task_failed",
];

const TERMINAL: ReadonlySet<TaskEventType> = new Set(["task_finished", "task_failed"]);

interface TaskStream {
  task: Task | null;
  events: TaskEvent[];
  /** true while the EventSource is open */
  live: boolean;
}

/**
 * Fetch-then-stream (the step-30 contract): snapshot from REST, deltas via
 * EventSource. The bus has no replay, so on any (re)connect we re-fetch the
 * snapshot; EventSource handles reconnection itself.
 */
export function useTaskStream(taskId: string | null): TaskStream {
  const [task, setTask] = useState<Task | null>(null);
  const [events, setEvents] = useState<TaskEvent[]>([]);
  const [live, setLive] = useState(false);
  // re-fetch the snapshot after terminal events (status/result became final)
  const refetchTimer = useRef<number | null>(null);

  useEffect(() => {
    if (!taskId) return;
    setTask(null);
    setEvents([]);

    let cancelled = false;

    const fetchSnapshot = () =>
      api.getTask(taskId).then(
        (t) => !cancelled && setTask(t),
        () => undefined,
      );

    void fetchSnapshot();

    const source = new EventSource(api.streamUrl(taskId));
    setLive(true);

    const onEvent = (raw: MessageEvent<string>) => {
      const event = JSON.parse(raw.data) as TaskEvent;
      setEvents((prev) => [...prev, event]);
      if (TERMINAL.has(event.type)) {
        // server closes the stream after terminal; pull the final snapshot
        refetchTimer.current = window.setTimeout(() => void fetchSnapshot(), 400);
        setLive(false);
      }
      if (event.type === "escalation_raised" || event.type === "task_resumed") {
        void fetchSnapshot(); // status flips (awaiting_approval/running)
      }
    };

    for (const type of EVENT_TYPES) source.addEventListener(type, onEvent);
    source.onerror = () => setLive(false);
    source.onopen = () => setLive(true);

    return () => {
      cancelled = true;
      source.close();
      if (refetchTimer.current !== null) window.clearTimeout(refetchTimer.current);
      setLive(false);
    };
  }, [taskId]);

  return { task, events, live };
}
