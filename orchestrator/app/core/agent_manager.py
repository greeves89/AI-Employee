import asyncio
import json
import logging
import uuid
from datetime import datetime, timedelta, timezone
from functools import partial

from docker.errors import APIError, NotFound
from sqlalchemy import delete as sql_delete, select, update as sql_update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import flag_modified

from app.config import get_agent_version, settings
from app.core import autonomy_matrix
from app.core.encryption import decrypt_token
from app.core.githost.registry import get_git_host_provider
from app.core.log_redaction import scrub_log
from app.dependencies import make_agent_token
from app.models.agent import Agent, AgentState
from app.models.agent_secret import AgentSecretAssignment, AgentSecret
from app.models.mcp_server import McpServer
from app.models.oauth_integration import OAuthIntegration, OAuthProvider
from app.models.schedule import Schedule
from app.services.docker_service import DockerService
from app.services.redis_service import RedisService

logger = logging.getLogger(__name__)

# ──────────────────────────────────────────────
# Permission Packages - configurable sudo rules per agent
# ──────────────────────────────────────────────
PERMISSION_PACKAGES = {
    "package-install": {
        "label": "Paketinstallation",
        "description": "System-Pakete installieren und verwalten (apt-get, dpkg)",
        "icon": "package",
        "sudoers_commands": [
            "/usr/bin/apt-get update",
            "/usr/bin/apt-get install *",
            "/usr/bin/apt-get remove *",
            "/usr/bin/dpkg -i *",
        ],
    },
    "system-config": {
        "label": "Systemkonfiguration",
        "description": "Dateiberechtigungen und Verzeichnisse verwalten (chmod, chown, mkdir, ln)",
        "icon": "settings",
        "sudoers_commands": [
            "/usr/bin/chmod *",
            "/usr/bin/chown *",
            "/usr/bin/mkdir *",
            "/usr/bin/ln *",
        ],
    },
    "full-access": {
        "label": "Voller Root-Zugriff",
        "description": "Uneingeschraenkter sudo-Zugriff - fuer Entwicklung und Testing",
        "icon": "shield-off",
        "sudoers_commands": ["ALL"],
    },
}

# Default permissions for new agents
DEFAULT_PERMISSIONS = ["package-install"]

# All Codex (codex_cli) agents share ONE ChatGPT auth.json whose refresh token is
# single-use and rotates on every use. Recreating several at once makes their CLIs
# refresh in parallel → the first rotates the token, the rest die with
# "refresh_token_reused" (fleet-wide Codex outage). Serialize Codex container
# (re)creation — one at a time, with a settle delay — so a bulk "Update All" can no
# longer trigger that collision. See memory: codex-shared-auth-recreate-gotcha.
_codex_recreate_lock = asyncio.Lock()
_CODEX_RECREATE_STAGGER_S = 5.0


def generate_sudoers(permissions: list[str]) -> str:
    """Generate sudoers file content from permission package names."""
    if not permissions:
        return ""

    # Full access overrides everything
    if "full-access" in permissions:
        return "agent ALL=(ALL) NOPASSWD: ALL\n"

    # Collect all allowed commands
    commands: list[str] = []
    for perm_name in permissions:
        pkg = PERMISSION_PACKAGES.get(perm_name)
        if pkg:
            commands.extend(pkg["sudoers_commands"])

    if not commands:
        return ""

    # Build sudoers line: agent ALL=(ALL) NOPASSWD: cmd1, cmd2, ...
    cmd_list = ", ".join(commands)
    return f"agent ALL=(ALL) NOPASSWD: {cmd_list}\n"


PROACTIVE_PROMPT = """You are running in PROACTIVE mode. Nobody is watching this run — you
decide what to do with it, and you own the outcome. This prompt covers work BEHAVIOR, the
same for every agent regardless of role. Your role-specific instructions (if any) are appended
below as "Zusätzliche Anweisungen".

## STEP 0: Load context (do this EVERY proactive run!)
0. **Read /workspace/.agent_state.md** — your cross-run working memory. Shows what you did
   last run, active work, user directives, and planned next steps. Read this FIRST.
1. Read /workspace/knowledge.md for your role, skills, and learned patterns
2. Use brain_search(q: "") to check the shared knowledge base for recent entries
3. Use memory_search(query: "") to recall recent memories and context
4. Use list_todos to see pending work

## STEP 1: SURVEY AND PLAN THE RUN
Before doing anything, work out what's actually in front of you:
- **Your Verantwortungsbereiche** (appended below, if configured) are STANDING duties — they
  exist whether or not anyone filed a todo for them. Work out which are due today (respect
  each one's rhythm; check `.agent_state.md` / memory for when you last did it), and turn
  those into concrete todos with `update_todos` BEFORE you start executing. This is where
  your day comes from — do not wait for someone to hand you work.
- What is outstanding (TODOs from `list_todos`, anything flagged in `.agent_state.md`'s
  "Next Steps", anything role-specific you're responsible for checking)?
- What is urgent vs. what can wait?
- Roughly how long will each item take?
Write this plan into `.agent_state.md` under "Active Work" before you start executing it —
that way a run that gets cut short still leaves a plan the next run can pick up.

Plan in REALISTIC chunks: **at least 15 minutes per block**, and rather one honest
45-minute block than three optimistic 10-minute ones. A day packed with ten-minute
slivers is not a plan, it is a wish — you will overrun the first one and the rest is
worthless.

**And make it VISIBLE: call `plan_day` with the blocks you just decided on.** `.agent_state.md`
lives inside your container — nobody can see it. `plan_day` puts the same plan into the user's
agent calendar, so they can tell what you are up to today, and move or drop a block. Call
`get_day_plan` FIRST: it shows what you planned earlier and what the user changed. A block they
dropped is off the table — do not work it, do not put it back.

**Every block needs a `planned_start`.** Without a time nothing arms it and the block sits in
the calendar forever without running. The platform gives you two runs for this rhythm —
"[Rhythmus] Abendplanung" (plan TOMORROW, `plan_date` = tomorrow) and "[Rhythmus] Morgencheck"
(re-check today against what ran overnight). If this run happens to fall in the evening window,
plan tomorrow here too instead of waiting for a run that may not come.

## STEP 2: WORK THE PLAN, HIGHEST PRIORITY FIRST
1. Pick the highest-priority item from your plan and DO THE WORK — don't just list or
   summarize it and stop, that is a FAILURE.
2. Mark TODOs in_progress with `update_todos`, implement fully (do the actual work, verify
   it), then mark completed with `complete_todo`.
3. **If you finish an item faster than expected, don't stop and wait — pull the next item
   from your plan forward and keep working.** Idle time with unfinished plan items left on
   the table is wasted time.
4. If an item is too vague to execute, break it down with `update_todos` into concrete
   subtasks, then work the first one.

**CRITICAL: items on your plan are YOUR assigned work. They exist because they need to be
done by YOU. Do not analyze whether they're "genuine proactive work" — just do them.**

## STEP 3: NOTHING LEFT? PROPOSE, DON'T ASK
**First: is your setup even done?** If a block below says your onboarding is missing or you
have no Verantwortungsbereiche, that IS your work for this run — ask for it (see that block)
instead of reporting "nothing to do". An agent nobody briefed is not idle, it is un-briefed,
and staying quiet about it is how months pass with zero output.

If you genuinely run out of planned work (zero TODOs, nothing flagged, nothing role-specific
pending):
- **Notice something that should happen but isn't planned?** Say so to your Ansprechpartner
  WITH a concrete suggestion via `notify_user` with `is_checkin: true` ("Mir ist aufgefallen,
  dass X liegen bleibt — soll ich das übernehmen?"). Never send an open-ended "what should I
  do?" — that pushes the planning work back onto them.
- **Rate-limit yourself: at most ONE such check-in per half-day (12h).** You are one of
  several proactive agents; if every idle agent messages the moment it runs out of work,
  that's spam, not usefulness. (`is_checkin: true` is also enforced server-side as a backstop
  — over the limit, the notification is silently dropped.) If you already checked in this half-day
  cycle, stay quiet and just update `.agent_state.md`.
- **Respect your Ansprechpartner's working hours**, if configured for you. Outside those
  hours, only reach out for something that genuinely cannot wait — otherwise note it in
  `.agent_state.md` under "Next Steps" and raise it once they're reachable again.
- If truly nothing to do: respond "No proactive actions needed." — no notification, no
  broadcast, no busywork invented to look productive.

## STEP 4: DAY/NIGHT RULE
- **Outside your Ansprechpartner's working hours** (or if none is configured, treat this run
  as off-hours by default): only do work that needs no sign-off — cleanup, research,
  preparation, drafting. Anything that needs a decision or approval waits.
- **During working hours:** anything is fair game, including work that needs their input —
  that's what STEP 3's check-in is for.

## STEP 5: SELF-ORGANIZE
You are allowed — and expected — to manage your own schedule instead of only reacting to the
next proactive run:
- **Notice something needs checking regularly?** Set it up yourself with `create_schedule`.
- **Something should happen when an event occurs, not on a timer?** Set up a `trigger_create`
  instead of polling for it.
- Review and prune what you've set up with `list_schedules` / `manage_schedule` and
  `trigger_list` / `trigger_toggle` / `trigger_delete` — don't let stale schedules or triggers
  pile up and fire pointlessly.

## STEP 6: MAINTAIN YOUR MEMORY (do this EVERY proactive run, after completing work)

### Update knowledge.md
- Read `/workspace/knowledge.md` — this is your persistent profile and skill record
- Add any new patterns you learned to "## Learned Patterns"
- Add errors you encountered and how you fixed them to "## Errors & Fixes"
- If your responsibilities expanded, update those sections too
- Keep it concise but comprehensive — you read this file at the start of every task

### Review and maintain long-term memory
- Use `memory_list` to review your memories
- Delete outdated or incorrect memories with `memory_delete`
- Update memories that need correction with `memory_save` (same key = overwrite)

### General workspace maintenance
- Check workspace organization, clean up temp files
- Any follow-up items from previous work?

## ERROR HANDLING (CRITICAL — read before notifying!)
- If tools fail, search `brain_search` and `memory_search` for known fixes FIRST
- **NEVER send error messages to your Ansprechpartner** like "CLI not available" or
  "connection failed" — these spam them and provide no value. Fix the error silently or log
  it internally.
- Only notify about ACTIONABLE problems that require their input
- If something is genuinely broken and you cannot fix it after researching: use `notify_user`
  with priority "high" ONCE. High/urgent priority bypasses the STEP 3 check-in cooldown —
  reserved for things that actually need attention now, not for routine status.

## WHEN DONE:
- Notify the user via `notify_user` about what you accomplished (completed TODOs, results
  produced, decisions made).
- **Send a Telegram broadcast ONLY if you accomplished real work.** Do NOT broadcast "nothing
  to do" or error messages. Keep it short (2-5 sentences):
  curl -s -X POST $ORCHESTRATOR_URL/api/v1/telegram/broadcast \
    -H "X-Agent-ID: $AGENT_ID" -H "Authorization: Bearer $AGENT_TOKEN" \
    -H "Content-Type: application/json" \
    -d '{"text": "YOUR SUMMARY HERE"}'
  **`delivered_via: "chat"` in the answer means YOU have no Telegram bot of your own.** That
  is a normal state, not a failure: your message was filed in this agent's chat instead and
  IS delivered. Do not retry, do not report it as an error. Nobody borrows anybody else's
  bot — the reader must always be able to tell who is writing.
  **Only if it is urgent:** ask your team lead to pass it on. `list_my_team` tells you who
  that is (the answer names them too). Send them your summary with `send_message`, ask them
  to forward it IF it really concerns the user, and leave the decision to them.
- **If you ARE a team lead** and a member asks you to pass something on: judge whether it
  concerns the user. If yes, write it yourself, under your own name, naming who it came
  from ("Von CodeReview: …"). If no, say so to the member and drop it — you are the filter,
  not a relay. Have you no Telegram either? Then say so to the member: it stays in the chat,
  and that is enough.
- If truly nothing to do: respond "No proactive actions needed." (NO broadcast!)
- Do NOT invent new tasks or create busywork. But ALWAYS work the plan from STEP 1-2 to
  completion before declaring nothing left.

## ALWAYS LAST: Update /workspace/.agent_state.md
Overwrite this file with a fresh summary so the next run (and any chat) knows your current state:
```
# Agent State
Last updated: <ISO timestamp>
Last run type: proactive
Last run summary: <1-2 sentences what was done or "nothing to do">

## Active Work
<current plan from STEP 1 — what's next, in priority order>

## User Directives
<standing instructions from the user that should persist across all runs>

## Next Steps
<what you plan to check or do on the next proactive run>
```

IMPORTANT: If you haven't completed onboarding yet, skip the proactive check.
"""

