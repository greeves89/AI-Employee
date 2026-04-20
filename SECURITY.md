# Security Policy

We take security seriously. AI-Employee is used by teams in regulated industries — lawyers, tax advisors, medical practices, and finance — so the stakes are real. Thank you for helping us keep the platform safe.

## Supported Versions

Only the latest minor release receives security updates. We recommend running the most recent version at all times.

| Version | Supported |
|---|:---:|
| 1.x (current) | Yes |
| 0.x (pre-release) | No |

## Reporting a Vulnerability

**Please do not file a public GitHub issue for security vulnerabilities.**

Instead, email **daniel.alisch@me.com** with:

- A clear description of the vulnerability
- Steps to reproduce (or a proof-of-concept)
- The affected version / commit hash
- Your assessment of the impact (confidentiality, integrity, availability)
- Your name or handle for credit (optional)

If you prefer encrypted communication, request our PGP key in the first message and we will send it before you share details.

### Response SLA

We commit to:

- **Acknowledge** your report within **48 hours**
- **Initial assessment** within **5 business days**
- **Fix timeline** depending on severity:
  - **Critical** (RCE, auth bypass, data leak across tenants): patch within **7 days**
  - **High** (privilege escalation, significant data exposure): patch within **14 days**
  - **Medium** (XSS, CSRF with limited impact): patch within **30 days**
  - **Low** (minor information disclosure): patch in the next scheduled release

We will keep you updated throughout and coordinate public disclosure with you.

## Please DO

- Give us reasonable time to patch before publicly disclosing
- Work with us in good faith on the disclosure timeline
- Provide enough detail for us to reproduce and fix the issue
- Let us know your preferred credit name for the Hall of Fame

## Please DO NOT

- File a public GitHub issue, discussion, or pull request describing the vulnerability
- Post about it on Twitter/X, Mastodon, Reddit, or any other public forum before we have released a fix
- Exploit the vulnerability on installations you do not own
- Access, modify, or delete data that does not belong to you
- Perform automated scanning against production installations you do not operate

## Scope

**In scope** (we want to hear about these):

- Remote Code Execution (RCE) in orchestrator, agent, or embedding service
- SQL injection
- Authentication bypass or broken authentication
- Authorization flaws (including PostgreSQL RLS bypass)
- Cross-Site Scripting (XSS), Cross-Site Request Forgery (CSRF)
- Server-Side Request Forgery (SSRF)
- Insecure deserialization
- Secrets leakage (tokens, keys, passwords in logs or responses)
- Container escape from an agent sandbox
- Multi-tenant isolation failures (one user seeing another user's data)
- Prompt-injection attacks that lead to real-world impact (exfiltration, approval bypass, etc.)

**Out of scope** (please do not report these):

- Denial-of-service attacks requiring floods of traffic
- Social engineering of maintainers or contributors
- Physical attacks requiring access to the host machine
- Outdated dependencies **without** a working proof-of-concept
- Issues in third-party integrations that are not exploitable in AI-Employee itself
- Self-XSS, tab-nabbing, clickjacking on pages without sensitive actions
- Missing security headers on non-sensitive endpoints
- Best-practice suggestions without a demonstrable security impact
- Attacks requiring an already-compromised host or an already-authenticated admin

## Hall of Fame

Security researchers who responsibly disclose vulnerabilities get credit here. We are grateful for your work.

*(No entries yet — your name could be first.)*

## Security Best Practices for Self-Hosting

If you are running AI-Employee in production, please follow these guidelines:

### Secrets & Keys

- **Rotate all default secrets** immediately after first install. The `.env.example` placeholders must be replaced.
- **Generate strong random values**:
  ```bash
  openssl rand -base64 32                    # for JWT_SECRET, POSTGRES_PASSWORD
  python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"  # for ENCRYPTION_KEY
  ```
- **Never commit `.env` to git.** `.gitignore` already excludes it, but double-check.
- **Back up your `ENCRYPTION_KEY`** — without it, encrypted OAuth tokens and secrets are unrecoverable.
- **Rotate tokens periodically**, especially Claude OAuth tokens and OAuth integration tokens.

### Network & Exposure

- **Do NOT expose the orchestrator port (8000) directly to the internet.** Always put a reverse proxy (Caddy, Traefik, Nginx) in front of it with TLS.
- **Use Let's Encrypt** — both Caddy and Traefik do this automatically.
- **Prefer Cloudflare Tunnel** or a VPN for remote access rather than opening ports.
- **Restrict database and Redis** to the internal Docker network. They should never be publicly reachable.
- **Enable HSTS** and other security headers in your reverse proxy.

### Updates & Patching

- **Keep Docker images up to date** — pull weekly at minimum:
  ```bash
  docker compose pull && docker compose up -d
  ```
- **Subscribe to release notifications** on GitHub to hear about security releases.
- **Update the host OS** regularly, especially Docker Engine itself.

### Governance & Access

- **Review approval rules regularly.** Make sure they still match your risk tolerance as the team grows.
- **Audit user accounts monthly.** Remove accounts for people who have left.
- **Use strong, unique passwords** for every admin account. Enable 2FA when available.
- **Restrict agent capabilities** to what each role needs. A Marketing agent does not need `rm -rf` permissions.

### Backups & Recovery

- **Schedule automated backups** — `pg_dump` + volume tar + SHA256 manifest (see `scripts/backup.sh`).
- **Test restores quarterly.** An untested backup is not a backup.
- **Store backups off-host** (S3, B2, encrypted external drive).
- **Encrypt backups at rest**, especially the encryption key and OAuth token dumps.

### Monitoring

- **Enable the monitoring overlay** (`docker-compose.monitoring.yml`) in production.
- **Alert on** failed logins, approval-rule rejections, agent crashes, and unusual egress traffic.
- **Review audit logs weekly.**

---

Thank you for helping us keep AI-Employee safe for everyone.

— The AI-Employee Maintainers
