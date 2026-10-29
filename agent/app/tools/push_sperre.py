"""Push-Sperre: kein ``git push`` in den Branch einer fremden Übernahme (#1117).

Am Repo eines Projekts arbeiten mehrere Seiten — Agenten dieser Anlage, andere
Anlagen, Claude-Code-Sitzungen, Menschen. Am 29.10. hat ein Plattform-Agent im
Arbeitstakt in die Branches einer fremden Übernahme gepusht; der Push der anderen
Sitzung wurde danach abgelehnt. Die Übernahme-Regeln standen in der ``AGENTS.md``
des Repos, durchgesetzt hat sie niemand.

Hier wird jeder Shell-Befehl auf ``git push`` untersucht (auch ``git -C dir push``,
``HEAD:ziel``, ``--force``, mehrere Befehle mit ``&&``/``;``, ``bash -c "…"``). Für
jeden Ziel-Branch fragt der Agent den Orchestrator
(``POST /api/v1/teams/repo/push-pruefen``, ``projekt_git.push_pruefen``): Läufe des
Arbeitstakts pushen gar nicht, und ein Branch mit Issue-Nummer gehört dem, der das
Issue übernommen hat.

EIN Entscheidungsweg für alle Laufzeiten: der PreToolUse-Hook
(``pretooluse_hook.decide_async``, Claude Code direkt, Codex über
``mcp/codex-hook.mjs``) und der Custom-LLM-Executor (``executor._tool_bash``).

Bewusst fail-open: Ist der Orchestrator nicht erreichbar, kennt er den Endpunkt
nicht (älterer Stand) oder lässt sich der Befehl nicht lesen, wird NICHT gesperrt,
nur protokolliert. Das Ziel ist, Zusammenstöße unter Kollegen zu vermeiden — keine
Sicherheitsgrenze: Wer eine Shell hat, kann am Hook vorbei pushen (Skript, Alias).
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import shlex
import subprocess

logger = logging.getLogger(__name__)

#: Umgebungsvariable mit der Kennung des laufenden Laufs (Task) — die Runner setzen
#: sie für die CLI; Claude Code reicht sie als Kopfzeile an den Hook weiter
#: (``allowedEnvVars``), der Codex-Hook liest sie selbst.
LAUF_ENV = "AI_EMPLOYEE_LAUF"
#: Kopfzeile, in der der Hook den Lauf bekommt.
LAUF_HEADER = "X-AI-Employee-Lauf"

_ZUWEISUNG = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
_DAUER = re.compile(r"^\d+(?:\.\d+)?[smhd]?$")
_HUELLEN = {"sudo", "command", "exec", "nohup", "time", "env", "nice", "timeout"}
_SHELLS = {"bash", "sh", "zsh", "dash"}
_OPERATOREN = {"&&", "||", ";", "|", "&", ";;", "(", ")", "|&"}
_PUSH_OPTIONEN_MIT_WERT = {"-o", "--push-option", "--repo", "--receive-pack", "--exec",
                           "--recurse-submodules", "--signed"}
_GIT_OPTIONEN_MIT_WERT = {"-C", "-c", "--git-dir", "--work-tree", "--namespace", "--config-env"}
_ZUGANGSDATEN = re.compile(r"(://)[^/@\s]+@")


def _woerter(befehl: str) -> list[list[str]]:
    """Befehlskette → einzelne Befehle als Wortlisten (Anführungszeichen beachtet)."""
    lexer = shlex.shlex(befehl.replace("\n", " ; "), posix=True, punctuation_chars=True)
    lexer.whitespace_split = True
    lexer.commenters = ""
    befehle, aktuell = [], []
    for wort in lexer:
        if wort in _OPERATOREN:
            if aktuell:
                befehle.append(aktuell)
            aktuell = []
        else:
            aktuell.append(wort)
    if aktuell:
        befehle.append(aktuell)
    return befehle


def _ohne_umleitungen(woerter: list[str]) -> list[str]:
    """``2>&1``, ``> datei`` usw. entfernen — sie sind keine Argumente."""
    ergebnis, i = [], 0
    while i < len(woerter):
        w = woerter[i]
        if w and set(w) <= set("<>&"):
            if ergebnis and ergebnis[-1].isdigit():
                ergebnis.pop()
            i += 2
            continue
        ergebnis.append(w)
        i += 1
    return ergebnis


def _git(verzeichnis: str, *argumente: str) -> str | None:
    """Ein lesender git-Aufruf im Arbeitsverzeichnis — None bei jedem Fehler."""
    if not verzeichnis or not os.path.isdir(verzeichnis):
        return None
    try:
        aus = subprocess.run(["git", "-C", verzeichnis, *argumente], capture_output=True, text=True,
                             timeout=3, check=False)
    except (OSError, subprocess.SubprocessError):
        return None
    return aus.stdout.strip() if aus.returncode == 0 else None


def _ohne_zugangsdaten(adresse: str | None) -> str | None:
    """``https://nutzer:token@host/…`` → ``https://host/…`` — Schlüssel gehen nicht mit."""
    if not adresse:
        return None
    return _ZUGANGSDATEN.sub(r"\1", adresse.strip())[:500]