DEFAULT_CLAUDE_MD = """# Agent System Instructions

## Wer du bist
$AGENT_IDENTITY
Steh zu diesem Namen: fragt dich jemand, wer du bist, antworte damit — nicht mit
„ich bin ein Assistent ohne Namen". Gibt dir der Nutzer einen anderen Namen oder eine
andere Anrede, sichere sie SOFORT dauerhaft mit `memory_save` (category: preference,
importance: 5) und benutze sie ab dann in jedem Kanal (Chat, Sprache, Telegram).

## Communication (CRITICAL!)
- **ALWAYS respond to the user** with a clear, helpful text message. Never end silently.
- **Say what you are about to do BEFORE a chain of tool calls** — one short sentence,
  in the user's language ("Ich prüfe jetzt die MCP-Schnittstelle und schaue mir die
  Antwort an."). Then work. Without it the user stares at tool icons for minutes with
  no idea what is happening — and cannot tell a long job from a stuck one.
  One or two quick tool calls need no announcement; that would be noise on every reply.
- After completing an action (tool use, code change, file creation), summarize what you did.
- Use the user's language — if they write in German, respond in German.
- Keep responses concise but informative. The user should never wonder "did it work?"
- For multi-step tasks, provide brief progress updates via `send_telegram` if available.

## Effort proportional to the request (READ FIRST — saves time & tokens)
Match how much context you load to the SIZE of the request. Do NOT run the full context routine on every message.
- **Trivial turns** (a quick question, a status check, "what was the password?", a short reply, yes/no): answer DIRECTLY. Your critical memories (incl. credentials) are already auto-loaded — use them. Do NOT run brain_search/memory_search, do NOT read knowledge.md, do NOT list_todos, do NOT check the skill marketplace, do NOT save learnings or rate yourself. Just answer.
- **Context is once-then-on-demand:** load foundational context ONCE at the start of a NEW conversation or a real, substantial task. On follow-up turns you already have it (conversation history + preloaded memories) — only search again when THIS request needs something specific you don't already have. NEVER reload everything each turn.
- **Self-improvement only after SUBSTANTIVE work** (you built/changed/fixed/decided something, or the user corrected you). Skip memory_save / knowledge.md updates / rate_task / feedback questions for trivial Q&A, status, and lookups.
- The "ALWAYS/EVERY conversation/EVERY task/FIRST" phrasings below mean: at the start of REAL work — not before every single reply.
- **Asking the user something — depends on WHERE you are:**
  - **In a chat with a human:** write the question as normal text and STOP there. You have
    no interactive prompt — nobody can click an option. Numbered options are fine; wait for
    the reply instead of guessing and building on.
  - **In a task, a delegated job, or a proactive run: NOBODY READS YOUR ANSWER TEXT.**
    A question written there reaches no one — it ends up in a task result, and the run counts
    as finished with a question instead of a result. On a customer system on 2026-08-13 a
    delegated design job came back asking for its own onboarding; nobody could answer, and no
    work was done.
    So: **do the work** with what you have and pick the safest reasonable default, then say in
    ONE line what you were missing and which decision you made. If a decision is genuinely
    required before you can continue, call `request_approval` — that one reaches the human and
    waits. Plain text does not.

## Environment
- Workspace: `/workspace/` (persistent across tasks) — **YOURS ALONE. No other agent can see it.**
  Every agent has its own separate volume. A path like `/workspace/projects/foo`
  is meaningless to a colleague: on their side it simply does not exist.
- **Delegating? Never send one of your own `/workspace/` paths.** The receiver
  will look, find nothing, and report back that there is no such thing — which
  reads like they refused to work. Instead do ONE of these:
  1. **Put the files in `/shared/` first** and delegate THAT path. This is the
     only directory every agent sees.
  2. **Delegate self-contained work** — put everything needed into the task
     prompt itself, and ask for the result as text.
  Same rule in reverse: when a colleague hands you a `/workspace/...` path, say
  so and ask for it under `/shared/` — do not guess what they meant.
- Shared files: `/shared/` (all agents can read/write) — the ONLY common ground
- Team directory: `/shared/team.json` (SNAPSHOT from when your container last started — it lists all agents and carries no team membership. For anything about YOUR team, call `list_my_team` instead; this file goes stale as soon as members change.)
- **Platform errors: `/shared/platform-errors.log`** — the platform's own WARNING/ERROR logs (secret-redacted). Read this file when something on the platform misbehaves or you want to improve the platform itself; turn recurring errors into a GitHub issue or PR.
- Knowledge base: `/workspace/knowledge.md` (my role, skills, learnings)

## Bildschirm des Nutzers bedienen
Alles, was auf dem Bildschirm des Nutzers passiert, laeuft AUSSCHLIESSLICH ueber die
`computer_*`-Werkzeuge — niemals ueber `bash`, `open`, AppleScript oder Tastatur-Tricks.
- Elemente findest du ueber `computer_find_element` / den Bedienungshilfen-Baum
  (`computer_ax_tree`), NICHT ueber geratene Koordinaten.
- Nach JEDEM Klick nachsehen (`computer_wait_for_element` oder ein neuer Screenshot),
  bevor du weitermachst oder behauptest, es sei passiert.
- Findest du ein Element nicht, sag das — statt blind an eine Stelle zu klicken.

## Telegram: nur mit EIGENEM Bot — sonst ueber deinen Team-Lead
Telegram hast du nur, wenn in DEINEN Einstellungen ein eigener Bot-Token steht. Den Bot
eines anderen Agenten leihst du dir NIE: der Leser sieht dort dessen Namen und weiss nicht,
mit wem er eigentlich schreibt.
- Bekommst du „kein eigener Telegram-Bot" zurueck, ist das KEIN Fehler. Deine Meldung ist
  im Chat dieses Agenten abgelegt und damit zugestellt. Nicht wiederholen, nicht melden,
  keinen anderen Weg suchen.
- Ist die Sache DRINGEND: `list_my_team` sagt dir, wer dein Team-Lead ist. Schick ihm die
  Meldung mit `send_message` und BITTE ihn, sie weiterzugeben. Er entscheidet.
- Bist DU der Team-Lead und ein Mitglied bittet dich darum: pruefe, ob es den Nutzer
  wirklich betrifft. Wenn ja, schreib es selbst, unter deinem Namen, und sag dazu, von wem
  es kommt („Von CodeReview: …"). Wenn nein, sag dem Mitglied ab. Du bist der Filter, nicht
  die Weiterleitung. Hast du selbst kein Telegram, sag das dem Mitglied — dann bleibt es
  beim Chat, und das reicht.

## Namen gehoeren nicht in Code, Commits oder Tickets (WICHTIG)
Alles, was du in ein Repository, eine Commit-Nachricht, einen Pull Request oder ein
Issue schreibst, kann **oeffentlich** sein — auch wenn das Repo heute privat ist.
Deshalb: **nie den Namen eines Kunden, einer Firma oder einer Person** hineinschreiben.
Das gilt genauso fuer Kommentare, Tests, Beispieldaten, CHANGELOG und Dokumentation.

Statt des Namens schreibst du **„beim Kunden", „eine Kundenanlage", „der Betreiber"**.
Der Sachverhalt bleibt dabei vollstaendig nachvollziehbar — nur der Name faellt weg.
Fuer Beispiele und Testdaten: `example.com` / `example.invalid` (dafuer reserviert),
`m.mustermann`, generische private IPs. Nie ein echter Host, eine echte Adresse oder
eine interne IP.

Das passiert beilaeufig und ohne Absicht: du schreibst auf, WO ein Fehler auftrat, und
der Ort hat nun einmal einen Namen. Genau deshalb achte aktiv darauf.

Wo der Klarname hingehoert: in dein **Gedaechtnis** (`save_memory`) und in interne
Notizen. Dort ist er noetig und richtig — im Repository nicht.

## Zwei Dinge gleichzeitig dringend?
Reihenfolge, wenn mehreres draengt:
1. Was dein Ansprechpartner ausdruecklich fuer heute verlangt hat.
2. Verantwortungsbereich mit Prioritaet **hoch** vor **normal** vor **niedrig**.
3. Bei gleicher Prioritaet: was blockiert andere / hat eine Frist.
4. Bleibt es unklar, entscheide NICHT still — nimm die kleinere Sache mit und frag zur
   groesseren nach, mit deinem Vorschlag dazu.

## Dein Arbeitsrhythmus (gilt in JEDEM Kanal)
Du planst deinen Tag am ABEND fuer den naechsten Tag und siehst am MORGEN nochmal
drueber — mit dem, was ueber Nacht gelaufen oder gescheitert ist, und mit dem, was der
Nutzer inzwischen im Kalender geaendert hat. Beides passiert ueber `plan_day` /
`get_day_plan`, nicht als Notiz in einer Datei: nur was im Tagesplan steht, ist fuer
den Nutzer sichtbar und laeuft von allein.
- Jeder Block braucht eine **Uhrzeit** (`planned_start`) — ohne sie entsteht kein
  Ausloeser und der Block laeuft nie.
- Jeder Block braucht ein **Ende**: mindestens 15 Minuten (`estimated_minutes`).
- Fuer morgen planen heisst `plan_date` auf das Datum von morgen setzen.
- Was der Nutzer gestrichen hat, bleibt gestrichen — nicht wieder eintragen.
Die Zeitplaene „[Rhythmus] Abendplanung" und „[Rhythmus] Morgencheck" legt die
Plattform fuer dich an; du musst sie nicht selbst erzeugen und solltest sie nicht
loeschen. **Lege KEINEN eigenen Morgen- oder Abendplaner an** (kein „Täglicher
Morgen-Report zum Planen", kein „Abendplanung: Tagesplan für morgen") — du haettest
dann zwei Laeufe fuer dieselbe Sache, und im Kalender stehen doppelte Eintraege.
Brauchst du zusaetzlich einen INHALTLICHEN Bericht, nenn ihn auch so.

**Uhrzeiten in `create_schedule` sind DEINE Ortszeit.** Lass `timezone` weg, dann
rechnet der Server in deiner Zone. Traegst du von Hand „UTC" ein, feuert ein Zeitplan
namens „(07:00)" im Sommer um neun — genau so ist es passiert.

## Self-diagnosis
- To see YOUR OWN recent container logs (e.g. after a failed task/tool call), use the `read_logs` tool. A team lead can also pass a team member's agent id. Use it to find the real error (401, stack trace, missing env) and fix it.
$MOUNTS_SECTION

## MCP Tools (IMPORTANT!)
I have powerful MCP (Model Context Protocol) tools available. These appear as native
tools in my tool list - I use them like any other tool (no bash commands needed).

**CRITICAL: I MUST use MCP tools for memory, NOT the Write tool or MEMORY.md!**
The built-in auto-memory (MEMORY.md) is NOT visible in the Web UI.
Only `memory_save` stores data that the user can see in the Memory tab.

**CRITICAL: I MUST NEVER use the built-in TodoWrite tool!**
The built-in TodoWrite is LOCAL ONLY - the user CANNOT see it in the Web UI Todo tab!
ALWAYS use the MCP tools `update_todos` / `complete_todo` / `list_todos` instead.
These save to the database and are visible in the Todo tab in real-time.

### Memory Tools (mcp-memory)
I have persistent long-term memory that survives across ALL conversations and tasks.
**I MUST use `memory_save` to remember important things!**
**I MUST NEVER use Write/Edit to write to MEMORY.md for memory storage!**

- **memory_save** - Save important information to memory (ALWAYS USE THIS!)
  - Categories: preference, contact, project, procedure, decision, fact, learning
  - When to save: user preferences, corrections/learnings, contacts, project context,
    recurring procedures, important decisions, facts (company info, URLs, etc.)
  - Use importance 1-5 (1=trivial, 3=normal, 5=critical)
- **memory_search** - Search memories by keyword and/or category
  - At the start of a NEW conversation or a real task, if you need context beyond the auto-loaded memories
  - Only when THIS request needs info you don't already have — not before every reply
- **memory_list** - List all memories, optionally filtered by category
- **memory_delete** - Delete a specific memory by ID

### Notification Tools (mcp-notifications)
- **send_telegram** - Send a DIRECT message to the user via Telegram chat
  - Use this for **live progress updates** during work (e.g. "Step 1/3 done", "Building...", "Found issue, fixing")
  - **ALWAYS use this frequently** to keep the user informed about what you are doing!
  - Send updates at every major step, not just at the end
  - The user expects regular status messages via Telegram
- **notify_user** - Send notification to the Web UI notification center (+ Telegram for high/urgent)
  - Types: info (blue), warning (amber), error (red), success (green)
  - Priorities: low, normal, high (Telegram), urgent (Telegram + flashing)
  - Use for completed tasks, errors, important events
- **request_approval** - Ask user to approve a critical action before proceeding
  - Presents clickable options in the UI (e.g. ["Send now", "Edit first", "Cancel"])
  - High-impact actions (sending emails, deleting files, purchases, external API calls) normally need approval — BUT your per-task AUTONOMY block is authoritative: if it says you are fully autonomous (L4), do them WITHOUT asking; if it lists a whitelist, follow that. Never ask for something your autonomy level already allows.
- **escalate_if_unsure** - Report your confidence (0-100) instead of GUESSING
  - This is the tool for *uncertainty*, not for *risk*. Risky-but-clear → `request_approval`. Unclear → this one.
  - Use it when: the instruction has several plausible readings, you are missing information you cannot look up, or you are about to pick one interpretation and hope it was the right one.
  - **The server decides, not you.** If your confidence meets the operator's threshold, the call returns instantly and nobody is bothered — so use it freely. Only below the threshold does it reach a human, and then it blocks until they answer.
  - **Be honest with the number.** Inflating it defeats the whole point; a guessed result is worse than a question, because it looks like work and is not.

### Orchestrator Tools (mcp-orchestrator)
- **create_task** - Create a new task (for self or another agent)
- **list_tasks** - List tasks assigned to me (filter by status)
- **list_team** - See the agents visible to you (your team members plus other teams' leads) with roles and status
- **list_my_team** - See ONLY the members of the team(s) YOU belong to (with roles + who is the lead). **When someone asks "who is on your team / who are your colleagues / which agents do you have", ALWAYS call `list_my_team` first and answer from its result — never from memory.** As a team lead, this is how you know your own team.
- **write_knowledge** - Save/update an entry in the shared Knowledge Base (upsert by title; appears in the Knowledge graph). Use for durable, searchable knowledge — e.g. importing wiki pages (read via a MediaWiki MCP, then write each page here) or storing a meeting protocol.
- **send_message** - Send a text message to another agent
- **create_schedule** - Create a recurring task schedule; use cron_expression for exact wall-clock times and interval_seconds for relative intervals
- **list_schedules** - List all recurring schedules
- **manage_schedule** - Pause, resume, or delete a schedule
- **trigger_create** - Set up an event trigger (fires a task when a matching webhook arrives) instead of polling on a timer
- **trigger_list** - List your event triggers
- **trigger_toggle** - Enable or disable an event trigger
- **trigger_delete** - Delete an event trigger

### TODO Tools (mcp-orchestrator) - VISIBLE IN WEB UI!
**⚠️ NEVER use the built-in TodoWrite tool - it is NOT visible to the user!**
**⚠️ ONLY use these MCP tools for TODOs - they save to the database!**
TODOs are persistent and displayed in the "Todos" tab for the user to see.
- **list_todos** - List my TODO items (filter by status or task_id). **Call this first when starting real WORK** (not for a quick question).
- **update_todos** - Add/replace pending TODOs (completed TODOs are preserved automatically)
  - ⚠️ **ALWAYS `list_todos` first** before using this! Existing TODOs are the user's work plan!
  - Only replaces pending/in_progress items, completed ones are never deleted
  - Include task_id to link TODOs to a specific task
- **complete_todo** - Mark a single TODO as completed by ID
**When starting a real task: `list_todos` first → work on existing ones → only add NEW if needed!** (Skip for trivial questions.)

### Knowledge Base Tools (mcp-knowledge) — SHARED ACROSS ALL AGENTS!
All agents share a central knowledge base. **USE THIS ACTIVELY!**
- **brain_search** - Search the shared knowledge base by keyword and/or tag
  - **ALWAYS search BEFORE asking the user** for information you might already know!
  - Search when you encounter a problem, need context, or start a new topic
  - Example: `brain_search(q: "telegram")` to find Telegram-related knowledge
- **brain_get** - Read a specific knowledge entry by exact title
  - Use when you know the title (e.g. from a [[backlink]] in another entry)
- **brain_contribute** - Write/update a knowledge entry (all agents can read it)
  - Use [[Title]] syntax to link between entries, #tags for categorization
  - Write company knowledge, processes, decisions, contacts, project docs here

### Microsoft 365 & External Sources (MCP) — CALL THEM, DON'T ANNOUNCE
You may have Microsoft 365 tools (name prefix `ms_`) plus other MCP sources (SharePoint, DMS, Web). These are DEFERRED tools: if the one you need isn't directly listed, find it with the tool-search FIRST and then CALL it in the SAME turn. NEVER reply that you "will search" or "kann als Nächstes suchen" without actually invoking the tool — the user reads that as doing nothing.
- **A person / colleague / "wer ist <Name>" / you need someone's email** → `ms_search_people` (searches relevant people, the org directory AND personal contacts).
- **"wer ist mein Vorgesetzter / mein Chef", your department, "who am I in M365"** → `ms_get_user_info` (also returns the manager).
- **Read or search email** → `ms_search` with `types=['message']` (works via the search index). On an on-prem mailbox `ms_list_emails` can 404 — then use `ms_search`.
- **Recently edited files** → `ms_recent_files`; **any file** → `ms_search` / `ms_search_files`.
- **Turn a meeting action-item into a task** → `ms_create_planner_task` with `description` and `assignee` (`assignee='me'` assigns it to yourself; other people need directory read rights).
- **Internal documents / policies** → the SharePoint-MCP or DMS-MCP search tools when present.
Rule of thumb: for ANY Microsoft/M365/people/mail/file/document question, invoke the matching `ms_`/MCP tool and answer from its actual result. Do not say "I can't see it" until a tool has actually returned an error.

### Legacy CLI (still available as fallback)
The `ai-team` bash command still works for all the above operations.
Run `ai-team help` for usage. Prefer MCP tools over CLI when possible.

## Knowledge Access (for real work — NOT every reply)
I have TWO knowledge sources. Use them when a task actually needs context (see "Effort proportional" above) — NOT before trivial questions.

### 1. Personal knowledge file: `/workspace/knowledge.md`
- **When starting a substantial task**, skim it once to recall my role, skills, and past learnings
- **After substantive work**, update it with new patterns, errors & fixes, and insights
- Sections to maintain: "Learned Patterns", "Errors & Fixes", role/responsibilities if they change
- This is my persistent profile — it makes me better over time

### 2. Shared Knowledge Base (MCP tools)
- **At the start of a NEW conversation or a real task** (once — not every turn), load context if you need it:
  1. `brain_search(q: "projects")` — user's active projects
  2. `brain_search(q: "preferences")` — user preferences & style
  3. `brain_search(q: "architecture")` — tech stack & decisions
  On later turns you already have this — don't repeat it. Skip it entirely for trivial questions.
- **`brain_search` BEFORE asking the user or giving up** — when you actually lack info the question needs.
- When I encounter a problem → search knowledge base first
- When I need to know how something works → search knowledge base first
- When the user asks about a topic → search knowledge base for existing entries
- **After learning something new → `brain_contribute` to share with all agents**

**Search is not the answer to every brain question.** `brain_search` returns snippets across
everything. Two follow-ups come up constantly and each has its own tool:
- "tell me more / read it to me" about ONE entry → `brain_get(id)` (full text), NOT the snippet
- "what is this connected to? / what else belongs to it?" → `brain_related(id)`
- "what is in the brain? / list it" → `brain_list(limit, offset)`

`brain_related` returns TWO groups: **LINKED** = the explicit `[[wikilinks]]`, i.e. exactly the
edges the knowledge graph draws — that is what a user pointing at the graph means — and
**SIMILAR** = semantically close entries. Keep the `id` from the search; you need it here.

### Self-Research Rule (CRITICAL!)
Before telling the user "I don't know" or "CLI not available" or sending error messages:
1. `brain_search` for the topic
2. `memory_search` for related memories
3. Read `/workspace/knowledge.md` for patterns and fixes
4. `grep` or `find` in the workspace for relevant files
5. **`WebSearch`** for current information, facts, documentation, or anything external
6. **`WebFetch`** to read a specific URL (docs, APIs, articles)
7. ONLY THEN ask the user if still stuck

**I CAN search the internet!** Use `WebSearch` freely for: current events, weather, prices,
documentation, error messages, library versions, or any real-world information.

## The user's computer — Desktop Bridge (`computer_*` tools)
If the Desktop Bridge is connected, I can operate the USER'S own machine — their real screen,
mouse and keyboard: `computer_screenshot`, `computer_open_app`, `computer_close_app`,
`computer_click`, `computer_type`, `computer_key`, `computer_scroll`, `computer_find_element`,
`computer_wait_for_element`, `computer_ax_tree`.

**Pick the right machine — this is the mistake to avoid:**
- "open X **in my browser**", "on **my** screen", "show me", "screenshot", "click/type here"
  → `computer_*` tools. NOT a browser skill, NOT `bash`, NOT `curl`.
- "read/summarise this page", "look it up on the web" → `WebFetch`/`WebSearch`/browser skill
  in MY container.

An **internal company URL** (intranet, ticket system) is the clearest case: I cannot reach it,
but the user's computer can. Open it with `computer_open_app` — never answer "that address
seems internal, I can't open it".

No session setup call needed — the tools find the connected bridge session themselves.

**If a `computer_*` tool fails, say so — never silently switch to another route.** The error
names exactly what is missing (no bridge session → the user opens the Computer-Use tab and
starts the Bridge app). Quietly falling back to my own container produces answers about a
screen the user is not looking at — worse than admitting the block.

**Never describe a screen whose screenshot failed.** No image means no description. Say the
screenshot failed instead of guessing what might be on it.

## Proactive Mode
I periodically wake up (via schedule) to check if there is work to do on my own.
The proactive prompt gives detailed instructions each time. Key principles:
- Survey what's outstanding and plan the run before acting; work the plan, highest priority first
- Finish something early? Pull the next planned item forward instead of stopping
- Nothing planned left? Propose a concrete next step to my Ansprechpartner via `notify_user`
  (`is_checkin: true`) instead of asking open-ended — and only once per half-day
- Respect my Ansprechpartner's configured working hours; outside them, only do work that needs
  no sign-off
- Set up my own `create_schedule` / `trigger_create` when I notice recurring or event-driven work
- Execute genuine work only (no busywork!)

## TODO Management (CRITICAL - NEVER USE TodoWrite!)
The built-in TodoWrite tool is BROKEN for this platform - it does NOT save to the database!

**ALWAYS check existing TODOs first before creating new ones!**
1. **FIRST: `list_todos`** - Check what TODOs already exist
2. **Work on existing TODOs** - Pick highest-priority pending item, mark in_progress, do the work
3. **Complete with `complete_todo`** - Mark individual items as done
4. **Only add NEW TODOs** if there is genuinely new work not already covered
5. **NEVER blindly replace** the entire TODO list

TODOs persist across sessions and container restarts. Previous TODOs are the user's work plan!
**If I accidentally use TodoWrite, the user sees NOTHING. Always use MCP tools!**

## Git Workflow (CRITICAL!)
- **Always push** - Never leave work only local. Run `git push` after committing.
- **Create PRs** for any non-trivial change: `gh pr create --title "..." --body "..."`
- **Reference issues** in commits and PRs: `fixes #N` auto-closes the issue
- **Only work on own repos** - Check ownership with `gh repo view --json owner` first
- **Never work on third-party repos** you don't own (forks, upstream, external)

## Skills (Slash Commands)
I have custom skills installed as slash commands in `/workspace/.claude/skills/`.
- **To see all my skills**: `ls /workspace/.claude/skills/`
- **To read a skill**: `cat /workspace/.claude/skills/<name>/SKILL.md`
- **To use a skill**: type `/<skill-name>` — Claude Code CLI automatically loads the SKILL.md as instructions
- **At the start of a conversation**, if the user asks about a topic, check `ls /workspace/.claude/skills/` first — I may already have a skill for it!
- Skills contain detailed step-by-step workflows. ALWAYS follow them precisely when invoked.

## General Work Principles
- **Understand before acting** - Read existing files, docs, and context before making changes
- **Be consistent** - Match the style and patterns already used in a project
- **Verify your work** - Run build/tests before committing. NEVER commit broken code
- **Save learnings** - Use `memory_save` (category: "learning") for important discoveries

## Workspace Organization (IMPORTANT!)
I MUST keep my workspace organized with proper directories:
- `/workspace/transfer/` - **All output files for the user go HERE** (PDFs, reports, exports, downloads)
- `/workspace/scripts/` - Python scripts, automation code, tools
- `/workspace/data/` - Raw data, downloaded content, caches
- `/workspace/docs/` - Documentation, notes, research

**Rules:**
- NEVER dump files directly in /workspace root - always use subdirectories
- When creating files the user requested (PDFs, reports, exports): put in `/workspace/transfer/`
- When creating scripts: put in `/workspace/scripts/`
- Create additional subdirectories as needed
- Use `mkdir -p` to create directories before writing files

## Disk Quota (IMPORTANT!)
My workspace has a soft quota of **$AGENT_WORKSPACE_SIZE_GB GB**.
- **Before large operations** (downloads, builds, cloning repos): check with `df -h /workspace` or `du -sh /workspace`
- **Check remaining space**: `echo "Used: $(du -sh /workspace 2>/dev/null | cut -f1)"`
- **Warning file**: If `/workspace/.disk_warning` exists, read it — I am running low on space and MUST clean up first
- **Clean up**: `rm -rf /workspace/data/cache /workspace/tmp && find /workspace -name '*.log' -delete`
- **Find large files**: `du -sh /workspace/* | sort -rh | head -10`
- If I ignore disk warnings and run out of space, my container will be stopped automatically
"""

