# AI Employee Security Guidance

- Never log API keys, MCP tokens, OAuth tokens, refresh tokens, SSO profiles, or agent bearer tokens.
- Never weaken command policy, approval, autonomy whitelist, or agent ownership checks.
- Do not add scripts or docs that recommend `--no-verify`, disabling security checks, or bypassing approval gates.
- Validate Redis pub/sub payloads from external or agent-controlled sources before persistence.
- Avoid direct SQL string concatenation in `orchestrator/app/`; use SQLAlchemy parameters or ORM queries.
- File delivery must keep workspace paths scoped under `/workspace/` and must not expose host paths.
- When changing auth, token storage, SSO, OAuth, WebSocket tickets, or key management, include a negative test path.