def _remote_adresse(verzeichnis: str, remote: str | None) -> str | None:
    remote = remote or "origin"
    if "://" in remote or re.match(r"^[^/\s]+@[^:\s]+:", remote) or remote.startswith(("/", ".")):
        return _ohne_zugangsdaten(remote)
    return _ohne_zugangsdaten(_git(verzeichnis, "remote", "get-url", remote))


def _ziel_branch(spec: str, verzeichnis: str) -> str | None:
    spec = spec.lstrip("+")
    quelle, doppelpunkt, ziel = spec.partition(":")
    ziel = ziel if doppelpunkt else quelle
    if not ziel or ziel.startswith("refs/tags/"):
        return None
    if ziel == "HEAD" or ziel.startswith("@"):
        ziel = _git(verzeichnis, "rev-parse", "--abbrev-ref", "HEAD") or ""
    ziel = ziel.removeprefix("refs/heads/")
    return ziel if ziel and ziel != "HEAD" else None


def _git_push(woerter: list[str], verzeichnis: str) -> list[dict] | None:
    """Ziele eines ``git … push …`` — None, wenn es kein Push ist."""
    i, git_verzeichnis = 1, verzeichnis
    while i < len(woerter):
        w = woerter[i]
        if w in _GIT_OPTIONEN_MIT_WERT:
            if w == "-C" and i + 1 < len(woerter):
                git_verzeichnis = os.path.join(git_verzeichnis, os.path.expanduser(woerter[i + 1]))
            i += 2
            continue
        if w.startswith("-"):
            i += 1
            continue
        break
    if i >= len(woerter) or woerter[i] != "push":
        return None
    positionen, remote_option, alle = [], None, False
    argumente = woerter[i + 1:]
    j = 0
    while j < len(argumente):
        a = argumente[j]
        if a == "--":
            positionen.extend(argumente[j + 1:])
            break
        if a in ("--all", "--mirror", "--branches"):
            alle = True
        elif a.startswith("--repo="):
            remote_option = a.split("=", 1)[1]
        elif a in _PUSH_OPTIONEN_MIT_WERT:
            if a == "--repo" and j + 1 < len(argumente):
                remote_option = argumente[j + 1]
            j += 1
        elif not a.startswith("-"):
            positionen.append(a)
        j += 1
    remote = positionen[0] if positionen else remote_option
    specs = positionen[1:]
    if specs:
        branches = [b for b in (_ziel_branch(s, git_verzeichnis) for s in specs) if b]
    elif alle:
        liste = _git(git_verzeichnis, "for-each-ref", "--format=%(refname:short)", "refs/heads") or ""
        branches = [b for b in liste.splitlines() if b][:20]
    else:
        aktuell = _git(git_verzeichnis, "rev-parse", "--abbrev-ref", "HEAD")
        branches = [aktuell] if aktuell and aktuell != "HEAD" else []
    adresse = _remote_adresse(git_verzeichnis, remote)
    return [{"branch": b[:250], "remote": adresse} for b in branches]


def push_ziele(befehl: str, verzeichnis: str | None = None, *, tiefe: int = 0) -> list[dict] | None:
    """Alle Push-Ziele eines Shell-Befehls — None, wenn darin kein ``git push`` steht.

    Eine leere Liste heißt: es wird gepusht, aber der Ziel-Branch ist unbekannt
    (die Takt-Regel gilt trotzdem).
    """
    if not befehl or "push" not in befehl or tiefe > 2:
        return None
    try:
        befehle = _woerter(befehl)
    except ValueError:
        # Unlesbar (offenes Anführungszeichen): nichts sperren, nur merken.
        logger.info("[Push-Sperre] Befehl nicht lesbar, nicht geprüft")
        return None
    verzeichnis = verzeichnis or os.getcwd()
    ziele: list[dict] | None = None
    for woerter in befehle:
        woerter = _ohne_umleitungen(woerter)
        # Vorangestellte Zuweisungen und Hüllen (``FOO=1 timeout 30s sudo -E git …``).
        while woerter and (_ZUWEISUNG.match(woerter[0]) or woerter[0] in _HUELLEN or _DAUER.match(woerter[0])
                           or (woerter[0].startswith("-") and len(woerter) > 1)):
            woerter = woerter[1:]
        if not woerter:
            continue
        kopf = os.path.basename(woerter[0])
        if kopf == "cd" and len(woerter) > 1:
            verzeichnis = os.path.join(verzeichnis, os.path.expanduser(woerter[1]))
            continue
        if kopf in _SHELLS and "-c" in woerter:
            stelle = woerter.index("-c")
            if stelle + 1 < len(woerter):
                innen = push_ziele(woerter[stelle + 1], verzeichnis, tiefe=tiefe + 1)
                if innen is not None:
                    ziele = (ziele or []) + innen
            continue
        if kopf == "git":
            gefunden = _git_push(woerter, verzeichnis)
            if gefunden is not None:
                ziele = (ziele or []) + gefunden
    return ziele