DEFAULT_KNOWLEDGE_MD = """# Agent Knowledge Base

## Meine Rolle

Meine Rolle, meine Schwerpunkte und meine Grenzen stehen in meiner **Vorlage** und in
meiner Agenten-Konfiguration. Die gelten — ich frage sie nicht noch einmal ab.

**Kein Onboarding-Interview.** Frueher stand hier die Anweisung, bei der ersten
Unterhaltung Rolle, Aufgaben und Grenzen zu erfragen. Das war schon dann ueberfluessig,
wenn ein Agent aus einer Vorlage entsteht — dort ist all das bereits festgelegt —, und
es hat aktiv geschadet: am 2026-08-13 kam ein delegierter Auftrag mit der Rueckfrage
„fuer mich sind keine Verantwortungsbereiche hinterlegt, bitte festlegen" zurueck,
statt mit Arbeit. In einem Auftrag sitzt niemand, der antwortet.

Fehlt mir etwas fuer eine konkrete Aufgabe:
1. Ich **arbeite trotzdem** und waehle die sicherste vernuenftige Annahme.
2. Ich sage in EINER Zeile, was gefehlt hat und wie ich entschieden habe.
3. Braucht es wirklich eine Entscheidung, rufe ich `request_approval` — das erreicht
   einen Menschen und wartet. Eine Frage als Fliesstext erreicht in einem Auftrag
   niemanden.

## Verantwortungsbereiche

Was ich dauerhaft besitze, pflegt der Mensch in meinen Einstellungen (Bereich
„Verantwortungsbereiche"). Sind dort keine hinterlegt, arbeite ich auftragsbezogen —
das ist kein Fehler und kein Grund, eine Aufgabe anzuhalten.

## Learned Patterns
<!-- I update this section after each task with new learnings -->

## Errors & Fixes
<!-- Common errors and how I resolved them -->
"""


def strip_onboarding_block(knowledge: str) -> str | None:
    """Den ueberholten Onboarding-Abschnitt aus einer bestehenden knowledge.md nehmen.

    ``None`` heisst: nichts zu tun.

    Die Datei liegt im Volume des Agenten und wird beim Neuerstellen bewusst NICHT
    ueberschrieben — dort steht Gelerntes drin. Folge: Agenten, die vor dem
    Wegfall des Interviews entstanden sind, tragen die Anweisung weiter mit sich
    herum und halten damit weiter Auftraege an, um nach ihrer Rolle zu fragen.

    Deshalb wird hier **nur der Kopf** ersetzt: alles ab dem ersten Abschnitt, den
    der Agent selbst gefuellt haben koennte, bleibt Zeichen fuer Zeichen stehen.
    Eine Datei, die den Block nicht (mehr) hat, wird nicht angefasst.
    """
    if not knowledge or "Onboarding Status: NOT COMPLETED" not in knowledge:
        return None

    # Erster Abschnitt, der dem Agenten gehoert. Ab da wird nichts mehr angefasst.
    keep_from = len(knowledge)
    for marker in ("## Learned Patterns", "## Errors & Fixes", "## My Role"):
        pos = knowledge.find(marker)
        if pos != -1:
            keep_from = min(keep_from, pos)
    tail = knowledge[keep_from:] if keep_from < len(knowledge) else ""

    head = DEFAULT_KNOWLEDGE_MD.split("## Learned Patterns")[0]
    return head + tail if tail else DEFAULT_KNOWLEDGE_MD


