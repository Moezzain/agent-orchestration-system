import { useState } from "react";
import { api } from "../../api/client";
import type { Task } from "../../api/types";
import "./tasks.css";

export function NewTaskForm({ onCreated }: { onCreated: (task: Task) => void }) {
  const [text, setText] = useState("");
  const [requireReview, setRequireReview] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    if (!text.trim() || submitting) return;
    setSubmitting(true);
    setError(null);
    try {
      const task = await api.createTask(text.trim(), requireReview);
      setText("");
      onCreated(task);
    } catch (e) {
      setError(e instanceof Error ? e.message : "request failed");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <form className="new-task" onSubmit={submit} aria-label="Submit a task">
      <textarea
        value={text}
        onChange={(e) => setText(e.target.value)}
        placeholder="Describe a task for the agent team… e.g. 'Research X, compute Y, write a summary.'"
        rows={3}
        disabled={submitting}
      />
      <div className="new-task-row">
        <label className="require-review">
          <input
            type="checkbox"
            checked={requireReview}
            onChange={(e) => setRequireReview(e.target.checked)}
          />
          require plan approval before any work
        </label>
        <button className="primary" type="submit" disabled={submitting || !text.trim()}>
          {submitting ? "submitting…" : "run task"}
        </button>
      </div>
      {error && <p className="form-error">{error}</p>}
    </form>
  );
}