#: Werkzeuge, die in einem Lauf aus dem Rückkanal (Frage aus einem Issue) gesperrt sein
#: können — nur für sie wird der Orchestrator gefragt (``projekt_git.RUECKKANAL_GESPERRT``).
LAUF_PRUEFEN = frozenset({
    "projekt_karte_vergeben", "projekt_karte_uebergeben", "projekt_karte_verschieben", "projekt_karte_anlegen",
    "create_task", "create_task_batch", "delegate_and_wait",
    "email_send", "email_reply", "send_email", "reply_email", "ms_send_email",
    "send_teams_message", "send_teams_chat_message",
})


async def werkzeug_pruefen(werkzeug: str, eingabe: dict | None, lauf: str | None) -> str | None:
    """Sperrgrund für ein Werkzeug in einem beschränkten Lauf — None, wenn erlaubt.

    Fail-open wie die Push-Sperre: Ist der Orchestrator nicht erreichbar, wird nicht
    gesperrt (Zusammenstoß- und Missbrauchsbremse, keine Sicherheitsgrenze).
    """
    name = werkzeug.rsplit("__", 1)[-1] if werkzeug.startswith("mcp__") else werkzeug
    if name not in LAUF_PRUEFEN:
        return None
    from app.config import settings

    spalte = (eingabe or {}).get("spalte") if isinstance(eingabe, dict) else None
    try:
        import httpx

        async with httpx.AsyncClient(timeout=5) as client:
            antwort = await client.post(
                f"{settings.orchestrator_url}/api/v1/teams/repo/werkzeug-pruefen",
                headers={"X-Agent-ID": settings.agent_id, "Authorization": f"Bearer {settings.agent_token}"},
                json={"lauf": (lauf or "").strip()[:64] or None, "werkzeug": name,
                      "eingabe": {"spalte": spalte} if isinstance(spalte, str) else {}},
            )
        if antwort.status_code != 200:
            logger.warning("[Lauf-Sperre] Orchestrator antwortet %s, %s erlaubt", antwort.status_code, name)
            return None
        daten = antwort.json()
    except Exception as e:  # noqa: BLE001 — fail-open, siehe oben
        logger.warning("[Lauf-Sperre] Orchestrator nicht erreichbar, %s erlaubt: %s", name, e)
        return None
    if isinstance(daten, dict) and daten.get("erlaubt") is False:
        return str(daten.get("grund") or "In diesem Lauf gesperrt.")[:1500]
    return None


async def pruefen(befehl: str, verzeichnis: str | None = None, lauf: str | None = None) -> str | None:
    """Sperrgrund für einen Shell-Befehl — None, wenn er pushen darf (oder nicht pusht)."""
    from app.config import settings

    try:
        ziele = await asyncio.to_thread(push_ziele, befehl, verzeichnis or settings.workspace_dir)
    except Exception as e:  # noqa: BLE001 — Lesefehler sperren nicht
        logger.warning("[Push-Sperre] Befehl nicht untersucht, Push erlaubt: %s", e)
        return None
    if ziele is None:
        return None
    try:
        import httpx

        async with httpx.AsyncClient(timeout=8) as client:
            antwort = await client.post(
                f"{settings.orchestrator_url}/api/v1/teams/repo/push-pruefen",
                headers={"X-Agent-ID": settings.agent_id, "Authorization": f"Bearer {settings.agent_token}"},
                json={"lauf": (lauf or "").strip()[:64] or None, "ziele": ziele[:20]},
            )
        if antwort.status_code != 200:
            logger.warning("[Push-Sperre] Orchestrator antwortet %s, Push erlaubt", antwort.status_code)
            return None
        daten = antwort.json()
    except Exception as e:  # noqa: BLE001 — fail-open, siehe Moduldoku
        logger.warning("[Push-Sperre] Orchestrator nicht erreichbar, Push erlaubt: %s", e)
        return None
    if isinstance(daten, dict) and daten.get("erlaubt") is False:
        return str(daten.get("grund") or "Push gesperrt.")[:1500]
    return None