def _build_mounts_section(mount_labels: list[str], catalog: dict | None = None) -> str:
    """Return a CLAUDE.md section listing mounted host directories, or empty string.

    ``catalog`` should be the effective catalog (env + DB Second Brains). When not
    provided it falls back to the static env catalog so callers without a DB session
    still work.
    """
    if not mount_labels:
        return ""
    if catalog is None:
        from app.core.mounts import parse_mount_catalog
        catalog = parse_mount_catalog(settings.agent_mount_catalog)
    lines = ["\n## Host Mounts"]
    lines.append("The following directories from the host are mounted into this container:")
    has_brain = False
    for label in mount_labels:
        entry = catalog.get(label)
        if entry:
            mode_note = "(read-only)" if entry.mode == "ro" else "(read-write)"
            is_brain = label.startswith("brain-")
            note = " — shared department Second Brain (Markdown knowledge base)" if is_brain else ""
            lines.append(f"- `{entry.container_path}` — {label} {mode_note}{note}")
            has_brain = has_brain or is_brain
    lines.append("\nWhen the user asks about their local files, always check these paths first.")
    if has_brain:
        lines.append(
            "\n### Second Brain lookup (IMPORTANT)\n"
            "A shared department knowledge base is mounted above (a `brain-*` path under "
            "`/mnt/brains/`). For support, how-to or troubleshooting questions (e.g. error "
            "codes like `x17137`), **search it FIRST** before answering: use `grep` for "
            "keywords/error codes and `read_file` on the matches, then answer from the found "
            "`.md` content and cite the file. If you learn something new and have read-write "
            "access, **write it back**: use your normal file/Write tool to create or update a "
            "concise `.md` article directly at `/mnt/brains/<slug>/<Ordner>/<thema>.md` "
            "(Markdown, `[[wikilinks]]` between topics). No special tool needed — the vault is "
            "mounted read-write, so a plain file write preserves the knowledge for the whole department."
        )
    return "\n".join(lines)


def agent_timezone(config: dict | None) -> str:
    """Die Zeitzone DIESES Agenten — dieselbe Reihenfolge wie ueberall sonst."""
    from app.core.plan_rhythm import timezone_name
    return timezone_name(config)


def instructions_paths(mode: str | None) -> list[str]:
    """Wohin die Agenten-Anleitung geschrieben wird — EINE Quelle für alle Pfade.

    Jeder Harness liest eine ANDERE Datei, und er liest ausschliesslich seine eigene:
      * Claude Code  → `/workspace/CLAUDE.md`
      * Codex CLI    → `/workspace/AGENTS.md` (Mehrzahl! Das ist die Konvention der
        Codex-CLI. Wir haben jahrelang nur `AGENT.md` geschrieben — der Codex-Agent
        hat die Anleitung damit nie gelesen, auch nicht die Bildschirm-Regeln.)
      * Custom-LLM   → `/workspace/AGENT.md`, wird von `runner_hooks.get_identity_context()`
        aktiv eingelesen und in den Systemprompt gehaengt.

    Geschrieben wird immer auch `AGENT.md`: sie ist der modellneutrale Name, auf den sich
    Werkzeuge und Prompts beziehen, und der Custom-LLM-Weg liest genau sie. Ein unbekannter
    oder fehlender Modus faellt auf `AGENT.md` zurueck statt auf gar keine Anleitung.
    """
    if mode == "claude_code":
        # AGENT.md kommt mit: Claude Code liest zwar nur CLAUDE.md, aber auf dem Pi lagen
        # bei jedem Claude-Agenten uralte AGENT.md-Reste aus der Zeit, als sie fuer alle
        # Modi geschrieben wurde. Wer sie ansieht (Nutzer, Werkzeug, spaeterer Umbau),
        # liest sonst eine Anleitung von vor mehreren Fassungen.
        return ["/workspace/CLAUDE.md", "/workspace/AGENT.md"]
    if mode in ("codex_cli", "codex"):
        return ["/workspace/AGENTS.md", "/workspace/AGENT.md"]
    return ["/workspace/AGENT.md"]


def instructions_path(mode: str | None) -> str:
    """Der primaere Pfad (Rueckwaertskompatibilitaet) — siehe ``instructions_paths``."""
    return instructions_paths(mode)[0]


def _identity_line(name: str, role: str) -> str:
    """The one sentence that tells an agent who it is.

    Lives in the shared instruction file, so EVERY harness gets it from the same
    place — Claude Code via CLAUDE.md, Codex via AGENTS.md, custom_llm because
    runner_hooks.get_identity_context() inlines the file into its system prompt.
    """
    name = (name or "").strip()
    role = (role or "").strip()
    if not name and not role:
        return "Du bist ein KI-Agent dieser Plattform."
    who = f'Du bist „{name}"' if name else "Du bist ein KI-Agent dieser Plattform"
    return f"{who} — {role}." if role else f"{who}."


def _render_claude_md(agent_mounts: list[str], catalog: dict | None = None,
                      workspace_size_gb: float | None = None,
                      agent_name: str = "", agent_role: str = "") -> str:
    """Render the agent CLAUDE.md from its template — the SINGLE place that fills
    its placeholders (identity, workspace soft-quota, host-mounts section). Used by
    the create / update / restart paths, so the substitution lives in exactly one spot.

    ``workspace_size_gb`` defaults to the global setting; pass a per-agent value to
    honour an individual agent's quota override (``config["workspace_size_gb"]``).
    """
    size = settings.agent_workspace_size_gb if workspace_size_gb is None else workspace_size_gb
    return (
        DEFAULT_CLAUDE_MD
        .replace("$AGENT_IDENTITY", _identity_line(agent_name, agent_role))
        .replace("$AGENT_WORKSPACE_SIZE_GB", str(size))
        .replace("$MOUNTS_SECTION", _build_mounts_section(agent_mounts, catalog))
    )


def _container_slug(name: str) -> str:
    """Derive a VALID docker-name fragment from a (possibly rich) agent display name.

    Docker names must match [a-zA-Z0-9][a-zA-Z0-9_.-]+ — a display name with slashes,
    umlauts, spaces or other characters would otherwise produce an invalid/injectable
    container name at (re)creation. We lower-case, replace any run of non-[a-z0-9]
    chars with a single hyphen and trim, falling back to "agent" if nothing remains.
    """
    import re as _re
    slug = _re.sub(r"[^a-z0-9]+", "-", (name or "").lower()).strip("-")
    return slug or "agent"


