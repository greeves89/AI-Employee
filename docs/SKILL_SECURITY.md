# Skill Security — Static Install-Time Gate

This document describes the static security gate that vets marketplace skills before
they are stored, and what an administrator sees and does when a skill is blocked.
It covers the **shipped** half of issue #192 (static install-time hardening). The
runtime egress allow-list and the admin review-queue UI are tracked as follow-ups in
#192 (and overlap the zero-trust egress work in #194).

## Why this exists

The "post-install dropper" attack adds a single lifecycle script to a `package.json`
(or ships a `setup.sh` / `postinstall.js` hook) that runs arbitrary code the moment a
bundle is installed — in about a second, never touching source review. Because
AI-Employee ships a multi-tenant skill marketplace, a skill bundle must never be able
to smuggle an executable hook or a native binary past import.

## What the gate blocks

The gate is implemented in `orchestrator/app/core/skill_security.py` and rejects a
skill when any of the following is present:

1. **package.json lifecycle scripts** — a `scripts` block declaring any of:
   `preinstall`, `install`, `postinstall`, `preuninstall`, `postuninstall`,
   `prepare`, `prepublish`, `prepublishonly`, `prestart`, `poststart`.
   This is detected both in an uploaded `package.json` file and in an embedded
   `"scripts": { … }` block inside the skill's text content (e.g. a fenced code
   block in the markdown).
2. **Setup/lifecycle hook files** — an attachment whose filename matches
   `(pre|post)?(install|start|setup).(sh|bash|zsh|py|js|cjs|mjs|ts)`,
   e.g. `postinstall.js`, `setup.sh`, `preinstall.py`.
3. **Compiled executables** — an attachment whose magic bytes identify a native
   binary: ELF (Linux/BSD), Windows PE (`MZ` + `PE\0\0` signature), Mach-O
   (32/64, BE/LE, and fat/Java-class), or WebAssembly (`\0asm`). A skill ships
   documentation and text templates, never a binary.

A skill bundle that carries none of the above passes the gate unchanged.

## Where the gate runs

The gate is enforced on every path that introduces skill content into the system:

| Endpoint | What is checked |
| --- | --- |
| `POST /marketplace` (create skill) | content |
| `POST /marketplace/import` (import skill) | content |
| `POST /agent/propose` (agent-proposed skill) | content |
| `POST /marketplace/{skill_id}/files` (file upload) | file attachment |
| Skill crawler (`skill_crawler.py`) | content of every crawled skill |

On rejection the API returns **HTTP 400** with the specific reason (e.g.
`package.json declares lifecycle script(s): postinstall`), so the reason is visible to
the caller and in the UI.

## What an administrator sees

Every block writes an audit-log entry with event type **`SKILL_INSTALL_BLOCKED`**
(`orchestrator/app/models/audit_log.py`) containing:

- `agent_id` — the agent that attempted the install/proposal,
- `user_id` — the acting user (when known),
- `command` — the offending filename or skill name,
- `outcome` — `blocked`,
- `meta.skill_name` and `meta.reason` — the human-readable rejection reason.

A matching `WARNING` line is written to the orchestrator log:
`Skill '<name>' blocked by security gate: <reason>`.

Review these entries in the audit log to see which skills were rejected, who
attempted them, and why.

## How to approve a blocked (but trusted) skill

If an administrator has reviewed a skill and decided its hook is legitimate, add the
skill's **exact name** to the `SKILL_HOOK_ALLOWLIST` environment variable
(comma-separated) on the orchestrator, then restart it:

```yaml
# docker-compose.yml (orchestrator service)
environment:
  SKILL_HOOK_ALLOWLIST: "my-trusted-skill,another-reviewed-skill"
```

An allow-listed skill name bypasses the static gate entirely (`is_allowlisted()`),
so use it only for skills you have manually reviewed. Allow-listing is name-based and
does not disable the gate for any other skill.

## Tests

`orchestrator/tests/test_skill_security.py` covers: post-install/lifecycle rejection
(file and embedded content), hook-file rejection, compiled-executable rejection,
allow-list bypass, and benign-skill acceptance.

## Not yet covered (follow-ups in #192)

- **Runtime egress allow-list** — a `network_allowlist` manifest field with
  deny-by-default per-skill egress enforced in the agent container (overlaps #194).
- **Admin review-queue UI** — a queue of heuristically flagged skills pending an
  admin click, showing the rejected reason.
