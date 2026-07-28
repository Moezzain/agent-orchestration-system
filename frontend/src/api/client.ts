import type { Approval, CostReport, Span, Task } from "./types";

const BASE = import.meta.env.VITE_API_URL ?? "http://localhost:8000";

class ApiError extends Error {
  status: number;

  constructor(status: number, message: string) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${BASE}${path}`, {
    headers: { "content-type": "application/json" },
    ...init,
  });
  if (!response.ok) {
    const detail = await response
      .json()
      .then((b: { detail?: string }) => b.detail)
      .catch(() => undefined);
    throw new ApiError(response.status, detail ?? response.statusText);
  }
  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}

export const api = {
  createTask: (requestText: string, requireReview: boolean) =>
    request<Task>("/tasks", {
      method: "POST",
      body: JSON.stringify({ request: requestText, require_review: requireReview }),
    }),

  getTask: (id: string) => request<Task>(`/tasks/${id}`),

  listTasks: (limit = 50) => request<Task[]>(`/tasks?limit=${limit}`),

  listApprovals: (taskId?: string) =>
    request<Approval[]>(`/approvals${taskId ? `?task_id=${taskId}` : ""}`),

  decideApproval: (id: string, approved: boolean, note = "") =>
    request<Approval>(`/approvals/${id}/decision`, {
      method: "POST",
      body: JSON.stringify({ approved, note }),
    }),

  getSpans: (taskId: string) => request<Span[]>(`/tasks/${taskId}/spans`),

  getCosts: (taskId: string) => request<CostReport>(`/tasks/${taskId}/costs`),

  /** SSE endpoint URL — consumed via EventSource, not fetch. */
  streamUrl: (taskId: string) => `${BASE}/tasks/${taskId}/stream`,
};

export { ApiError };