class AgentManager:
    """Manages the lifecycle of agent Docker containers."""

    def __init__(self, db: AsyncSession, docker: DockerService, redis: RedisService):
        self.db = db
        self.docker = docker
        self.redis = redis

    async def _agent_redis_url(self, agent_id: str) -> str:
        """REDIS_URL for one agent's container.

        Behind settings.redis_acl_enabled (default off, see config.py /
        Sentinel epic #588 sub-issue #589): provisions a least-privilege
        per-agent Redis ACL user and returns credentials scoped to it,
        instead of every agent sharing the one admin credential.

        Fail-closed once the flag is explicitly on: if ACL provisioning
        fails (Sentinel-HA not yet supported, Redis unreachable, ...), the
        exception propagates and the caller's create/restart/update fails
        loudly instead of silently handing the agent the shared admin
        credential — a security flag that quietly degrades to "no security"
        on error would defeat its own purpose. With the flag at its default
        (False) this method never touches Redis, so this change has zero
        effect until an operator explicitly opts in.
        """
        if not settings.redis_acl_enabled:
            return settings.redis_url_internal
        return await self.redis.ensure_agent_acl_user(agent_id)

    @staticmethod
    def _mode_for_ai_provider(provider_type: str | None, requested_mode: str = "custom_llm") -> str:
        """Map account provider to the harness that should run it."""
        provider = (provider_type or "").lower()
        if provider == "anthropic":
            return "claude_code"
        if provider == "openai":
            return "codex_cli"
        return requested_mode if requested_mode in {"claude_code", "codex_cli"} else "custom_llm"

    @staticmethod
    def _model_provider_for_mode(mode: str, effective_llm: dict | None = None) -> str:
        provider = (effective_llm or {}).get("provider_type")
        if mode == "codex_cli":
            return "codex"
        if mode == "claude_code":
            return "anthropic"
        return provider or settings.model_provider

    @staticmethod
    def _cli_account_env(mode: str, effective_llm: dict | None) -> dict[str, str]:
        """Expose API-key based accounts to CLI harnesses."""
        if not effective_llm:
            return {}
        provider = (effective_llm.get("provider_type") or "").lower()
        api_key = effective_llm.get("api_key") or ""
        model = effective_llm.get("model_name") or ""

        if mode == "claude_code" and provider == "anthropic":
            env = {"DEFAULT_MODEL": model}
            if api_key:
                env["ANTHROPIC_API_KEY"] = api_key
            return env

        if mode == "codex_cli" and provider == "openai":
            env = {"CODEX_HOME": "/home/agent/.codex", "DEFAULT_MODEL": model}
            if api_key:
                env["OPENAI_API_KEY"] = api_key
            return env

        return {}

    @staticmethod
    def _build_provider_env(agent_provider: str | None = None) -> dict[str, str]:
        """Build environment variables for the active model provider.

        Uses per-agent provider if set, otherwise falls back to global settings.

        - ``anthropic`` (default): ANTHROPIC_API_KEY *or* CLAUDE_CODE_OAUTH_TOKEN
        - ``bedrock``: CLAUDE_CODE_USE_BEDROCK + AWS credentials
        - ``vertex``:  CLAUDE_CODE_USE_VERTEX + GCP credentials
        - ``foundry``: CLAUDE_CODE_USE_FOUNDRY + Azure Foundry credentials
        """
        provider = agent_provider or settings.model_provider

        if provider == "codex":
            return {"CODEX_HOME": "/home/agent/.codex"}

        if provider == "bedrock":
            env: dict[str, str] = {"CLAUDE_CODE_USE_BEDROCK": "1"}
            if settings.aws_access_key_id:
                env["AWS_ACCESS_KEY_ID"] = settings.aws_access_key_id
            if settings.aws_secret_access_key:
                env["AWS_SECRET_ACCESS_KEY"] = settings.aws_secret_access_key
            if settings.aws_region:
                env["AWS_REGION"] = settings.aws_region
            return env

        if provider == "vertex":
            env = {
                "CLAUDE_CODE_USE_VERTEX": "1",
                "CLOUD_ML_REGION": settings.vertex_region or "us-east5",
            }
            if settings.vertex_project_id:
                env["ANTHROPIC_VERTEX_PROJECT_ID"] = settings.vertex_project_id
            if settings.vertex_credentials_json:
                # The agent entrypoint will write this to a file and set
                # GOOGLE_APPLICATION_CREDENTIALS accordingly.
                env["GOOGLE_CREDENTIALS_JSON"] = settings.vertex_credentials_json
            return env

        if provider == "foundry":
            env = {"CLAUDE_CODE_USE_FOUNDRY": "1"}
            if settings.foundry_api_key:
                env["ANTHROPIC_FOUNDRY_API_KEY"] = settings.foundry_api_key
            if settings.foundry_resource:
                env["ANTHROPIC_FOUNDRY_RESOURCE"] = settings.foundry_resource
            return env

        # Default: Anthropic Direct
        env = {}
        if settings.anthropic_api_key:
            env["ANTHROPIC_API_KEY"] = settings.anthropic_api_key
        elif settings.claude_code_oauth_token:
            env["CLAUDE_CODE_OAUTH_TOKEN"] = settings.claude_code_oauth_token
        return env

    async def _owner_credential_env(
        self, owner_id: str | None, mode: str | None, model_provider: str | None
    ) -> dict[str, str]:
        """Der Zugang des **Besitzers** dieses Agenten — oder die Teamlizenz.

        Wird NACH ``_build_provider_env`` gemischt und überschreibt dessen Werte.
        Damit gilt die Reihenfolge aus :mod:`app.core.agent_credentials`: eigener
        Zugang → Teamlizenz (falls der Administrator sie freigegeben hat) → nichts.

        Massgeblich ist der Besitzer, nicht der gerade Eingeloggte: ein Agent
        arbeitet auch nachts weiter, wenn niemand angemeldet ist.

        Warum das mehr ist als Bequemlichkeit: bisher teilten sich **alle**
        Codex-Agenten einen rotierenden Refresh-Token. Erneuert ihn einer, sind
        die anderen tot — deshalb muss das Neuerstellen bis heute serialisiert
        werden. Getrennte Zugänge sind getrennte Token-Familien; der Ausfall
        eines Abos trifft dann genau einen Agenten.
        """
        from app.core import agent_credentials as creds

        try:
            source, harness, secret = await creds.resolve(
                self.db, owner_id=owner_id, mode=mode, model_provider=model_provider
            )
        except Exception:  # noqa: BLE001
            # Ein Fehler beim Aufloesen darf keinen Agenten am Start hindern —
            # dann greift wie bisher die globale Einstellung.
            logger.warning("[Zugang] Aufloesen fehlgeschlagen, globale Einstellung gilt",
                           exc_info=True)
            return {}

        if not harness:
            return {}
        if not secret:
            logger.info("[Zugang] %s: kein Zugang fuer Besitzer %s (%s)",
                        harness, scrub_log(owner_id or "-"), source)
            return {}
        logger.info("[Zugang] %s laeuft mit %s Zugang",
                    harness, "eigenem" if source == creds.SOURCE_PERSONAL else "Team-")
        return creds.env_for(harness, secret)

    async def _effective_llm_config(
        self, ai_account_id: int | None, inline_llm_config: dict | None,
        agent_model: str | None = None,
    ) -> dict | None:
        """Resolve the effective custom-LLM config with a PLAINTEXT api_key.

        A linked AIAccount (admin-managed, reusable) takes precedence over an
        agent's inline llm_config. ``agent_model`` is the model the agent
        picked from the account's model list. Returns None if neither is set.
        """
        from app.core.encryption import decrypt_token as _decrypt
        from app.models.ai_account import AIAccount

        if ai_account_id:
            acc = await self.db.get(AIAccount, ai_account_id)
            if acc:
                extra = acc.extra or {}
                models = acc.models or []
                # Each model entry may be a dict {name, provider_type,
                # api_endpoint} carrying its own surface, or a legacy string.
                entry = None
                for m in models:
                    nm = m.get("name") if isinstance(m, dict) else m
                    if nm == agent_model:
                        entry = m
                        break
                if entry is None and models:
                    entry = models[0]
                if isinstance(entry, dict):
                    mname = entry.get("name", "") or ""
                    prov = entry.get("provider_type") or acc.provider_type
                    endp = entry.get("api_endpoint") or acc.api_endpoint or ""
                elif isinstance(entry, str):
                    mname, prov, endp = entry, acc.provider_type, acc.api_endpoint or ""
                else:
                    mname, prov, endp = (agent_model or ""), acc.provider_type, acc.api_endpoint or ""
                return {
                    "provider_type": prov,
                    "api_endpoint": endp,
                    "api_key": _decrypt(acc.api_key_encrypted) if acc.api_key_encrypted else "",
                    "model_name": mname,
                    "max_tokens": extra.get("max_tokens") or 0,   # 0 = keine eigene Grenze
                    "temperature": extra.get("temperature", 0.7),
                    "system_prompt": extra.get("system_prompt", ""),
                    "tools_enabled": extra.get("tools_enabled", True),
                    "thinking_mode": extra.get("thinking_mode", "auto"),
                    "reasoning_effort": extra.get("reasoning_effort", ""),
                    "api_version": extra.get("api_version", ""),
                    "deployment": mname,
                }
        if inline_llm_config:
            cfg = dict(inline_llm_config)
            if cfg.get("api_key_encrypted") and not cfg.get("api_key"):
                cfg["api_key"] = _decrypt(cfg["api_key_encrypted"])
            return cfg
        return None

    @staticmethod
    def _llm_env(cfg: dict) -> dict[str, str]:
        """Build the LLM_* container env vars from a resolved llm config dict."""
        return {
            "LLM_PROVIDER_TYPE": cfg.get("provider_type", ""),
            "LLM_API_ENDPOINT": cfg.get("api_endpoint", "") or "",
            "LLM_API_KEY": cfg.get("api_key", "") or "",
            "LLM_MODEL_NAME": cfg.get("model_name", ""),
            # 0 = keine eigene Grenze, dann entscheidet das Modell. Die frueheren
            # 4096 stammten aus einer Zeit, in der Modelle nicht mehr konnten —
            # fuer ein Review oder eine Datei brach die Antwort damit mitten im
            # Satz ab, und das sah aus wie ein fertiges Ergebnis.
            "LLM_MAX_TOKENS": str(cfg.get("max_tokens") or 0),
            "LLM_TEMPERATURE": str(cfg.get("temperature", 0.7)),
            "LLM_SYSTEM_PROMPT": cfg.get("system_prompt", "") or "",
            "LLM_TOOLS_ENABLED": str(cfg.get("tools_enabled", True)).lower(),
            "LLM_THINKING_MODE": cfg.get("thinking_mode", "auto"),
            "LLM_REASONING_EFFORT": cfg.get("reasoning_effort", "") or "",
            # Ausweichmodelle bei Rate-Limit/Ueberlastung (#200). Liste oder
            # kommagetrennter Text, beides erlaubt — leer heisst kein Ausweichen.
            "LLM_FALLBACK_MODELS": ",".join(cfg["fallback_models"])
            if isinstance(cfg.get("fallback_models"), list)
            else (cfg.get("fallback_models") or ""),
            # Azure OpenAI specifics (empty for other providers)
            "LLM_API_VERSION": cfg.get("api_version", "") or "",
            "LLM_DEPLOYMENT": cfg.get("deployment", "") or "",
            "DEFAULT_MODEL": cfg.get("model_name", ""),
        }

    async def _publish_event(self, agent_id: str, event_type: str, message: str) -> None:
        """Publish a lifecycle event to the agent's log channel."""
        if self.redis.client is None:
            # No connected Redis client (e.g. a freshly instantiated RedisService in
            # the lifecycle recreate path) — nothing to publish to. Debug, not warn,
            # so the periodic sweep does not spam the log.
            logger.debug(f"Skip publish event for agent {scrub_log(agent_id)}: no Redis client")
            return
        try:
            event = json.dumps({
                "agent_id": agent_id,
                "task_id": "",
                "type": event_type,
                "data": {"message": message},
                "timestamp": datetime.now(timezone.utc).isoformat(),
            })
            channel = f"agent:{agent_id}:logs"
            await self.redis.client.publish(channel, event)
            # Store in activity history (keep last 200 events)
            history_key = f"agent:{agent_id}:activity"
            await self.redis.client.rpush(history_key, event)
            await self.redis.client.ltrim(history_key, -200, -1)
        except Exception as e:
            logger.warning(f"Could not publish event for agent {scrub_log(agent_id)}: {scrub_log(e)}")

    async def _cancel_open_chats(self, agent_id: str, reason: str) -> None:
        """Broadcast a terminal ``cancelled`` event to any open chat streams for this
        agent, so the UI does not hang on "Thinking..." after a recreate/restart kills
        the in-flight response.

        Empty ``message_id`` is deliberate: the WS forwarder broadcasts message_id-less
        events to ALL chat connections of the agent (they bypass the per-session
        filter), and the frontend's ``cancelled`` handler then clears the waiting state.
        Published on the same channel (``agent:{id}:chat:response``) the agent container
        streams responses on — no new mechanism.
        """
        if self.redis.client is None:
            logger.debug(f"Skip cancel open chats for agent {scrub_log(agent_id)}: no Redis client")
            return
        try:
            event = json.dumps({
                "agent_id": agent_id,
                "message_id": "",
                "type": "cancelled",
                "data": {"message": reason},
                "timestamp": datetime.now(timezone.utc).isoformat(),
            })
            await self.redis.client.publish(f"agent:{agent_id}:chat:response", event)
        except Exception as e:  # noqa: BLE001 — best effort, never block the restart
            logger.warning(f"Could not cancel open chats for agent {scrub_log(agent_id)}: {scrub_log(e)}")

    async def _get_custom_mcp_env(self, agent_config: dict | None = None, agent_id: str | None = None, agent_integrations: list[str] | None = None) -> dict[str, str]:
        """Load custom MCP servers and return as env var dict.

        If agent_config contains 'mcp_servers' (list of IDs), only those
        servers are included. Otherwise all enabled servers are returned.
        Automatically injects the MS Graph MCP server when microsoft is connected.
        """
        result = await self.db.execute(
            select(McpServer).where(McpServer.enabled == True)
        )
        servers = result.scalars().all()

        # Per-agent filtering
        agent_mcp_ids = None
        if agent_config and "mcp_servers" in agent_config:
            agent_mcp_ids = set(agent_config["mcp_servers"])

        if agent_mcp_ids is not None:
            servers = [s for s in servers if s.id in agent_mcp_ids]

        # Role/group restriction: limit to MCP servers the agent owner's group allows
        # (custom_role.permissions.mcp_server_ids; None = all). Admins are unrestricted.
        if agent_id:
            try:
                from app.models.agent import Agent as _Ag
                from app.models.user import User as _U
                from app.core.permissions import get_effective_permissions
                ag = await self.db.get(_Ag, agent_id)
                owner = await self.db.get(_U, ag.user_id) if (ag and ag.user_id) else None
                if owner:
                    perms = await get_effective_permissions(owner, self.db)
                    allowed = perms.get("mcp_server_ids")
                    if allowed is not None:
                        allowed_set = set(allowed)
                        servers = [s for s in servers if s.id in allowed_set]
            except Exception as e:
                logger.warning(f"MCP role filter failed for agent {scrub_log(agent_id)}: {e}")

        # OAuth-protected servers (#426): mint/refresh a fresh access token before
        # handing it to the agent, so a short-lived token never arrives already
        # expired. Best-effort — a refresh failure leaves the (possibly stale)
        # token in place rather than blocking agent startup.
        try:
            from app.services.mcp_oauth_refresh import refresh_if_needed
            for s in servers:
                if getattr(s, "oauth_enabled", False):
                    await refresh_if_needed(s, self.db)
        except Exception as e:
            logger.warning(f"MCP OAuth token refresh pass failed: {e}")

        mcp_map = {s.name: s.url for s in servers}
        # Bearer tokens passed alongside so the agent can authenticate per server.
        auth_map = {
            s.name: decrypt_token(s.auth_token_encrypted)
            for s in servers if s.auth_token_encrypted
        }
        # Custom auth headers (x-api-key etc.) for servers that don't use Bearer.
        headers_map: dict[str, dict] = {}
        for s in servers:
            if getattr(s, "headers_encrypted", None):
                try:
                    headers_map[s.name] = json.loads(decrypt_token(s.headers_encrypted))
                except Exception:  # noqa: BLE001 — skip a corrupt entry, don't break the agent
                    logger.warning("Could not decode custom headers for MCP server %s", s.name)

        # Auto-inject MS Graph MCP server when agent has microsoft integration.
        # The agent's MCP client authenticates with the agent's HMAC bearer token
        # (via CUSTOM_MCP_AUTH); without it the msgraph endpoint returns 401.
        if agent_id and agent_integrations and "microsoft" in agent_integrations:
            mcp_map["msgraph"] = f"http://ai-employee-orchestrator:8000/api/v1/mcp/msgraph/{agent_id}"
            auth_map["msgraph"] = make_agent_token(agent_id)

        # Auto-inject the on-prem Exchange MCP when the agent has the exchange
        # integration. Per-user: the endpoint resolves the agent owner's mailbox.
        if agent_id and agent_integrations and "exchange_onprem" in agent_integrations:
            mcp_map["exchange_onprem"] = f"http://ai-employee-orchestrator:8000/api/v1/mcp/exchange-onprem/{agent_id}"
            auth_map["exchange_onprem"] = make_agent_token(agent_id)

        if not mcp_map:
            return {}
        env = {"CUSTOM_MCP_SERVERS": json.dumps(mcp_map)}
        if auth_map:
            env["CUSTOM_MCP_AUTH"] = json.dumps(auth_map)
        if headers_map:
            env["CUSTOM_MCP_HEADERS"] = json.dumps(headers_map)
        return env

    async def _get_integration_env(self, agent_integrations: list[str], user_id: str | None = None) -> dict[str, str]:
        """Get environment variables for agent integrations (e.g., GitHub token)."""
        env: dict[str, str] = {}
        if "github" in agent_integrations:
            result = await self.db.execute(
                select(OAuthIntegration).where(
                    OAuthIntegration.provider == OAuthProvider.GITHUB,
                    OAuthIntegration.user_id.is_(None),
                )
            )
            integration = result.scalar_one_or_none()
            if integration:
                token = decrypt_token(integration.access_token_encrypted)
                provider = get_git_host_provider(
                    integration.host_type or "github", integration.base_url
                )
                env.update(provider.get_agent_env(token))
        if "microsoft" in agent_integrations and user_id:
            from sqlalchemy import and_ as _sqland
            result = await self.db.execute(
                select(OAuthIntegration).where(
                    _sqland(
                        OAuthIntegration.provider == OAuthProvider.MICROSOFT,
                        OAuthIntegration.user_id == user_id,
                    )
                )
            )
            if result.scalar_one_or_none():
                env["MSGRAPH_ENABLED"] = "true"
        return env

    async def _get_secrets_env(self, agent_id: str) -> dict[str, str]:
        """Decrypt and inject KMS secrets assigned to this agent as env vars."""
        result = await self.db.execute(
            select(AgentSecretAssignment).where(AgentSecretAssignment.agent_id == agent_id)
        )
        assignments = result.scalars().all()
        if not assignments:
            return {}

        secret_ids = [a.secret_id for a in assignments]
        s_result = await self.db.execute(
            select(AgentSecret).where(
                AgentSecret.id.in_(secret_ids),
                AgentSecret.is_active.is_(True),
            )
        )
        env: dict[str, str] = {}
        for secret in s_result.scalars().all():
            try:
                env[secret.key_name] = decrypt_token(secret.value_encrypted)
            except Exception:
                logger.warning("Failed to decrypt secret %s (%s)", secret.name, secret.key_name)
        github_token = env.get("GITHUB_TOKEN") or env.get("GH_TOKEN") or env.get("GIT_PAT")
        if github_token:
            env.setdefault("GITHUB_TOKEN", github_token)
            env.setdefault("GH_TOKEN", github_token)
        return env

    async def create_agent(self, name: str, model: str | None = None, role: str | None = None, integrations: list[str] | None = None, permissions: list[str] | None = None, user_id: str | None = None, budget_usd: float | None = None, budget_exceeded_action: str = "haiku", mode: str = "claude_code", llm_config: dict | None = None, ai_account_id: int | None = None, browser_mode: bool = False, autonomy_level: str = "l3",
                           knowledge_md: str | None = None) -> Agent:
        agent_id = uuid.uuid4().hex[:8]
        container_name = f"ai-agent-{_container_slug(name)}-{agent_id}"
        volume_name = f"workspace-{agent_id}"
        session_volume = f"claude-session-{agent_id}"
        model = model or settings.default_model

        # A linked AI account drives the harness choice:
        # Anthropic -> Claude Code, OpenAI -> Codex CLI, everything else -> custom harness.
        if ai_account_id:
            from app.models.ai_account import AIAccount
            account = await self.db.get(AIAccount, ai_account_id)
            mode = self._mode_for_ai_provider(account.provider_type if account else None, mode)
        elif mode == "claude_code" and settings.model_provider == "codex":
            mode = "codex_cli"

        # Last-line defense: never launch a harness with a model it can't run.
        # e.g. a codex_cli agent with the platform default claude-sonnet-4-6, or
        # a claude_code agent handed a GPT model. custom_llm is left untouched.
        from app.core.model_catalog import coerce_model_for_mode
        model = coerce_model_for_mode(mode, model)

        # Resolve the effective LLM config (account takes precedence over inline).
        effective_llm = await self._effective_llm_config(ai_account_id, llm_config, model)

        # Encrypt API key for inline custom_llm config before storing.
        # Account-linked agents store no inline config — the account is the source.
        encrypted_llm_config = None
        if mode == "custom_llm" and not ai_account_id and llm_config:
            from app.core.encryption import encrypt_token
            encrypted_llm_config = dict(llm_config)
            if encrypted_llm_config.get("api_key"):
                encrypted_llm_config["api_key_encrypted"] = encrypt_token(
                    encrypted_llm_config.pop("api_key")
                )
        if mode == "custom_llm" and effective_llm:
            # Use the custom model name as the display model
            model = effective_llm.get("model_name", model)

        # Build environment based on mode
        env_vars: dict[str, str] = {
            "AGENT_ID": agent_id,
            "AGENT_NAME": name,
            "AGENT_ROLE": role or "",
            "AGENT_TOKEN": make_agent_token(agent_id),
            "REDIS_URL": await self._agent_redis_url(agent_id),
            "ORCHESTRATOR_URL": "http://ai-employee-orchestrator:8000",
            "AGENT_MODE": mode,
            "MAX_TURNS": str(settings.max_turns),
            "MAX_PARALLEL_CHATS": str(settings.max_parallel_chats),
            "MAX_PARALLEL_TASKS": str(settings.max_parallel_tasks),
            "AUTONOMY_LEVEL": autonomy_level.lower(),
            # Der Container tickt in SEINER Zeitzone. Ohne das lief `date` im Agenten
            # in UTC: er schrieb „07:00" in einen Zeitplan und meinte neun, und in
            # jedem Bericht stand eine Uhrzeit, die zwei Stunden daneben lag.
            #
            # ``None``, nicht ``config``: hier wird der Agent gerade erst angelegt,
            # eine eigene Zeitzone hat er noch nicht — es gilt die Vorgabe. Die
            # Variable ``config`` existiert an dieser Stelle NICHT (sie wird erst
            # weiter unten fuer den proaktiven Zeitplan gesetzt), und der Zugriff
            # darauf liess jedes Anlegen eines Agenten mit 500 scheitern:
            # „cannot access local variable 'config'".
            "TZ": agent_timezone(None),
        }

        if mode == "custom_llm" and effective_llm:
            # Custom LLM: LLM-specific env vars + integrations + MCP servers
            mcp_env = await self._get_custom_mcp_env(agent_id=agent_id, agent_integrations=integrations)
            integration_env = await self._get_integration_env(integrations or [], user_id=user_id)
            env_vars.update({
                **self._llm_env(effective_llm),
                **mcp_env,
                **integration_env,
            })
        else:
            # Claude Code: standard provider env + MCP + integrations
            # Per-agent provider from config, fallback to global
            model_provider = self._model_provider_for_mode(mode, effective_llm)
            provider_env = {
                **self._build_provider_env(model_provider),
                **self._cli_account_env(mode, effective_llm),
                # Zuletzt, damit der eigene Zugang des Besitzers die globale
                # Einstellung ueberschreibt (#eigene-Abos).
                **await self._owner_credential_env(user_id, mode, model_provider),
            }
            mcp_env = await self._get_custom_mcp_env(agent_id=agent_id, agent_integrations=integrations)
            integration_env = await self._get_integration_env(integrations or [], user_id=user_id)
            env_vars.update({
                **provider_env,
                **mcp_env,
                **integration_env,
                "DEFAULT_MODEL": model,
                "EXTENDED_THINKING": str(settings.extended_thinking).lower(),
                "COMPUTER_USE_BROWSER": "true" if browser_mode else "false",
                "COMPUTER_USE_USER_ID": str(user_id) if user_id else "",
                "AGENT_WORKSPACE_SIZE_GB": str(settings.agent_workspace_size_gb),
            })

        # Create Docker container with workspace + session + shared volumes
        # Container sudo follows the autonomy matrix — see
        # core.autonomy_matrix.effective_permissions. An explicit list from the
        # create modal means the user picked by hand, which pins the agent to
        # manual mode; without one the level's matrix decides.
        permissions_mode = "manual" if permissions is not None else "auto"
        agent_permissions = (
            list(permissions) if permissions is not None
            else autonomy_matrix.effective_permissions({}, autonomy_level)
        )
        needs_sudo = len(agent_permissions) > 0
        container = self.docker.create_container(
            image=settings.agent_image,
            name=container_name,
            environment=env_vars,
            volume_name=volume_name,
            session_volume_name=session_volume,
            shared_volume_name="ai-employee-shared",
            network=settings.agent_network,
            memory_limit=settings.agent_memory_limit,
            cpu_quota=settings.agent_cpu_quota,
            needs_sudo=needs_sudo,
            bind_mounts=None,  # No mounts on initial create; assigned via PATCH /agents/{id}/mounts
        )

        # Apply permission packages (write sudoers file as root)
        try:
            self._apply_permissions(container.id, agent_permissions)
        except Exception as e:
            logger.warning(f"Could not apply permissions for agent {scrub_log(agent_id)}: {e}")

        # Initialize workspace files
        agent_mounts = []
        claude_md = _render_claude_md(agent_mounts, agent_name=name, agent_role=role or "")
        # Same instruction text for EVERY harness — only the file name differs, because
        # each CLI reads its own (see instructions_paths). Two copies of this branch used
        # to decide it; now there is one list and no mode can quietly fall through.
        try:
            for _path in instructions_paths(mode):
                self.docker.write_file_in_container(container.id, _path, claude_md)
            # Kommt der Agent aus einer Vorlage, gilt DEREN Beschreibung. Die
            # leere Vorgabe wuerde sie ueberschreiben — und genau deshalb stand
            # bei jedem Vorlagen-Agenten "Onboarding NOT COMPLETED", obwohl Rolle,
            # Schwerpunkte und Grenzen laengst festgelegt waren.
            self.docker.write_file_in_container(
                container.id, "/workspace/knowledge.md",
                (knowledge_md or "").strip() or DEFAULT_KNOWLEDGE_MD,
            )
            logger.info(
                "Initialized %s + knowledge.md for agent %s (mode=%s)",
                ", ".join(instructions_paths(mode)), agent_id, mode,
            )
        except Exception as e:
            logger.warning(f"Could not initialize agent files: {e}")

        # write_file_in_container writes as root; the agent runs as uid 1000 and MUST be
        # able to write its own /workspace/knowledge.md (otherwise "save results to
        # knowledge.md" fails with Permission denied). Hand the workspace to the agent.
        try:
            self.docker.exec_in_container(container.id, ["chown", "-R", "1000:1000", "/workspace"], user="root")
        except Exception as e:
            logger.warning(f"Could not chown workspace for agent {agent_id}: {e}")

        # Update shared team registry
        try:
            self._update_team_registry(container.id, agent_id, name, role or "Unassigned")
        except Exception as e:
            logger.warning(f"Could not update team registry: {e}")

        # Save to DB
        agent = Agent(
            id=agent_id,
            name=name,
            container_id=container.id,
            volume_name=volume_name,
            user_id=user_id,
            state=AgentState.RUNNING,
            model=model,
            mode=mode,
            llm_config=encrypted_llm_config,
            ai_account_id=ai_account_id,
            budget_usd=budget_usd,
            budget_exceeded_action=budget_exceeded_action,
            browser_mode=browser_mode,
            autonomy_level=autonomy_level.lower(),
            config={
                "session_volume": session_volume,
                "role": role or "",
                # Das Einrichtungsgespraech ist entfallen — der Agent haelt sich an
                # seine Vorlage. Ein Agent, der hier auf `false` stuende, bekaeme
                # ein Interview eingeblendet, das es nicht mehr gibt, und seine
                # proaktiven Laeufe wuerden dauerhaft uebersprungen: nichts koennte
                # den Haken je setzen. Was er tun soll, steht in Rolle und Vorlage.
                "onboarding_complete": True,
                "model_provider": self._model_provider_for_mode(mode, effective_llm),
                "integrations": integrations or [],
                "permissions": agent_permissions,
                "permissions_mode": permissions_mode,
                "agent_version": get_agent_version(),
                "metrics": {"total": 0, "success": 0, "fail": 0, "success_rate": 0.0},
            },
        )
        self.db.add(agent)
        await self.db.commit()
        await self.db.refresh(agent)

        # Create proactive schedule (auto-enabled, 1h default)
        try:
            now = datetime.now(timezone.utc)
            schedule_id = uuid.uuid4().hex[:8]
            proactive_schedule = Schedule(
                id=schedule_id,
                name=f"[Proactive] {name}",
                prompt=PROACTIVE_PROMPT,
                interval_seconds=3600,
                priority=0,
                agent_id=agent_id,
                enabled=True,
                next_run_at=now + timedelta(minutes=10),
            )
            self.db.add(proactive_schedule)

            config = dict(agent.config)
            config["proactive"] = {
                "enabled": True,
                "schedule_id": schedule_id,
                "interval_seconds": 3600,
            }
            agent.config = config
            flag_modified(agent, "config")
            await self.db.commit()
            await self.db.refresh(agent)
            logger.info(f"Created proactive schedule {schedule_id} for agent {agent_id}")
        except Exception as e:
            logger.warning(f"Could not create proactive schedule: {e}")

        await self._publish_event(agent_id, "system", f"Agent created: {name} (model: {model or 'default'})")
        return agent

    def _apply_permissions(self, container_id: str, permissions: list[str]) -> None:
        """Write sudoers file into container based on permission packages."""
        sudoers_content = generate_sudoers(permissions)
        if sudoers_content:
            # Write sudoers file via tar archive (avoids shell escaping issues)
            self.docker.write_file_in_container(
                container_id,
                "/etc/sudoers.d/agent-permissions",
                sudoers_content,
            )
            # Fix ownership and permissions (must be root:root, 0440)
            self.docker.exec_in_container(
                container_id,
                "chmod 0440 /etc/sudoers.d/agent-permissions",
                user="root",
            )
            logger.info(f"Applied permissions {scrub_log(permissions)} to container {scrub_log(container_id[:12])}")
        else:
            # No permissions - remove any existing sudoers file
            self.docker.exec_in_container(
                container_id,
                "rm -f /etc/sudoers.d/agent-permissions",
                user="root",
            )
            logger.info(f"Removed all sudo permissions from container {container_id[:12]}")

    def _update_team_registry(self, container_id: str, agent_id: str, name: str, role: str) -> None:
        """Update the shared team.json with this agent's info."""
        import json as json_module

        # Read existing team.json (or start fresh)
        try:
            _, content = self.docker.exec_in_container(container_id, "cat /shared/team.json")
            team = json_module.loads(content) if content.strip() else {"agents": []}
        except Exception:
            team = {"agents": []}

        # Remove old entry for this agent if exists
        team["agents"] = [a for a in team["agents"] if a.get("id") != agent_id]

        # Add new entry
        team["agents"].append({
            "id": agent_id,
            "name": name,
            "role": role,
            "status": "online",
        })

        self.docker.write_file_in_container(
            container_id, "/shared/team.json", json_module.dumps(team, indent=2)
        )

    async def restart_agent(self, agent_id: str) -> Agent:
        """Restart agent by recreating its container with fresh env vars.

        Preserves all data (volumes, knowledge, config) but picks up
        new environment (MCP servers, integrations, etc).
        """
        await self._publish_event(agent_id, "system", "Agent restarting (recreating container with fresh config)...")
        # Clear any open chat's "Thinking..." before we kill the in-flight response.
        await self._cancel_open_chats(agent_id, "Agent wird neu gestartet — laufende Antwort abgebrochen.")
        agent = await self._get_agent(agent_id)
        config = agent.config or {}
        container_name = f"ai-agent-{_container_slug(agent.name)}-{agent_id}"

        # 1. Stop and remove old container (volumes stay!)
        # Reconcile by BOTH the stored container_id AND the deterministic name:
        # a stale container_id can point at an already-removed container while a
        # container under the fixed name still exists, which would make the create
        # below collide with a 409 (name already in use). Same pattern as update_agent.
        for ref in [agent.container_id, container_name]:
            if not ref:
                continue
            try:
                self.docker.stop_container(ref)
            except (NotFound, APIError):
                pass
            try:
                self.docker.remove_container(ref)
            except (NotFound, APIError):
                pass

        # 2. Build fresh environment based on mode
        volume_name = agent.volume_name
        session_volume = config.get("session_volume", f"claude-session-{agent_id}")
        role = config.get("role", "")
        model = agent.model or settings.default_model
        mode = agent.mode or "claude_code"
        if mode == "claude_code" and (config.get("model_provider") or settings.model_provider) == "codex":
            mode = "codex_cli"
        from app.core.model_catalog import coerce_model_for_mode
        model = coerce_model_for_mode(mode, model)

        env_vars: dict[str, str] = {
            "AGENT_ID": agent_id,
            "AGENT_NAME": agent.name,
            "AGENT_ROLE": role,
            "AGENT_TOKEN": make_agent_token(agent_id),
            "REDIS_URL": await self._agent_redis_url(agent_id),
            "ORCHESTRATOR_URL": "http://ai-employee-orchestrator:8000",
            "AGENT_MODE": mode,
            "TZ": agent_timezone(config),      # siehe oben: der Container tickt lokal
            "MAX_TURNS": str(settings.max_turns),
            # Per-agent parallelism (config['parallel_sessions']) overrides the
            # global default; applies to both tasks and chats. Beyond it, work
            # queues in Redis. New agents (create path) use the global default.
            "MAX_PARALLEL_CHATS": str(config.get("parallel_sessions") or settings.max_parallel_chats),
            "MAX_PARALLEL_TASKS": str(config.get("parallel_sessions") or settings.max_parallel_tasks),
            "AUTONOMY_LEVEL": (agent.autonomy_level or "l3").lower(),
        }

        secrets_env = await self._get_secrets_env(agent_id)

        effective_llm = await self._effective_llm_config(agent.ai_account_id, agent.llm_config, agent.model)
        if mode == "custom_llm" and effective_llm:
            mcp_env = await self._get_custom_mcp_env(agent_config=config, agent_id=agent_id, agent_integrations=config.get("integrations", []))
            integration_env = await self._get_integration_env(config.get("integrations", []), user_id=agent.user_id)
            env_vars.update({
                **self._llm_env(effective_llm),
                **mcp_env,
                **integration_env,
                **secrets_env,
            })
        else:
            agent_provider = self._model_provider_for_mode(mode, effective_llm)
            provider_env = {
                **self._build_provider_env(agent_provider),
                **self._cli_account_env(mode, effective_llm),
                **await self._owner_credential_env(agent.user_id, mode, agent_provider),
            }
            mcp_env = await self._get_custom_mcp_env(agent_config=config, agent_id=agent_id, agent_integrations=config.get("integrations", []))
            integration_env = await self._get_integration_env(config.get("integrations", []), user_id=agent.user_id)
            env_vars.update({
                **provider_env,
                **mcp_env,
                **integration_env,
                **secrets_env,
                "DEFAULT_MODEL": model,
                "EXTENDED_THINKING": str(settings.extended_thinking).lower(),
            })

        # 3. Create new container with same volumes + any assigned bind mounts
        agent_permissions = autonomy_matrix.effective_permissions(
            config, agent.autonomy_level or "l3"
        )
        needs_sudo = len(agent_permissions) > 0
        from app.core.mounts import get_effective_catalog, resolve_agent_mounts, mounts_to_docker_volumes
        catalog = await get_effective_catalog(self.db)
        mount_entries = resolve_agent_mounts(config.get("mounts", []), catalog, config.get("mount_modes", {}))
        bind_mounts = mounts_to_docker_volumes(mount_entries) or None
        def _create_agent_container():
            return self.docker.create_container(
                image=settings.agent_image,
                name=container_name,
                environment=env_vars,
                volume_name=volume_name,
                session_volume_name=session_volume,
                shared_volume_name="ai-employee-shared",
                network=settings.agent_network,
                memory_limit=settings.agent_memory_limit,
                cpu_quota=settings.agent_cpu_quota,
                needs_sudo=needs_sudo,
                bind_mounts=bind_mounts,
            )
        if mode == "codex_cli":
            # Serialize Codex recreation: only ONE codex container comes up at a time
            # (+ settle delay) so their CLIs never refresh the shared single-use token
            # in parallel — the cause of the fleet-wide refresh_token_reused outage.
            async with _codex_recreate_lock:
                container = _create_agent_container()
                await asyncio.sleep(_CODEX_RECREATE_STAGGER_S)
        else:
            container = _create_agent_container()

        # 4. Re-apply permissions
        try:
            self._apply_permissions(container.id, agent_permissions)
        except Exception as e:
            logger.warning(f"Could not apply permissions for agent {scrub_log(agent_id)}: {e}")

        # 5. Update team registry
        try:
            self._update_team_registry(container.id, agent_id, agent.name, role or "Unassigned")
        except Exception as e:
            logger.warning(f"Could not update team registry: {e}")

        # 5b. Refresh instructions file with latest DEFAULT_CLAUDE_MD (CLAUDE.md for
        # Claude Code, AGENT.md for Custom LLM — model-agnostic naming).
        try:
            agent_mounts = config.get("mounts", [])
            fresh_claude_md = _render_claude_md(
                agent_mounts, catalog, agent_name=agent.name, agent_role=role or ""
            )
            mode = agent.mode or config.get("mode", "claude_code")
            for target_file in instructions_paths(mode):
                self.docker.write_file_in_container(container.id, target_file, fresh_claude_md)
            await self.migrate_knowledge_file(container.id, agent_id)

            # Clean up old CLAUDE.md if this is now a custom_llm agent (one-time migration)
            if mode != "claude_code":
                try:
                    self.docker.exec_in_container(container.id, ["rm", "-f", "/workspace/CLAUDE.md"])
                except Exception:
                    pass
        except Exception as e:
            logger.warning(f"Could not refresh instructions file for agent {scrub_log(agent_id)}: {scrub_log(e)}")

        # 6. Update DB
        agent.container_id = container.id
        agent.state = AgentState.RUNNING
        await self.db.commit()
        await self.db.refresh(agent)

        logger.info(f"Agent {scrub_log(agent_id)} restarted with fresh config")
        await self._publish_event(agent_id, "system", "Agent restarted successfully with updated config")
        return agent

    async def stop_agent(self, agent_id: str) -> Agent:
        await self._publish_event(agent_id, "system", "Agent stopping...")
        agent = await self._get_agent(agent_id)
        if agent.container_id:
            try:
                self.docker.stop_container(agent.container_id)
            except (NotFound, APIError) as e:
                logger.warning(f"Container {scrub_log(agent.container_id)} not found when stopping agent {scrub_log(agent_id)}: {scrub_log(e)}")
        agent.state = AgentState.STOPPED
        await self.db.commit()
        await self._publish_event(agent_id, "system", "Agent stopped")
        return agent

    async def start_agent(self, agent_id: str) -> Agent:
        await self._publish_event(agent_id, "system", "Agent starting...")
        agent = await self._get_agent(agent_id)
        if not agent.container_id:
            # No container exists — recreate it (keeps volumes/data)
            logger.info(f"Agent {scrub_log(agent_id)} has no container — recreating via update_agent")
            return await self.update_agent(agent_id)
        try:
            self.docker.start_container(agent.container_id)
        except NotFound:
            logger.warning(f"Container {scrub_log(agent.container_id)} not found for agent {scrub_log(agent_id)} — recreating")
            return await self.update_agent(agent_id)
        await self.refresh_instructions(agent)
        agent.state = AgentState.RUNNING
        await self.db.commit()
        await self._publish_event(agent_id, "system", "Agent started")
        return agent

    async def migrate_knowledge_file(self, container_id: str, agent_id: str) -> bool:
        """Den entfallenen Onboarding-Abschnitt aus der Wissensdatei nehmen.

        Bewusst an EINER Stelle und von beiden Wegen gerufen (``restart_agent``
        und ``update_agent`` ueber ``refresh_instructions``). Die erste Fassung
        hing nur in ``restart_agent`` — das Neuerstellen laeuft aber ueber
        ``update_agent``, und so passierte beim Kunden schlicht nichts. Genau
        dieselbe Falle wie zwei Mal zuvor an diesem Wochenende.
        """
        try:
            _, current = self.docker.exec_in_container(
                container_id, ["cat", "/workspace/knowledge.md"]
            )
            migrated = strip_onboarding_block(current or "")
            if not migrated:
                return False
            self.docker.write_file_in_container(
                container_id, "/workspace/knowledge.md", migrated
            )
            logger.info("[Wissen] Onboarding-Abschnitt bei %s entfernt", scrub_log(agent_id))
            return True
        except Exception as e:  # noqa: BLE001
            logger.warning("[Wissen] knowledge.md von %s nicht migriert: %s",
                           scrub_log(agent_id), scrub_log(e))
            return False

    async def refresh_instructions(self, agent: Agent) -> bool:
        """Schreibt die aktuelle Anleitung in einen BESTEHENDEN Container.

        Bisher passierte das nur beim Neuerstellen (Update/Restart). Wer nach einem
        Deploy bloss `git pull` machte, liess seine laufenden Agenten mit der alten
        Anleitung zurueck — die Datei liegt im Container, nicht im Repo. Genau die
        Falle, in die sonst jede Installation tappt, die sich selbst aktualisiert.

        Best effort: schlaegt es fehl, laeuft der Agent trotzdem weiter.
        """
        if not agent.container_id:
            return False
        try:
            from app.core.mounts import get_effective_catalog
            catalog = await get_effective_catalog(self.db)
            mode = agent.mode or (agent.config or {}).get("mode", "claude_code")
            rendered = _render_claude_md(
                (agent.config or {}).get("mounts", []), catalog,
                agent_name=agent.name, agent_role=(agent.config or {}).get("role", ""),
            )
            for path in instructions_paths(mode):
                self.docker.write_file_in_container(agent.container_id, path, rendered)
            # Wissensdatei mitziehen — sie liegt im Volume und ueberlebt sonst
            # jede Aenderung an der Vorlage.
            await self.migrate_knowledge_file(agent.container_id, agent.id)
            return True
        except Exception as e:  # noqa: BLE001 — Anleitung ist wichtig, aber nicht kritisch
            logger.warning(f"Could not refresh instructions for agent {agent.id}: {e}")
            return False

    async def update_agent(self, agent_id: str) -> Agent:
        """Recreate agent container with latest image, preserving all data (volumes)."""
        agent = await self._get_agent(agent_id)
        config = agent.config or {}

        # Zugangstoken VOR dem Neuerstellen auf Stand bringen.
        #
        # Anthropic rotiert beim Erneuern — sobald der neue Token da ist, ist der
        # alte tot. Faellt die Erneuerung mit dem Neustart zusammen, startet der
        # frische Container auf einem Token, der Sekunden spaeter ungueltig wird,
        # und der erste Zug des Nutzers stirbt mit „401 access token has been
        # revoked". Genau so ist es beim Ausrollen von 1.177.0 passiert.
        #
        # Hier einmal aktiv erneuern heisst: der neue Container liest den frischen
        # Token, und die naechste planmaessige Erneuerung ist wieder Stunden weg.
        try:
            from app.services.claude_token_service import ClaudeTokenService
            await ClaudeTokenService().refresh_access_token()
        except Exception as exc:  # noqa: BLE001 — ein alter Token ist besser als kein Update
            logger.warning("[Auth] Token vor dem Neuerstellen nicht erneuerbar: %s", exc)

        # Clear any open chat's "Thinking..." before we kill the in-flight response.
        await self._cancel_open_chats(agent_id, "Agent wird aktualisiert — laufende Antwort abgebrochen.")

        # 1. Stop and remove old container (volumes stay!)
        container_name = f"ai-agent-{_container_slug(agent.name)}-{agent_id}"
        # Try by container_id first, then by name (handles stale IDs)
        for ref in [agent.container_id, container_name]:
            if not ref:
                continue
            try:
                self.docker.stop_container(ref)
            except (NotFound, APIError):
                pass
            try:
                self.docker.remove_container(ref)
            except (NotFound, APIError):
                pass

        # 2. Build environment based on mode (same logic as restart)
        volume_name = agent.volume_name
        session_volume = config.get("session_volume", f"claude-session-{agent_id}")
        role = config.get("role", "")
        model = agent.model or settings.default_model
        mode = agent.mode or "claude_code"
        if mode == "claude_code" and (config.get("model_provider") or settings.model_provider) == "codex":
            mode = "codex_cli"
        from app.core.model_catalog import coerce_model_for_mode
        model = coerce_model_for_mode(mode, model)

        env_vars: dict[str, str] = {
            "AGENT_ID": agent_id,
            "AGENT_NAME": agent.name,
            "AGENT_ROLE": role,
            "AGENT_TOKEN": make_agent_token(agent_id),
            "REDIS_URL": await self._agent_redis_url(agent_id),
            "ORCHESTRATOR_URL": "http://ai-employee-orchestrator:8000",
            "AGENT_MODE": mode,
            "TZ": agent_timezone(config),      # siehe oben: der Container tickt lokal
            "MAX_TURNS": str(settings.max_turns),
            # Per-agent parallelism (config['parallel_sessions']) overrides the
            # global default; applies to both tasks and chats. Beyond it, work
            # queues in Redis. New agents (create path) use the global default.
            "MAX_PARALLEL_CHATS": str(config.get("parallel_sessions") or settings.max_parallel_chats),
            "MAX_PARALLEL_TASKS": str(config.get("parallel_sessions") or settings.max_parallel_tasks),
            "AUTONOMY_LEVEL": (agent.autonomy_level or "l3").lower(),
        }

        secrets_env = await self._get_secrets_env(agent_id)

        effective_llm = await self._effective_llm_config(agent.ai_account_id, agent.llm_config, agent.model)
        if agent.ai_account_id:
            # Derive the harness from the ACCOUNT's provider, not from the
            # per-model entry. A GPT deployment on an Azure/Foundry account is
            # naturally tagged provider_type "openai" on the model row — taking
            # that literally flipped the agent to the Codex CLI harness, which
            # silently dropped LLM_API_ENDPOINT/LLM_API_KEY (they only get
            # injected on the custom_llm branch below) and then hid the
            # AI-account card in the UI, so it couldn't be repaired either.
            from app.models.ai_account import AIAccount as _AIAccount
            _acc = await self.db.get(_AIAccount, agent.ai_account_id)
            _account_provider = _acc.provider_type if _acc else None
            mode = self._mode_for_ai_provider(_account_provider, mode)
            agent.mode = mode
            config["model_provider"] = self._model_provider_for_mode(mode, effective_llm)

        if mode == "custom_llm" and effective_llm:
            mcp_env = await self._get_custom_mcp_env(agent_config=config, agent_id=agent_id, agent_integrations=config.get("integrations", []))
            integration_env = await self._get_integration_env(config.get("integrations", []), user_id=agent.user_id)
            env_vars.update({
                **self._llm_env(effective_llm),
                **mcp_env,
                **integration_env,
                **secrets_env,
            })
        else:
            agent_provider = self._model_provider_for_mode(mode, effective_llm)
            provider_env = {
                **self._build_provider_env(agent_provider),
                **self._cli_account_env(mode, effective_llm),
                **await self._owner_credential_env(agent.user_id, mode, agent_provider),
            }
            mcp_env = await self._get_custom_mcp_env(agent_config=config, agent_id=agent_id, agent_integrations=config.get("integrations", []))
            integration_env = await self._get_integration_env(config.get("integrations", []), user_id=agent.user_id)
            env_vars.update({
                **provider_env,
                **mcp_env,
                **integration_env,
                **secrets_env,
                "DEFAULT_MODEL": model,
                "EXTENDED_THINKING": str(settings.extended_thinking).lower(),
            })

        # 3. Create new container with same volumes + any assigned bind mounts
        agent_permissions = autonomy_matrix.effective_permissions(
            config, agent.autonomy_level or "l3"
        )
        needs_sudo = len(agent_permissions) > 0
        from app.core.mounts import get_effective_catalog, resolve_agent_mounts, mounts_to_docker_volumes
        catalog = await get_effective_catalog(self.db)
        mount_entries = resolve_agent_mounts(config.get("mounts", []), catalog, config.get("mount_modes", {}))
        bind_mounts = mounts_to_docker_volumes(mount_entries) or None
        def _create_agent_container():
            return self.docker.create_container(
                image=settings.agent_image,
                name=container_name,
                environment=env_vars,
                volume_name=volume_name,
                session_volume_name=session_volume,
                shared_volume_name="ai-employee-shared",
                network=settings.agent_network,
                memory_limit=settings.agent_memory_limit,
                cpu_quota=settings.agent_cpu_quota,
                needs_sudo=needs_sudo,
                bind_mounts=bind_mounts,
            )
        if mode == "codex_cli":
            # Serialize Codex recreation: only ONE codex container comes up at a time
            # (+ settle delay) so their CLIs never refresh the shared single-use token
            # in parallel — the cause of the fleet-wide refresh_token_reused outage.
            async with _codex_recreate_lock:
                container = _create_agent_container()
                await asyncio.sleep(_CODEX_RECREATE_STAGGER_S)
        else:
            container = _create_agent_container()

        # 4. Re-apply permission packages from config
        try:
            self._apply_permissions(container.id, agent_permissions)
        except Exception as e:
            logger.warning(f"Could not apply permissions for agent {scrub_log(agent_id)}: {e}")

        # 5. Refresh the instructions file — for EVERY mode (knowledge.md preserved).
        #    Claude Code reads /workspace/CLAUDE.md, Codex and Custom-LLM read
        #    /workspace/AGENT.md. This used to run only for claude_code, so a Codex
        #    agent kept the instructions it was born with: every later improvement to
        #    DEFAULT_CLAUDE_MD silently passed it by, no matter how often it was updated.
        _instructions_files = instructions_paths(mode)
        try:
            _agent_mounts = (agent.config or {}).get("mounts", [])
            _rendered = _render_claude_md(
                _agent_mounts, catalog,
                agent_name=agent.name, agent_role=(agent.config or {}).get("role", ""),
            )
            for _path in _instructions_files:
                self.docker.write_file_in_container(container.id, _path, _rendered)
            logger.info(
                f"Updated {', '.join(_instructions_files)} for agent {scrub_log(agent_id)} "
                "(knowledge.md preserved)"
            )
        except Exception as e:
            logger.warning(f"Could not update {', '.join(_instructions_files)}: {e}")

        # 6. Update team registry
        try:
            self._update_team_registry(container.id, agent_id, agent.name, role or "Unassigned")
        except Exception as e:
            logger.warning(f"Could not update team registry: {e}")

        # 7. Update DB
        agent.container_id = container.id
        agent.state = AgentState.RUNNING
        config["agent_version"] = get_agent_version()
        agent.config = config
        flag_modified(agent, "config")
        await self.db.commit()
        await self.db.refresh(agent)

        logger.info(f"Agent {scrub_log(agent_id)} updated to new image version")
        return agent

    async def update_llm_config(self, agent_id: str, updates: dict) -> dict:
        """Update LLM config fields for a custom_llm agent. Returns safe config (no key)."""
        from app.core.encryption import encrypt_token
        agent = await self._get_agent(agent_id)
        if agent.mode != "custom_llm":
            raise ValueError("Agent is not in custom_llm mode")

        llm_cfg = dict(agent.llm_config or {})

        # Handle API key update (encrypt it)
        if "api_key" in updates:
            llm_cfg["api_key_encrypted"] = encrypt_token(updates.pop("api_key"))

        # Merge other fields
        for key, value in updates.items():
            llm_cfg[key] = value

        agent.llm_config = llm_cfg
        flag_modified(agent, "llm_config")

        # Update model display name if model_name changed
        if "model_name" in updates:
            agent.model = updates["model_name"]

        await self.db.commit()
        await self.db.refresh(agent)

        # Restart container to pick up new config
        if agent.state in (AgentState.RUNNING, AgentState.IDLE, AgentState.WORKING):
            await self.restart_agent(agent_id)

        # Return safe config (no API key)
        return {
            "provider_type": llm_cfg.get("provider_type", ""),
            "api_endpoint": llm_cfg.get("api_endpoint", ""),
            "model_name": llm_cfg.get("model_name", ""),
            "max_tokens": llm_cfg.get("max_tokens") or 0,
            "temperature": llm_cfg.get("temperature", 0.7),
            "system_prompt": llm_cfg.get("system_prompt", ""),
            "tools_enabled": llm_cfg.get("tools_enabled", True),
            "reasoning_effort": llm_cfg.get("reasoning_effort", ""),
        }

    async def remove_agent(self, agent_id: str, remove_data: bool = False) -> None:
        await self._publish_event(agent_id, "system", f"Agent being removed (delete data: {remove_data})")
        if settings.redis_acl_enabled:
            await self.redis.revoke_agent_acl_user(agent_id)
        agent = await self._get_agent(agent_id)
        if agent.container_id:
            try:
                self.docker.remove_container(agent.container_id)
            except (NotFound, APIError) as e:
                logger.warning(f"Container {scrub_log(agent.container_id)} already gone for agent {scrub_log(agent_id)}: {scrub_log(e)}")
        if remove_data and agent.volume_name:
            self.docker.remove_volume(agent.volume_name)
            config = agent.config or {}
            session_vol = config.get("session_volume")
            if session_vol:
                self.docker.remove_volume(session_vol)
        # Clear/delete FK references before deleting the agent
        from app.models.task import Task
        from app.models.task_rating import TaskRating

        # Tasks: keep history but unlink (agent_id is nullable)
        await self.db.execute(
            sql_update(Task).where(Task.agent_id == agent_id).values(agent_id=None)
        )
        # Task ratings: delete (not nullable)
        await self.db.execute(
            sql_delete(TaskRating).where(TaskRating.agent_id == agent_id)
        )
        # Schedules: delete (already had FK constraint)
        await self.db.execute(
            sql_delete(Schedule).where(Schedule.agent_id == agent_id)
        )
        await self.db.delete(agent)
        await self.db.commit()

    async def get_agent_with_metrics(
        self,
        agent_id: str,
        include_stats: bool = True,
        image_id_resolved: bool = False,
        current_image_id: str | None = None,
    ) -> dict:
        agent = await self._get_agent(agent_id)

        # Sync DB state with actual Docker container status (lightweight check).
        # Use a separate session for state updates because this method is called
        # concurrently via asyncio.gather() and the shared request session is not
        # safe for parallel commits (causes IllegalStateChangeError + destroys
        # SET LOCAL RLS settings).
        if agent.container_id:
            container_status = self.docker.get_container_status(agent.container_id)
            new_state = None
            if container_status == "running" and agent.state in (AgentState.ERROR, AgentState.STOPPED):
                new_state = AgentState.IDLE
            elif container_status == "unknown" and agent.state not in (AgentState.STOPPED, AgentState.ERROR):
                new_state = AgentState.ERROR
            elif container_status == "exited" and agent.state not in (AgentState.STOPPED,):
                new_state = AgentState.STOPPED

            if new_state is not None:
                from app.db.session import async_session_factory
                from sqlalchemy import text as sa_text
                async with async_session_factory() as sync_session:
                    await sync_session.execute(
                        sa_text(
                            "UPDATE agents SET state = :state WHERE id = :aid"
                        ),
                        {"state": new_state.name, "aid": agent_id},
                    )
                    await sync_session.commit()
                agent.state = new_state
                logger.info(f"Agent {scrub_log(agent_id)} container status={scrub_log(container_status)}, state→{new_state.name}")

        config = agent.config or {}

        # Check if agent version is outdated
        update_available = False
        stored_version = config.get("agent_version")
        if stored_version != get_agent_version():
            update_available = True

        # Check if the running container is on a stale agent image (issue #433):
        # the ai-employee-agent:latest tag can be rebuilt without agents being
        # recreated, so a live agent may silently serve two-day-old code.
        # image_id_resolved lets a caller iterating many agents (the list endpoint)
        # resolve the ai-employee-agent:latest image id once and pass it in here,
        # instead of one images.get() call per agent (issue #449).
        image_outdated = False
        if agent.container_id and agent.state in (
            AgentState.RUNNING, AgentState.IDLE, AgentState.WORKING
        ):
            loop = asyncio.get_running_loop()
            try:
                if image_id_resolved and current_image_id is None:
                    image_outdated = False
                else:
                    image_outdated = await loop.run_in_executor(
                        None,
                        partial(
                            self.docker.is_container_image_outdated,
                            agent.container_id,
                            current_image_id=current_image_id,
                        ),
                    )
            except Exception:
                image_outdated = False

        # Build safe LLM config for response (no API key!)
        llm_config_response = None
        if agent.mode == "custom_llm" and agent.llm_config:
            llm_cfg = agent.llm_config
            llm_config_response = {
                "provider_type": llm_cfg.get("provider_type", ""),
                "api_endpoint": llm_cfg.get("api_endpoint", ""),
                "model_name": llm_cfg.get("model_name", ""),
                "max_tokens": llm_cfg.get("max_tokens") or 0,
                "temperature": llm_cfg.get("temperature", 0.7),
                "system_prompt": llm_cfg.get("system_prompt", ""),
                "tools_enabled": llm_cfg.get("tools_enabled", True),
                "reasoning_effort": llm_cfg.get("reasoning_effort", ""),
            }

        # Monthly spend (current calendar month) for budget display.
        # Uses a separate session — this method runs concurrently per agent.
        # Counts BOTH task runs and chat messages — chat is real spend too.
        from app.db.session import async_session_factory
        from app.models.task import Task as _Task
        from app.models.chat_message import ChatMessage as _ChatMessage
        from sqlalchemy import func as _func
        from datetime import datetime as _dt, timezone as _tz

        _month_start = _dt.now(_tz.utc).replace(
            day=1, hour=0, minute=0, second=0, microsecond=0
        )
        async with async_session_factory() as _cost_session:
            _task_cost = await _cost_session.execute(
                select(_func.coalesce(_func.sum(_Task.cost_usd), 0)).where(
                    _Task.agent_id == agent_id,
                    _Task.cost_usd.isnot(None),
                    _Task.created_at >= _month_start,
                )
            )
            _chat_cost = await _cost_session.execute(
                select(_func.coalesce(_func.sum(_ChatMessage.cost_usd), 0)).where(
                    _ChatMessage.agent_id == agent_id,
                    _ChatMessage.cost_usd.isnot(None),
                    _ChatMessage.timestamp >= _month_start,
                )
            )
            monthly_cost_usd = round(
                float(_task_cost.scalar() or 0) + float(_chat_cost.scalar() or 0), 4
            )

        # Linked AI account name/provider for display (badge on the agent card)
        ai_account_name = None
        ai_account_provider = None
        if agent.ai_account_id:
            from app.models.ai_account import AIAccount as _AIAccount
            async with async_session_factory() as _acc_session:
                _acc = await _acc_session.get(_AIAccount, agent.ai_account_id)
                if _acc:
                    ai_account_name = _acc.name
                    ai_account_provider = _acc.provider_type

        result = {
            "id": agent.id,
            "name": agent.name,
            "container_id": agent.container_id,
            "state": agent.state,
            "model": agent.model,
            "model_provider": (
                llm_config_response.get("provider_type", config.get("model_provider", settings.model_provider))
                if llm_config_response
                else config.get("model_provider", settings.model_provider)
            ),
            "mode": agent.mode or "claude_code",
            "llm_config": llm_config_response,
            "role": config.get("role", ""),
            "onboarding_complete": config.get("onboarding_complete", False),
            # Fehlte hier: die Liste baut ihre Felder aus DIESEM Wörterbuch, nicht aus
            # dem Antwort-Modell — die Kachel meldete deshalb "kein Auftrag", obwohl
            # elf Verantwortungsbereiche hinterlegt waren.
            "has_responsibilities": bool((config.get("proactive") or {}).get("responsibilities")),
            "integrations": config.get("integrations", []),
            # What the container will actually get on the next (re)create — not the
            # stale stored list, otherwise the UI shows a grant the box no longer has.
            "permissions": autonomy_matrix.effective_permissions(
                config, agent.autonomy_level or "l3"
            ),
            "permissions_mode": config.get("permissions_mode") or "auto",
            "update_available": update_available,
            "image_outdated": image_outdated,
            "budget_usd": agent.budget_usd,
            "budget_exceeded_action": agent.budget_exceeded_action,
            "monthly_cost_usd": monthly_cost_usd,
            "ai_account_id": agent.ai_account_id,
            "ai_account_name": ai_account_name,
            "ai_account_provider": ai_account_provider,
            "browser_mode": agent.browser_mode,
            "autonomy_level": agent.autonomy_level or "l3",
            "parallel_sessions": int(config.get("parallel_sessions") or settings.max_parallel_tasks),
            "webhook_enabled": agent.webhook_enabled,
            "webhook_token": agent.webhook_token,
            "shared_for_rooms": getattr(agent, "shared_for_rooms", False),
            "total_cost_usd": config.get("total_cost_usd", 0.0),
            "user_id": agent.user_id,
            "created_at": agent.created_at,
            "updated_at": agent.updated_at,
            "knowledge_template": config.get("knowledge_template", ""),
            "interaction_model": config.get("interaction_model"),
            "interaction_account_id": config.get("interaction_account_id"),
            "interaction_model_id": config.get("interaction_model_id"),
            "config": config,
        }

        # Add live metrics from Redis
        status = await self.redis.get_agent_status(agent_id)
        result["current_task"] = status.get("current_task", "")
        # All chat sessions the agent is processing right now (parallel-safe). Stored
        # JSON-encoded by the agent; tolerate the old format (field absent).
        try:
            _as = status.get("active_sessions")
            result["active_sessions"] = json.loads(_as) if _as else []
        except (ValueError, TypeError):
            result["active_sessions"] = []
        result["queue_depth"] = await self.redis.get_queue_depth(agent_id)

        # Sync live state from Redis (agent reports idle/working in real-time)
        redis_state = status.get("state", "")
        if redis_state in ("idle", "working") and agent.state in (
            AgentState.RUNNING, AgentState.IDLE, AgentState.WORKING
        ):
            result["state"] = redis_state

        # Add Docker stats if running (run in thread pool to avoid blocking)
        if include_stats and agent.container_id and agent.state in (AgentState.RUNNING, AgentState.IDLE, AgentState.WORKING):
            loop = asyncio.get_running_loop()
            try:
                stats = await loop.run_in_executor(
                    None, self.docker.get_container_stats, agent.container_id
                )
                result["cpu_percent"] = stats["cpu_percent"]
                result["memory_usage_mb"] = stats["memory_usage_mb"]
            except Exception:
                result["cpu_percent"] = None
                result["memory_usage_mb"] = None
            try:
                per_agent_quota = float(agent.config.get("workspace_size_gb") or settings.agent_workspace_size_gb) if agent.config else settings.agent_workspace_size_gb
                disk = await loop.run_in_executor(
                    None,
                    self.docker.get_workspace_disk_usage,
                    agent.container_id,
                    per_agent_quota,
                )
                result["disk_usage_mb"] = disk.get("disk_usage_mb")
                result["disk_limit_mb"] = disk.get("disk_limit_mb")
                result["disk_percent"] = disk.get("disk_percent")
            except Exception:
                result["disk_usage_mb"] = None
                result["disk_limit_mb"] = None
                result["disk_percent"] = None

        return result

    async def list_agents(self) -> list[Agent]:
        result = await self.db.execute(select(Agent).order_by(Agent.created_at.desc()))
        return list(result.scalars().all())

    async def _get_agent(self, agent_id: str) -> Agent:
        result = await self.db.execute(select(Agent).where(Agent.id == agent_id))
        agent = result.scalar_one_or_none()
        if not agent:
            raise ValueError(f"Agent {agent_id} not found")
        return agent
