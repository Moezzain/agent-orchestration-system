import { useCallback, useEffect, useState } from "react";
import { api, ApiError } from "../../api/client";
import type { Approval } from "../../api/types";
import "./approvals.css";

const REFRESH_MS = 5000;

interface Props {
  /** notify the app so task list/detail can refresh after a decision */
  onDecided?: () => void;
}

export function ApprovalQueue({ onDecided }: Props) {
  const [approvals, setApprovals] = useState<Approval[]>([]);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    try {
      setApprovals(await api.listApprovals());
    } catch {
      // queue is non-critical chrome; the form-level error covers actions
    }
  }, []);

  useEffect(() => {
    void refresh();
    const timer = setInterval(() => void refresh(), REFRESH_MS);
    return () => clearInterval(timer);
  }, [refresh]);

  async function decide(approval: Approval, approved: boolean, note: string) {
    setBusyId(approval.id);
    setError(null);
    try {
      await api.decideApproval(approval.id, approved, note);
      setApprovals((prev) => prev.filter((a) => a.id !== approval.id));
      onDecided?.();
    } catch (e) {
      if (e instanceof ApiError && e.status === 409) {
        // someone else decided it — drop it from the queue quietly
        setApprovals((prev) => prev.filter((a) => a.id !== approval.id));
      } else {
        setError(e instanceof Error ? e.message : "decision failed");
      }
    } finally {
      setBusyId(null);
    }
  }

  if (approvals.length === 0) return null;

  return (
    <section className="approval-queue" aria-label="Pending approvals">
      <h2>
        needs human <span className="queue-count">{approvals.length}</span>
      </h2>
      {error && <p className="form-error">{error}</p>}
      <ul className="approval-list">
        {approvals.map((approval) => (
          <ApprovalCard
            key={approval.id}
            approval={approval}
            busy={busyId === approval.id}
            onDecide={(approved, note) => void decide(approval, approved, note)}
          />
        ))}
      </ul>
    </section>
  );
}

interface CardProps {
  approval: Approval;
  busy: boolean;
  onDecide: (approved: boolean, note: string) => void;
}

function ApprovalCard({ approval, busy, onDecide }: CardProps) {
  const [note, setNote] = useState("");
  const [expanded, setExpanded] = useState(false);

  return (
    <li className="approval-card">
      <div className="approval-head">
        <span className="approval-level">{approval.level.replaceAll("_", " ")}</span>
        <span className="approval-reason">{approval.reason.replaceAll("_", " ")}</span>
        <code className="task-id">task {approval.task_id.slice(0, 8)}</code>
        {approval.subtask_id && <code className="task-id">{approval.subtask_id}</code>}
      </div>

      <p className="approval-action">{approval.proposed_action}</p>

      {approval.context && (
        <>
          <button
            className="context-toggle"
            onClick={() => setExpanded((v) => !v)}
            aria-expanded={expanded}
          >
            {expanded ? "hide context" : "show context"}
          </button>
          {expanded && <pre className="approval-context">{approval.context}</pre>}
        </>
      )}

      <div className="approval-actions">
        <input
          type="text"
          placeholder="note for the agent (optional — sent as guidance on reject)"
          value={note}
          onChange={(e) => setNote(e.target.value)}
          disabled={busy}
        />
        <button className="primary" disabled={busy} onClick={() => onDecide(true, note)}>
          approve
        </button>
        <button className="danger" disabled={busy} onClick={() => onDecide(false, note)}>
          reject
        </button>
      </div>
    </li>
  );
}
