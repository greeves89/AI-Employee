# Design: Abteilungs-Second-Brains (UI-verwaltete, geteilte Vaults)

**Datum:** 2026-06-24
**Status:** Entwurf zur Abnahme
**Branch:** `feat/department-second-brains`
**Kontext:** Kunden-Deployment SKBS (Klinikum Braunschweig), Host `skbs-s-kichat`, AI-Employee v1.55.36

---

## 1. Ziel

Ein **Admin** kann im Web-UI pro Abteilung ein **„Second Brain"** anlegen — einen geteilten
Ordner mit Markdown-Dateien (Obsidian-artiger Vault) unter `/srv/secondbrain/<slug>/`. Die
Agents der zugewiesenen Personen greifen auf **denselben** Vault zu (lesend oder schreibend),
sodass eine **abteilungsweite, geteilte Wissensablage** entsteht (Wiki-/Wikimedia-Ersatz), mit der
die Mitarbeiter über die KI interagieren.

Das Anlegen eines Brains und die Vergabe der Lese-/Schreibrechte erfolgt **komplett im UI**,
**ohne** `.env`-Bearbeitung und **ohne** Orchestrator-Neustart.

**Erste Abteilung beim Rollout:** `it_operations` → `/srv/secondbrain/it_operations/`.

---

## 2. Nicht-Ziele (bewusst ausgeklammert)

- **Keine Datei-Versionierung** auf Inhaltsebene (kein Git-per-Vault). Nur die Brain-/Rechte-Aktionen
  werden im bestehenden `audit_log` getrackt. (Entscheidung 2026-06-24.)
- **Kein Mensch-Editier-Zugang** (Obsidian/Netzlaufwerk/Samba) in diesem Scope. Vorerst interagieren
  nur die Agents mit dem Vault. Der Mensch-Zugang wird später separat geklärt (siehe §12).
- **Keine eigene Super-Admin-Rolle.** Verwaltung läuft über die vorhandene `require_admin`-Dependency.
- **Kein eigenes Department-/Gruppen-Datenmodell.** Personengruppen werden über die bereits
  vorhandenen **Custom Roles** (`custom_roles.permissions.mount_labels`) abgebildet.
- **Kein Umbau des persönlichen Second Brain** (DB-basierter, `user_id`-scoped Brain bleibt wie er ist).

---

## 3. Kernidee: „Second Brain" = ein DB-verwalteter Mount-Eintrag

Heute lebt ein Mount-Label im `AGENT_MOUNT_CATALOG` (ENV, `.env`/compose). Wir verlagern die
Definition in eine **DB-Tabelle** und mergen sie beim Agent-Start mit dem ENV-Katalog.

**Entscheidend:** Die gesamte Rechte- und Zuweisungs-Maschinerie hängt heute schon am
**`mount_label`**. Sie bleibt damit **unverändert** nutzbar:

| Vorhandener Baustein | Datei | bleibt |
|---|---|---|
| ro/rw pro Person | `user_mount_access` (Tabelle vorhanden) | unverändert |
| Rechte-Modal im UI | `frontend/src/components/admin/mount-permissions-modal.tsx` | unverändert |
| Gruppen-Rechte | `custom_roles.permissions.mount_labels` (`core/permissions.py`) | unverändert |
| User hängt Brain an Agent | `MountSelectorSection` (`frontend/src/app/agents/[id]/page.tsx:2034`) | unverändert |
| Enforcement ro/rw beim Start | `api/agents.py:1515-1593`, `core/mounts.py:72-76` (`_stricter_mode`) | unverändert |
| Mount-Resolution → Docker-Bind | `core/mounts.py:79-106`, `core/agent_manager.py:1230-1233` | unverändert |
| Agent-File-Tools auf Mount | `read_file`/`write_file`/`edit_file`/`list_files`/`grep` (`agent/app/tools/`) | unverändert |

Ein neu angelegtes Brain erscheint dadurch **sofort** im Rechte-Modal und im Mount-Selector.

---

## 4. Architektur / Komponenten

```
Admin-UI „Second Brains"  ──POST /brains──►  Orchestrator
   (CRUD, Klon von AI-Accounts)                 │
                                                ├─ second_brain Tabelle (DB)
                                                ├─ mkdir -p /srv/secondbrain/<slug>
                                                │     (Orchestrator hat /srv/secondbrain rw gemountet)
                                                └─ Audit-Event BRAIN_CREATED

Agent-Start (bestehender Pfad, erweitert):
   effektiver Katalog = parse_mount_catalog(ENV)  ∪  load_db_brains()
                               │
                               ▼
   resolve_agent_mounts(agent.config["mounts"], katalog, mount_modes)
   → Bind-Mount  /srv/secondbrain/<slug>  →  /mnt/brains/<slug>  (ro|rw)
```

### 4.1 Datenmodell — neue Tabelle `second_brain`

