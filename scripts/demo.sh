#!/usr/bin/env bash
# Golden-path demo: submit -> pause for plan review -> approve -> complete.
# Needs the stack running (docker compose up -d) and jq installed.
set -euo pipefail

API="${API_URL:-http://localhost:8000}"
REQUEST="${1:-Compute 17*23 with the code sandbox, then write one sentence stating the result.}"

command -v jq >/dev/null || { echo "jq required (brew install jq)"; exit 1; }
curl -sf "$API/health" >/dev/null || { echo "API not reachable at $API — run: docker compose up -d"; exit 1; }

echo "── 1. submit task (require_review=true forces the plan gate to pause)"
TASK_ID=$(curl -sf -X POST "$API/tasks" -H 'content-type: application/json' \
  -d "{\"request\": $(jq -Rn --arg r "$REQUEST" '$r'), \"require_review\": true}" | jq -r .id)
echo "   task: $TASK_ID"
echo "   watch live: open http://localhost:5173 (or curl -N $API/tasks/$TASK_ID/stream)"

echo "── 2. wait for the graph to pause at the plan gate..."
for _ in $(seq 1 60); do
  STATUS=$(curl -sf "$API/tasks/$TASK_ID" | jq -r .status)
  [ "$STATUS" = "awaiting_approval" ] && break
  [ "$STATUS" = "failed" ] && { echo "   task failed:"; curl -sf "$API/tasks/$TASK_ID" | jq .; exit 1; }
  printf '   status: %s\r' "$STATUS"
  sleep 2
done
[ "$STATUS" = "awaiting_approval" ] || { echo "   never paused (status: $STATUS)"; exit 1; }
echo "   status: awaiting_approval — the celery task has ENDED; the pause lives in postgres."

APPROVAL=$(curl -sf "$API/approvals?task_id=$TASK_ID" | jq '.[0]')
APPROVAL_ID=$(echo "$APPROVAL" | jq -r .id)
echo "── 3. pending approval:"
echo "$APPROVAL" | jq '{reason, level, proposed_action, context}'

read -r -p "── 4. approve? [Y/n] " ANSWER
APPROVED=$([ "${ANSWER:-y}" = "n" ] && echo false || echo true)
curl -sf -X POST "$API/approvals/$APPROVAL_ID/decision" -H 'content-type: application/json' \
  -d "{\"approved\": $APPROVED, \"note\": \"demo decision\"}" >/dev/null
echo "   decision recorded — resume task enqueued, graph continues from its checkpoint."

echo "── 5. wait for completion..."
for _ in $(seq 1 90); do
  STATUS=$(curl -sf "$API/tasks/$TASK_ID" | jq -r .status)
  case "$STATUS" in completed|failed) break ;; esac
  printf '   status: %s\r' "$STATUS"
  sleep 2
done
echo "   status: $STATUS"

echo "── 6. result:"
curl -sf "$API/tasks/$TASK_ID" | jq -r .result

echo "── 7. cost breakdown:"
curl -sf "$API/tasks/$TASK_ID/costs" | jq .

echo "── done. span tree: $API/tasks/$TASK_ID/spans · UI: http://localhost:5173"
