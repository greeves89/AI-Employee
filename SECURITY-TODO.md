# Security Audit TODO

Audit durchgefuehrt am 2026-03-01. Findings nach Severity sortiert.

## CRITICAL

- [ ] **Docker Socket RW** (`docker-compose.yml:79`) - Orchestrator hat vollen Host-Zugriff via Docker Socket. Fix: Docker Socket Proxy (Tecnativa/docker-socket-proxy) einsetzen.
- [ ] **Docker Apps Container Escape** (`orchestrator/app/api/docker_apps.py:82-87`) - Compose-Runner bekommt Docker Socket → Agent kann ausbrechen. Fix: Compose-Files validieren, privileged/host mounts blocken.
- [ ] **Live Secrets in .env** - OAuth Tokens, Encryption Key, API Secret im Klartext auf Disk. Fix: Secrets Manager nutzen oder Docker Secrets.

## HIGH

- [ ] **Default API Secret Key** (`orchestrator/app/config.py:53`) - `"change-me-in-production"` erlaubt JWT/HMAC Faelschung. Fix: App-Start verweigern wenn Default-Key aktiv.
- [ ] **Webhook ohne Auth** (`orchestrator/app/api/webhooks.py:31`) - Jeder kann Tasks an Agents senden. Fix: HMAC Signature Verification hinzufuegen.
- [ ] **Memory Search ohne Auth** (`orchestrator/app/api/memory.py:79`) - Agent-Memories frei lesbar. Fix: `require_auth` Dependency hinzufuegen.
- [ ] **Full-Access Sudo** (`orchestrator/app/core/agent_manager.py:50`) - `ALL=(ALL) NOPASSWD: ALL`. Fix: Entfernen oder Admin-Bestaetigung erfordern.
- [ ] **Command Injection docker_apps** (`orchestrator/app/api/docker_apps.py:278-302`) - Unsanitized path in Shell-Commands. Fix: `shlex.quote()` auf alle User-Pfade anwenden.
- [ ] **Shell Injection templates** (`orchestrator/app/api/templates.py:230-236`) - Heredoc-Termination → Code Execution. Fix: `write_file_in_container()` (tar-Methode) statt Heredoc nutzen.
- [ ] **SSRF via MCP Server** (`orchestrator/app/api/mcp_servers.py:61-134`) - Admin kann interne Netzwerk-Scans machen. Fix: Private IP-Ranges und Cloud Metadata Endpoints blocken.
- [ ] **OAuth Token auf Shared Volume** (`orchestrator/app/services/claude_token_service.py:103`) - Alle Agents koennen OAuth Token lesen. Fix: Per-Agent Token-Delivery via Env-Vars.
- [ ] **Agents erreichen Redis direkt** (`docker-compose.yml:42`) - Redis auf agent-network ohne Passwort. Fix: Redis `--requirepass` hinzufuegen.
- [ ] **Orchestrator laeuft als Root** (`orchestrator/Dockerfile`) - Kein USER directive. Fix: Non-root User hinzufuegen.
- [ ] **xlsx Prototype Pollution** (`frontend/package.json`) - xlsx v0.18.5 hat bekannte CVEs. Fix: Durch `exceljs` oder `sheetjs-ce` ersetzen.
- [ ] **Next.js DoS** (`frontend/package.json`) - Next.js 14 hat bekannte CVEs. Fix: Auf Next.js 15+ upgraden.

## MEDIUM

- [ ] **CORS Wildcard + Credentials** (`orchestrator/app/main.py:496-508`) - `allow_origin_regex=".*"` mit credentials. Fix: Restrictive Origin-Liste als Default.
- [ ] **Keine Brute-Force Protection** (`orchestrator/app/api/auth.py:122-135`) - 120 Login-Versuche/min erlaubt. Fix: Login-spezifisches Rate Limit (5/min pro Email).
- [ ] **Anonymous Admin Setup Mode** (`orchestrator/app/dependencies.py:40-67`) - Voller Admin-Zugriff ohne Auth vor erstem User. Fix: Setup-Token aus Env-Var erfordern.
- [ ] **Approvals ohne Ownership Check** (`orchestrator/app/api/approvals.py:175-331`) - Jeder User kann alle Approvals sehen/aendern. Fix: Agent-Ownership filtern.
- [ ] **OAuth Token Endpoint fuer alle** (`orchestrator/app/api/integrations.py:106-117`) - Jeder Auth-User kann Plaintext Tokens abrufen. Fix: Admin-Only machen.
- [ ] **Redis ohne Passwort** (`docker-compose.yml:30`) - Kein `--requirepass`. Fix: Passwort setzen und alle URLs updaten.
- [ ] **Keine Security Headers Caddy** (`Caddyfile`) - Kein HSTS, X-Frame-Options, CSP etc. Fix: Header-Block hinzufuegen.
- [ ] **Keine Security Headers Traefik** (`traefik/traefik.yml`) - Gleiches Problem. Fix: Headers Middleware via Labels.
- [ ] **WebSocket Token in URL** (`orchestrator/app/api/ws.py:52`) - JWT in Query Parameter. Fix: Short-lived WS Tickets.
- [ ] **Keine CSP im Frontend** - Kein Content-Security-Policy Header. Fix: Via `next.config.js` headers() setzen.
- [ ] **XLSX dangerouslySetInnerHTML** (`frontend/src/components/files/viewers/xlsx-viewer.tsx:82`) - Ohne Sanitization. Fix: DOMPurify vorschalten.
- [ ] **iframe allow-same-origin** (`frontend/src/components/files/file-preview.tsx:283`) - Clickjacking-Risiko. Fix: `allow-same-origin` entfernen.
- [ ] **Orchestrator keine Resource Limits** (`docker-compose.yml`) - Kein Memory-Limit. Fix: `deploy.resources.limits.memory: 1G` hinzufuegen.
- [ ] **Shared Volume RW fuer alle Agents** - Agents koennen gegenseitig Daten manipulieren. Fix: Read-only mounten fuer Agents.

## POSITIVE (bereits vorhanden)

- SQLAlchemy ORM ueberall (kein SQL Injection)
- bcrypt fuer Passwoerter korrekt
- httpOnly Cookies fuer Auth
- JWT mit kurzer Laufzeit (30min)
- Container-Hardening (cap_drop ALL, pids_limit)
- Encrypted OAuth Storage (Fernet)
- File Upload Validation
- HMAC Agent Auth mit constant-time comparison