| Spalte | Typ | Bedeutung |
|---|---|---|
| `id` | int PK | |
| `label` | str, unique | Mount-Label, an dem alle Rechte hängen, z.B. `brain-it_operations` |
| `name` | str | Anzeigename / Abteilung, z.B. „IT Operations" |
| `slug` | str, unique | Dateisystem-sicher `[a-z0-9_-]`, z.B. `it_operations` |
| `host_path` | str | `/srv/secondbrain/<slug>` (server-intern, **nie** an UI exponiert) |
| `container_path` | str | `/mnt/brains/<slug>` |
| `default_mode` | str | `ro`\|`rw` (Katalog-Default; pro Person/Rolle weiter einschränkbar) |
| `description` | str, null | optional |
| `is_active` | bool | Soft-Disable ohne Löschen |
| `created_by` | str | Admin-User-ID |
| `created_at`/`updated_at` | tstz | |

Migration: 1 neue Alembic-Revision.

### 4.2 Katalog-Quelle erweitern (ENV + DB)

`core/mounts.py`: Der Katalog wird künftig aus **zwei** Quellen gebildet:
1. `parse_mount_catalog(settings.agent_mount_catalog)` (ENV, wie bisher — bleibt für statische Mounts).
2. **Neu:** `load_brain_catalog(db)` — alle aktiven `second_brain`-Zeilen als `MountEntry`.

Beide werden zu einem `dict[label → MountEntry]` gemerged (DB-Brains gewinnen bei Label-Kollision
nicht — Labels sind disjunkt durch Präfix `brain-`). Aufrufstellen in
`core/agent_manager.py` (`:437-438`, `:1063-1065`, `:1230-1232`) erhalten die gemergte Map.
Da jeder Agent-Start den Katalog frisch auflöst, ist **kein Orchestrator-Neustart** nötig.

### 4.3 Ordner-Provisionierung

- Compose: Orchestrator bekommt `/srv/secondbrain:/srv/secondbrain:rw` (eine Zeile in
  `docker-compose.override.yml`). Base-Ordner wird einmalig auf dem Host angelegt.
- Beim `POST /brains`: Orchestrator legt `mkdir -p /srv/secondbrain/<slug>` an (innerhalb der
  gemounteten Base; Pfad-Jail gegen `..`/Ausbruch). Optional eine Start-`index.md` als Vault-Wurzel.
- Berechtigungen: Ordner so, dass der Agent-Container-User schreiben kann (Mode-Setzung beim
  Anlegen; konkreter UID-Wert wird beim Setup verifiziert — operativer Punkt, kein Code).

### 4.4 Zugriffssteuerung (komplett wiederverwendet)

- **Pro Person:** Admin öffnet das bestehende **Mount-Permissions-Modal** und setzt `ro`/`rw`/kein
  Zugriff für das Brain-Label (`user_mount_access`).
- **Pro Gruppe:** Custom Role mit `permissions.mount_labels = ["brain-it_operations", …]`.
- **Effektiver Modus:** `_stricter_mode()` — `ro` gewinnt; Katalog-`default_mode` ist die Obergrenze.
- **Agent-Zuweisung:** User hakt das Brain in `MountSelectorSection` an → Agent-Restart (bestehendes
  Auto-Restart-Verhalten bei Mount-Änderung).

### 4.5 Admin-UI „Second Brains"

Neuer Tab im Admin-Bereich (`frontend/src/app/admin/page.tsx`), 1:1 nach dem Muster von
**AI-Accounts** (`frontend/src/app/ai-accounts/view.tsx`):
- Liste aller Brains (Name/Abteilung, slug, default_mode, aktiv).
- Modal Create/Edit: `name`, `slug` (auto aus name, editierbar), `default_mode`, `description`.
- Delete (Soft-Disable empfohlen; Hard-Delete entfernt **nicht** den Ordner — Daten bleiben).
- Pro Zeile: Button „Rechte" → öffnet das vorhandene Mount-Permissions-Modal gefiltert auf dieses Label.

### 4.6 API-Endpunkte (Klon von `api/ai_accounts.py`)

| Methode | Pfad | Auth | Zweck |
|---|---|---|---|
| GET | `/api/v1/brains` | auth | Liste (Host-Pfad wird **nicht** ausgegeben) |
| POST | `/api/v1/brains` | admin | Brain anlegen (+ mkdir + Audit) |
| PATCH | `/api/v1/brains/{id}` | admin | name/mode/description/aktiv ändern |
| DELETE | `/api/v1/brains/{id}` | admin | Soft-Disable (Ordner bleibt) |

Rechtevergabe läuft über die **bestehenden** Endpunkte `list_user_mount_access` /
`set_user_mount_access` (`api/settings.py:253-319`) — kein neuer Rechte-Endpoint nötig.

### 4.7 Audit

Neue Event-Typen in `models/audit_log.py`: `BRAIN_CREATED`, `BRAIN_UPDATED`, `BRAIN_DELETED`.
(Rechte-Änderungen werden über das vorhandene Mount-Access-Logging abgedeckt bzw. ergänzt um
`BRAIN_ACCESS_GRANTED`/`REVOKED`, falls dort noch nicht geloggt wird.)

---

## 5. Datenfluss (Beispiel it_operations)

