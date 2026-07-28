/** Mirrors of the backend response models (api/routes/*.py). */

export type TaskStatus =
  | "pending"
  | "planning"
  | "running"
  | "awaiting_approval"
  | "completed"
  | "failed"
  | "cancelled";

export interface Task {
  id: string;
  request: string;
  status: TaskStatus;
  result: string | null;
  require_review: boolean;
}

export interface Approval {
  id: string;
  task_id: string;
  subtask_id: string | null;
  reason: string;
  level: string;
  proposed_action: string;
  context: string;
  decision: string | null;
  created_at: string;
}

/** SSE payloads from GET /tasks/{id}/stream (observability/events.py). */
export type TaskEventType =
  | "task_started"
  | "plan_created"
  | "dispatching"
  | "subtask_completed"
  | "escalation_raised"
  | "task_resumed"
  | "task_finished"
  | "task_failed";

export interface TaskEvent {
  task_id: string;
  type: TaskEventType;
  data: Record<string, unknown>;
  ts: string;
}

export interface Span {
  id: string;
  parent_id: string | null;
  name: string;
  kind: string;
  status: string;
  attributes: Record<string, unknown>;
  started_at: string;
  ended_at: string | null;
  duration_ms: number;
}

export interface CostRow {
  agent_role: string;
  model: string;
  calls: number;
  input_tokens: number;
  output_tokens: number;
  cost_usd: number;
}

export interface CostReport {
  total_usd: number;
  rows: CostRow[];
}
