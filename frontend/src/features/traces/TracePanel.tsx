import { useEffect, useState } from "react";
import { api } from "../../api/client";
import type { CostReport, Span } from "../../api/types";
import "./traces.css";

interface TreeNode {
  span: Span;
  children: TreeNode[];
}

function buildTree(spans: Span[]): TreeNode[] {
  const nodes = new Map<string, TreeNode>(
    spans.map((span) => [span.id, { span, children: [] }]),
  );
  const roots: TreeNode[] = [];
  for (const node of nodes.values()) {
    const parent = node.span.parent_id ? nodes.get(node.span.parent_id) : undefined;
    if (parent) parent.children.push(node);
    else roots.push(node);
  }
  return roots;
}

const KIND_TONE: Record<string, string> = {
  graph_node: "info",
  llm_call: "accent",
  tool_call: "warn",
};

function SpanRow({ node, depth, maxMs }: { node: TreeNode; depth: number; maxMs: number }) {
  const { span, children } = node;
  const width = maxMs > 0 ? Math.max(2, (span.duration_ms / maxMs) * 100) : 2;
  return (
    <>
      <div className="span-row" style={{ paddingLeft: `${depth * 1.25}rem` }}>
        <span className={`span-name tone-${KIND_TONE[span.kind] ?? "dim"}`}>{span.name}</span>
        <span className="span-attrs">
          {span.kind === "llm_call" &&
            `${String(span.attributes["llm.model"] ?? "")} · ${String(
              span.attributes["llm.input_tokens"] ?? "?",
            )}→${String(span.attributes["llm.output_tokens"] ?? "?")} tok`}
          {span.kind === "tool_call" &&
            (span.attributes["tool.success"] === false ? "failed" : "")}
        </span>
        <span className="span-duration">
          <span className="span-bar" style={{ width: `${width}%` }} />
          <span className="span-ms">{span.duration_ms}ms</span>
        </span>
      </div>
      {children.map((child) => (
        <SpanRow key={child.span.id} node={child} depth={depth + 1} maxMs={maxMs} />
      ))}
    </>
  );
}

export function TracePanel({ taskId }: { taskId: string }) {
  const [open, setOpen] = useState(false);
  const [spans, setSpans] = useState<Span[] | null>(null);
  const [costs, setCosts] = useState<CostReport | null>(null);

  useEffect(() => {
    if (!open) return;
    void api.getSpans(taskId).then(setSpans, () => setSpans([]));
    void api.getCosts(taskId).then(setCosts, () => setCosts(null));
  }, [open, taskId]);

  return (
    <div className="trace-panel">
      <button className="context-toggle" onClick={() => setOpen((v) => !v)} aria-expanded={open}>
        {open ? "hide trace & cost" : "show trace & cost"}
      </button>

      {open && costs && (
        <div className="cost-report">
          <h3>
            cost <span className="cost-total">${costs.total_usd.toFixed(4)}</span>
          </h3>
          {costs.rows.length > 0 && (
            <table className="cost-table">
              <thead>
                <tr>
                  <th>role</th>
                  <th>model</th>
                  <th>calls</th>
                  <th>tokens in→out</th>
                  <th>cost</th>
                </tr>
              </thead>
              <tbody>
                {costs.rows.map((row) => (
                  <tr key={`${row.agent_role}-${row.model}`}>
                    <td>{row.agent_role}</td>
                    <td className="mono">{row.model}</td>
                    <td>{row.calls}</td>
                    <td className="mono">
                      {row.input_tokens}→{row.output_tokens}
                    </td>
                    <td className="mono">${row.cost_usd.toFixed(4)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      )}

      {open && spans && spans.length > 0 && (
        <div className="span-tree">
          <h3>trace</h3>
          {buildTree(spans).map((root) => (
            <SpanRow
              key={root.span.id}
              node={root}
              depth={0}
              maxMs={Math.max(...spans.map((s) => s.duration_ms))}
            />
          ))}
        </div>
      )}
      {open && spans && spans.length === 0 && (
        <p className="empty-state">no spans recorded for this task</p>
      )}
    </div>
  );
}