1. Admin legt Brain „IT Operations" an → `second_brain(label=brain-it_operations,
   slug=it_operations, host_path=/srv/secondbrain/it_operations, container_path=/mnt/brains/it_operations,
   default_mode=rw)`; Orchestrator `mkdir`t den Ordner.
2. Admin gibt User A `rw`, User B `ro` auf `brain-it_operations` (Mount-Permissions-Modal).
3. User A hängt das Brain an seinen Agent (Mount-Selector) → Agent-Restart.
4. Agent A schreibt `write_file("/mnt/brains/it_operations/Runbooks/Backup.md", …)`.
5. Agent B (User B, ro) liest dieselbe Datei, kann aber nicht schreiben (Enforcement).

---

## 6. Sicherheit

- **Host-Pfad nie an UI**: `GET /brains` gibt nur `label`/`name`/`container_path`/`mode` zurück.
- **Slug-/Pfad-Jail**: `slug ∈ [a-z0-9_-]+`, `host_path` muss unter `/srv/secondbrain/` liegen;
  `..`/absolute Ausbrüche werden abgelehnt (analog `parse_mount_catalog`-Validierung).
- **Admin-only** für Create/Update/Delete (`require_admin`).
- **ro-Härtung**: Katalog-`default_mode` ist Obergrenze; `_stricter_mode` verhindert Hochstufung.
- **Keine Secrets** betroffen; `ENCRYPTION_KEY` unberührt.

---

## 7. Wiederverwendete vs. neue Artefakte

**Wiederverwendet (0 Änderung):** `user_mount_access` (Tabelle+API+Modal), `custom_roles`/
`core/permissions.py`, `MountSelectorSection`, `resolve_agent_mounts`/`mounts_to_docker_volumes`,
Agent-File-Tools, Auto-Restart bei Mount-Änderung, `audit_log`-Framework.

**Neu / erweitert:**
- `orchestrator/app/models/second_brain.py` (neu)
- Alembic-Migration `second_brain` (neu)
- `orchestrator/app/api/brains.py` (neu, Klon `ai_accounts.py`)
- `orchestrator/app/core/mounts.py` (erweitert: `load_brain_catalog` + Merge)
- `orchestrator/app/core/agent_manager.py` (Katalog-Quelle an den 3 Resolve-Stellen erweitern)
- `orchestrator/app/models/audit_log.py` (neue Event-Typen)
- `frontend/src/app/admin/page.tsx` (Tab „Second Brains")
- `frontend/src/components/admin/second-brains-view.tsx` (neu, Klon AI-Accounts-View)
- `frontend/src/lib/api.ts` + `types.ts` (`getBrains/createBrain/updateBrain/deleteBrain`, Typ `SecondBrain`)
- `docker-compose.override.yml` (Orchestrator `/srv/secondbrain` rw)

---

## 8. Testing (MCDC-relevant: Auth + Enforcement)

- **AuthZ:** Create/Update/Delete als Nicht-Admin → 403; als Admin → 200.
- **Pfad-Jail:** slug `../etc`, absolute Pfade, leere slugs → 400.
- **Enforcement-Matrix:** (User-Grant ro|rw|none) × (Katalog default_mode ro|rw) → effektiver Mode;
  `ro`-User darf nicht schreiben, `none`-User sieht den Mount nicht.
- **Katalog-Merge:** ENV-Mount + DB-Brain gleichzeitig auflösbar; Label-Kollision ausgeschlossen.
- **Ordner-Provisionierung:** `mkdir` idempotent; existierender Ordner wird nicht überschrieben.
- **Agent-E2E:** Brain anlegen → zuweisen → Agent liest/schreibt `.md` im Vault.

---

## 9. Rollout-Schritte (it_operations)

1. Host: `/srv/secondbrain/` anlegen, Orchestrator-Mount in compose, Orchestrator neu erstellen (einmalig).
2. Feature deployen (Migration + Backend + Frontend-Build).
3. Brain „IT Operations" (`it_operations`) im UI anlegen.
4. Rechte an die IT-Operations-Personen vergeben (ro/rw).
5. Agent zuweisen, E2E lesen/schreiben verifizieren.

---

## 10. Offene Punkte / später

- **Mensch-Editier-Zugang** (Obsidian via Samba/Netzlaufwerk auf `/srv/secondbrain`) — separater Scope.
- **Datei-Historie** (Git-per-Vault) — bewusst ausgeklammert, später nachrüstbar.
- **Dynamischer Agent-Reload ohne Container-Restart** — heute Restart bei Mount-Änderung (akzeptiert).
- **Brain-Löschung inkl. Daten** — aktuell Soft-Disable; Hard-Delete der Ordnerdaten nur manuell.

---

## 11. Aufwand (grob)

Backend Modell+Migration+API ~1 Tag · Katalog-Merge+Provisionierung ~0,5 Tag · Admin-UI (Klon) ~1 Tag ·
Audit+Tests ~0,5 Tag → **~3 Tage**. Niedrig, weil Rechte-/Enforcement-Schicht bereits existiert.
