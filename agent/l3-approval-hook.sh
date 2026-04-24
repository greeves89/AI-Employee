#!/bin/bash
# PreToolUse hook for L3 autonomy level.
# Called by Claude Code before every tool use.
# stdin: JSON with tool_name and tool_input
# exit 0 = allow, exit 2 = block

TOOL_INPUT=$(cat)
TOOL_NAME=$(echo "$TOOL_INPUT" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('tool_name','Unknown'))" 2>/dev/null || echo "Unknown")

# Always-safe tools — no approval needed
case "$TOOL_NAME" in
  Read|Glob|Grep|LS|WebSearch|TodoRead|TodoWrite|memory_search|memory_save|\
  knowledge_search|notify_user|rate_task|skill_search|skill_rate|skill_propose|\
  list_todos|send_telegram)
    exit 0
    ;;
esac

# All other tools (Bash, Write, Edit, WebFetch, MultiEdit, etc.) need approval
TOOL_INPUT_SAFE=$(echo "$TOOL_INPUT" | python3 -c "
import sys,json
d=json.load(sys.stdin)
inp = d.get('tool_input', {})
print(json.dumps(inp)[:400].replace('\"','\\\"'))
" 2>/dev/null || echo "{}")

APPROVAL_ID=""
for attempt in 1 2 3; do
  RESPONSE=$(curl -s --max-time 5 -X POST "${ORCHESTRATOR_URL:-http://orchestrator:8000}/api/v1/approvals/request" \
    -H "Content-Type: application/json" \
    -H "Authorization: Bearer ${AGENT_TOKEN}" \
    -H "X-Agent-ID: ${AGENT_ID}" \
    -d "{\"tool\": \"$TOOL_NAME\", \"input\": {\"details\": \"$TOOL_INPUT_SAFE\"}, \"reasoning\": \"Agent (L3) requests permission to use $TOOL_NAME\", \"risk_level\": \"high\"}" 2>/dev/null)
  APPROVAL_ID=$(echo "$RESPONSE" | python3 -c "import sys,json; print(json.load(sys.stdin).get('approval_id',''))" 2>/dev/null)
  [ -n "$APPROVAL_ID" ] && break
  sleep $((2 ** (attempt - 1)))  # 1s, 2s, 4s backoff
done

if [ -z "$APPROVAL_ID" ]; then
  echo "Could not create approval request after 3 attempts. Action blocked." >&2
  exit 2
fi

# Poll up to 10 minutes
for i in $(seq 1 150); do
  sleep 4
  STATUS=$(curl -s "${ORCHESTRATOR_URL:-http://orchestrator:8000}/api/v1/approvals/check/${APPROVAL_ID}" \
    -H "Authorization: Bearer ${AGENT_TOKEN}" \
    -H "X-Agent-ID: ${AGENT_ID}" | python3 -c "import sys,json; print(json.load(sys.stdin).get('status','pending'))" 2>/dev/null)
  case "$STATUS" in
    approved) exit 0 ;;
    denied)   echo "Action denied by user." >&2; exit 2 ;;
  esac
done

echo "Approval timed out after 10 minutes. Action blocked." >&2
exit 2
