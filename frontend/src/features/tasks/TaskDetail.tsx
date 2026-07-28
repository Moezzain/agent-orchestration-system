import type { TaskEvent } from "../../api/types";
import { TracePanel } from "../traces/TracePanel";
import { StatusBadge } from "./StatusBadge";
import { useTaskStream } from "./useTaskStream";
import "./tasks.css";

interface PlanSubtask {
  id: string;
  specialist: string;
  description: string;
}

interface SubtaskOutcome {
  status: string;
  score: number;
  confidence: number;
  revisions: number;
}

/** Fold the event log into render state — events are the source of truth
 * for in-flight progress; the snapshot covers anything missed. */
function foldEvents(events: TaskEvent[]) {
  let plan: PlanSubtask[] | null = null;
  let memoriesUsed = 0;
  const outcomes = new Map<string, SubtaskOutcome>();
  let escalation: Record<string, unknown> | null = null;

  for (const event of events) {
    switch (event.type) {
      case "plan_created":
        plan = event.data.subtasks as PlanSubtask[];
        memoriesUsed = (event.data.memories_used as number) ?? 0;
        break;
      case "subtask_completed":
        outcomes.set(event.data.subtask_id as string, {
          status: event.data.status as string,
          score: event.data.score as number,
          confidence: event.data.confidence as number,
          revisions: (event.data.revisions as number) ?? 0,
        });
        break;
      case "escalation_raised":
        escalation = event.data;
        break;
      case "task_resumed":
        escalation = null;
        break;
    }
  }
  return { plan, memoriesUsed, outcomes, escalation };
}

const OUTCOME_TONE: Record<string, string> = {
  accepted: "accent",
  accepted_with_issues: "warn",
  escalated: "warn",
  failed: "danger",
};

export function TaskDetail({ taskId }: { taskId: string }) {
  const { task, events, live } = useTaskStream(taskId);
  const { plan, memoriesUsed, outcomes, escalation } = foldEvents(events);

  if (!task) return <p className="empty-state">loading…</p>;

  return (
    <section className="task-detail" aria-label="Task detail">
      <header className="detail-header">
        <h2 className="mono">{task.id.slice(0, 8)}</h2>
        <span className={`live-dot ${live ? "on" : ""}`} title={live ? "live" : "not streaming"} />
        <StatusBadge status={task.status} />
      </header>
      <p className="detail-request">{task.request}</p>

      {escalation && (
        <aside className="escalation-banner" role="alert">
          <strong>needs human decision</strong>
          <span>
            {String(escalation.reason ?? "")} — {String(escalation.proposed_action ?? "")}
          </span>
          <span className="dim">decide in the approvals queue below</span>
        </aside>
      )}

      {plan && (
        <div className="plan">
          <h3>
            plan{" "}
            {memoriesUsed > 0 && (
              <span className="memory-note">· informed by {memoriesUsed} memories</span>
            )}
          </h3>
          <ul className="plan-list">
            {plan.map((sub) => {
              const outcome = outcomes.get(sub.id);
              return (
                <li key={sub.id} className="plan-row">
                  <code>{sub.id}</code>
                  <span className="plan-spec">{sub.specialist}</span>
                  <span className="plan-desc">{sub.description}</span>
                  {outcome ? (
                    <span
                      className={`status-badge tone-${OUTCOME_TONE[outcome.status] ?? "dim"}`}
                    >
                      {outcome.status.replaceAll("_", " ")} · s{outcome.score.toFixed(2)} · c
                      {outcome.confidence.toFixed(2)}
                      {outcome.revisions > 0 && ` · r${outcome.revisions}`}
                    </span>
                  ) : (
                    <span className="status-badge tone-dim">queued</span>
                  )}
                </li>
              );
            })}
          </ul>
        </div>
      )}

      {task.result && (
        <div className="result">
          <h3>output</h3>
          <pre className="result-body">{task.result}</pre>
        </div>
      )}

      <TracePanel taskId={task.id} />
    </section>
  );
}
