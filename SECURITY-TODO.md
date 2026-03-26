# Security Audit TODO

Audit durchgefuehrt am 2026-03-01. Findings nach Severity sortiert.
Fixes angewendet am 2026-03-26.

## CRITICAL

- [ ] **Docker Socket RW** (`docker-compose.yml:79`) - Orchestrator hat vollen Host-Zugriff via Docker Socket. Fix: Docker Socket Proxy (Tecnativa/docker-socket-proxy) einsetzen.
- [ ] **Docker Apps Container Escape** (`orchestrator/app/api/docker_apps.py:82-87`) - Compose-Runner bekommt Docker Socket → Agent kann ausbrechen. Fix: Compose-Files validieren, privileged/host mounts blocken.
- [ ] **Live Secrets in .env** - OAuth Tokens, Encryption Key, API Secret im Klartext auf Disk. Fix: Secrets Manager nutzen oder Docker Secrets.

## HIGH

- [x] **Default API Secret Key** (`orchestrator/app/config.py:53`) - ~~`"change-me-in-production"` erlaubt JWT/HMAC Faelschung.~~ Fix: App-Start wird verweigert wenn Default-Key aktiv. (2026-03-26)
- [x] **Webhook ohne Auth** (`orchestrator/app/api/webhooks.py:31`) - ~~Jeder kann Tasks an Agents senden.~~ Fix: HMAC Signature Verification hinzugefuegt. (2026-03-26)
- [x] **Memory Search ohne Auth** (`orchestrator/app/api/memory.py:79`) - ~~Agent-Memories frei lesbar.~~ Fix: `verify_agent_token` Dependency hinzugefuegt + agent_id Ownership Check. (2026-03-26)
- [ ] **Full-Access Sudo** (`orchestrator/app/core/agent_manager.py:50`) - `ALL=(ALL) NOPASSWD: ALL`. Fix: Entfernen oder Admin-Bestaetigung erfordern.
- [x] **Command Injection docker_apps** (`orchestrator/app/api/docker_apps.py:278-302`) - ~~Unsanitized path in Shell-Commands.~~ Fix: `shlex.quote()` + path traversal validation auf alle User-Pfade angewendet. (2026-03-26)
- [x] **Shell Injection templates** (`orchestrator/app/api/templates.py:230-236`) - ~~Heredoc-Termination → Code Execution.~~ Fix: `write_file_in_container()` (tar-Methode) statt Heredoc. (2026-03-26)
- [ ] **SSRF via MCP Server** (`orchestrator/app/api/mcp_servers.py:61-134`) - Admin kann interne Netzwerk-Scans machen. Fix: Private IP-Ranges und Cloud Metadata Endpoints blocken.
- [ ] **OAuth Token auf Shared Volume** (`orchestrator/app/services/claude_token_service.py:103`) - Alle Agents koennen OAuth Token lesen. Fix: Per-Agent Token-Delivery via Env-Vars.
- [x] **Agents erreichen Redis direkt** (`docker-compose.yml:42`) - ~~Redis auf agent-network ohne Passwort.~~ Fix: Redis `--requirepass` hinzugefuegt. (2026-03-26)
- [x] **Orchestrator laeuft als Root** (`orchestrator/Dockerfile`) - ~~Kein USER directive.~~ Fix: Non-root User (appuser:1000) hinzugefuegt. (2026-03-26)
- [ ] **xlsx Prototype Pollution** (`frontend/package.json`) - xlsx v0.18.5 hat bekannte CVEs. Fix: Durch `exceljs` oder `sheetjs-ce` ersetzen.
- [ ] **Next.js DoS** (`frontend/package.json`) - Next.js 14 hat bekannte CVEs. Fix: Auf Next.js 15+ upgraden.

## MEDIUM

- [ ] **CORS Wildcard + Credentials** (`orchestrator/app/main.py:496-508`) - `allow_origin_regex=".*"` mit credentials. Fix: Restrictive Origin-Liste als Default. (Belassen fuer LAN/VPN Zugriff — nur bei Production einschraenken via CORS_ALLOW_ORIGIN env var.)
- [x] **Keine Brute-Force Protection** (`orchestrator/app/api/auth.py:122-135`) - ~~120 Login-Versuche/min erlaubt.~~ Fix: Per-Email Rate Limit (5 Versuche / 5 min) hinzugefuegt. (2026-03-26)
- [ ] **Anonymous Admin Setup Mode** (`orchestrator/app/dependencies.py:40-67`) - Voller Admin-Zugriff ohne Auth vor erstem User. Fix: Setup-Token aus Env-Var erfordern.
- [x] **Approvals ohne Ownership Check** (`orchestrator/app/api/approvals.py:175-331`) - ~~Jeder User kann alle Approvals sehen/aendern.~~ Fix: Agent-Ownership Filter + require_agent_access Check. (2026-03-26)
- [x] **OAuth Token Endpoint fuer alle** (`orchestrator/app/api/integrations.py:106-117`) - ~~Jeder Auth-User kann Plaintext Tokens abrufen.~~ Fix: Admin-Only gemacht. (2026-03-26)
- [x] **Redis ohne Passwort** (`docker-compose.yml:30`) - ~~Kein `--requirepass`.~~ Fix: Passwort gesetzt und URLs aktualisiert. (2026-03-26)
- [x] **Keine Security Headers Caddy** (`Caddyfile`) - ~~Kein HSTS, X-Frame-Options, CSP etc.~~ Fix: Header-Block hinzugefuegt (HSTS, X-Content-Type-Options, X-Frame-Options, Referrer-Policy, Permissions-Policy). (2026-03-26)
- [ ] **Keine Security Headers Traefik** (`traefik/traefik.yml`) - Gleiches Problem. Fix: Headers Middleware via Labels.
- [ ] **WebSocket Token in URL** (`orchestrator/app/api/ws.py:52`) - JWT in Query Parameter. Fix: Short-lived WS Tickets.
- [ ] **Keine CSP im Frontend** - Kein Content-Security-Policy Header. Fix: Via `next.config.js` headers() setzen.
- [ ] **XLSX dangerouslySetInnerHTML** (`frontend/src/components/files/viewers/xlsx-viewer.tsx:82`) - Ohne Sanitization. Fix: DOMPurify vorschalten.
- [ ] **iframe allow-same-origin** (`frontend/src/components/files/file-preview.tsx:283`) - Clickjacking-Risiko. Fix: `allow-same-origin` entfernen.
- [x] **Orchestrator keine Resource Limits** (`docker-compose.yml`) - ~~Kein Memory-Limit.~~ Fix: `deploy.resources.limits.memory: 1G` hinzugefuegt. (2026-03-26)
- [ ] **Shared Volume RW fuer alle Agents** - Agents koennen gegenseitig Daten manipulieren. Fix: Read-only mounten fuer Agents.

## CSP

- [x] **unsafe-eval entfernt** (`orchestrator/app/main.py`) - CSP script-src hatte 'unsafe-eval'. Entfernt am 2026-03-26.

## POSITIVE (bereits vorhanden)

- SQLAlchemy ORM ueberall (kein SQL Injection)
- bcrypt fuer Passwoerter korrekt
- httpOnly Cookies fuer Auth
- JWT mit kurzer Laufzeit (30min)
- Container-Hardening (cap_drop ALL, pids_limit)
- Encrypted OAuth Storage (Fernet)
- File Upload Validation
- HMAC Agent Auth mit constant-time comparison
