"""Codex CLI runner.

Runs OpenAI Codex through the CLI using a ChatGPT/Codex auth.json mounted by
the orchestrator into /shared/.codex/auth.json.
"""
from __future__ import annotations

import asyncio
import base64
import functools
import json
import time
import logging
import os
import signal
import subprocess
from typing import AsyncIterator

from app import codex_auth_sync
from app.config import settings
from app.ai_credential_status import report_result_status
from app.log_publisher import LogPublisher
from app.proc_watchdog import prozessgruppe_beenden
from app.llm_chat_handler import DESKTOP_MCP_ACTIVE_ENV
from app.runner_hooks import (
    SELF_IMPROVEMENT_SUFFIX,
    compose_prompt_bundle,
)

logger = logging.getLogger(__name__)


def _extract_text(event: dict) -> str:
    """Best-effort text extraction across Codex JSONL event shapes."""
    for key in ("text", "message", "delta", "result", "output_text"):
        value = event.get(key)
        if isinstance(value, str):
            return value

    payload = event.get("payload")
    if isinstance(payload, dict):
        text = _extract_text(payload)
        if text:
            return text

    item = event.get("item")
    if isinstance(item, dict):
        text = _extract_text(item)
        if text:
            return text

    message = event.get("message")
    if isinstance(message, dict):
        text = _extract_text(message)
        if text:
            return text
        content = message.get("content")
        if isinstance(content, list):
            return _extract_content_text(content)

    content = event.get("content")
    if isinstance(content, list):
        return _extract_content_text(content)

    return ""


def _extract_content_text(content: list) -> str:
    parts: list[str] = []
    for block in content:
        if isinstance(block, str):
            parts.append(block)
        elif isinstance(block, dict):
            value = (
                block.get("text")
                or block.get("output_text")
                or block.get("input_text")
            )
            if isinstance(value, str):
                parts.append(value)
    return "".join(parts)


def _event_item(event: dict) -> dict:
    """Return the nested Codex item/payload if present."""
    item = event.get("item")
    if isinstance(item, dict):
        return item
    payload = event.get("payload")
    if isinstance(payload, dict):
        return payload
    return event


def _extract_tool_call(event: dict) -> tuple[str, dict, str] | None:
    item = _event_item(event)
    event_type = str(event.get("type", "")).lower()
    typ = str(item.get("type", event.get("type", ""))).lower()
    status = str(item.get("status", "")).lower()

    if event_type.endswith("completed") or status in {"completed", "failed"}:
        return None

    if typ == "command_execution":
        command = item.get("command") or item.get("cmd") or ""
        return "bash", {"cmd": str(command)}, str(item.get("id") or event.get("id") or "")

    if "tool" not in typ and "function_call" not in typ:
        return None
    name = item.get("name") or item.get("tool") or item.get("call_name")
    if not name:
        return None
    args = item.get("arguments") or item.get("input") or {}
    if isinstance(args, str):
        try:
            args = json.loads(args)
        except json.JSONDecodeError:
            args = {"raw": args}
    return (
        str(name),
        args if isinstance(args, dict) else {"raw": args},
        str(item.get("id") or event.get("id") or ""),
    )


def _extract_tool_result(event: dict) -> tuple[str, str] | None:
    item = _event_item(event)
    event_type = str(event.get("type", "")).lower()
    typ = str(item.get("type", "")).lower()
    status = str(item.get("status", "")).lower()

    if not (event_type.endswith("completed") or status in {"completed", "failed"}):
        return None

    if typ == "command_execution":
        output = item.get("aggregated_output")
        if output is None:
            output = item.get("output") or ""
        exit_code = item.get("exit_code")
        if exit_code is not None:
            output = f"{output}\n(exit code: {exit_code})" if output else f"(exit code: {exit_code})"
        return str(item.get("id") or event.get("id") or ""), str(output)

    if "tool" in typ or "function_call" in typ:
        result = item.get("result") or item.get("output") or item.get("content") or ""
        if not isinstance(result, str):
            result = json.dumps(result, ensure_ascii=False)
        return str(item.get("id") or event.get("id") or ""), result

    return None


def _codex_auth_problem() -> str | None:
    """Diagnostic message if auth.json is missing/unreadable/malformed/expired.

    ``None`` means it's fine to proceed. Checked BEFORE spawning the CLI
    (#710): on 06.09.2026, 16/16 runs failed with a bare "Codex CLI exited
    with code 1" and zero stderr output — the actual cause (auth.json) never
    reaches the operator, because the CLI can exit before printing anything
    useful when the credential itself is the problem. Same JWT-expiry logic
    as ``orchestrator/app/services/codex_auth_service.py::_access_token_exp``
    (duplicated deliberately: agent and orchestrator are separate containers).
    """
    path = codex_auth_sync.auth_path()
    if not os.path.exists(path):
        return f"Codex-Anmeldedaten fehlen ({path} nicht vorhanden) — bitte neu anmelden."
    try:
        with open(path) as f:
            parsed = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        return f"Codex-Anmeldedaten nicht lesbar ({path}): {e}"

    tok = (parsed.get("tokens") or {}).get("access_token", "")
    if not isinstance(tok, str) or tok.count(".") != 2:
        return "Codex-Anmeldedaten unvollstaendig (kein gueltiger Access-Token in auth.json) — bitte neu anmelden."
    try:
        payload = tok.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        exp = json.loads(base64.urlsafe_b64decode(payload)).get("exp")
    except Exception:  # noqa: BLE001 — malformed token -> treat expiry as unknown
        exp = None
    if isinstance(exp, (int, float)) and exp < time.time():
        seit = int(time.time() - exp)
        return f"Codex-Anmeldedaten seit {seit}s abgelaufen — bitte neu anmelden (Device-Auth)."
    return None


class CodexAgentRunner:
    """Executes tasks through `codex exec --json`."""

    def __init__(self, log_publisher: LogPublisher):
        self.log_publisher = log_publisher
        self._process: asyncio.subprocess.Process | None = None
        self._runner: CodexAgentRunner | None = None
        self.is_running = False
        # Token-Stand des Fadens nach dem letzten Lauf (#896). Codex meldet in
        # ``turn.completed`` die SUMME des Fadens — nach ``exec resume``
        # inklusive aller frueheren Zuege (am 04.10.2026 nachgemessen: 13130,
        # dann 27506 Eingabe-Token; der zweite Zug selbst hatte 14376).
        self._nutzung_stand: dict[str, int] = {}

    async def execute_task(
        self, task_id: str, prompt: str, model: str | None = None,
        lightweight: bool = False,
    ) -> dict:
        model = model or settings.default_model
        self.is_running = True

        task_id_line = f"CURRENT_TASK_ID: {task_id}\n\n"
        from app.runner_hooks import zeitkontext
        # Unified context bundle (shared via runner_hooks) — same blocks as the
        # Claude and custom_llm runtimes, incl. host mounts / Second Brain awareness.
        enhanced_prompt = (
            task_id_line
            + compose_prompt_bundle(prompt, lightweight)
            # Datum/Wochentag/Uhrzeit (#905) — sonst raet das Modell das Jahr.
            + zeitkontext() + "\n\n"
            + prompt
            + SELF_IMPROVEMENT_SUFFIX
        )

        await self.log_publisher.publish(
            task_id, "system", {"message": f"Starting Codex task with model {model}"}
        )
        result = await self._run_codex(task_id, enhanced_prompt, model, stream="task")
        await report_result_status(result)
        self.is_running = False
        self._process = None
        return result

    async def _run_codex(self, target_id: str, prompt: str, model: str, stream: str, resume: bool = False,
                         resume_id: str | None = None) -> dict:
        """``resume_id``: diesen Codex-Faden fortsetzen (Chat ueber Nachrichten
        hinweg); ``resume``: den juengsten (Steuerung mitten im Zug)."""
        self._interrupted = False  # set by interrupt() when a steering message cuts this turn short
        self.thread_id = None
        if not resume and not resume_id:
            # Neuer Faden: die Summe der CLI beginnt bei null.
            self._nutzung_stand = {}

        auth_problem = _codex_auth_problem()
        if auth_problem:
            await _publish(self.log_publisher, stream, target_id, "error", {"message": auth_problem})
            return {"status": "error", "error": auth_problem}

        # Prompt via STDIN ("-") not argv → avoids E2BIG ("Argument list too long")
        # on large prompts (PR diffs etc.), same reason the claude path pipes stdin.
        # resume=True → `codex exec resume --last` continues the just-run session so a
        # folded (steering) message keeps the conversation instead of starting over.
        # NOTE: `codex exec resume` does NOT accept `-C` (working dir); it filters
        # sessions by cwd by default and we already start the subprocess with
        # cwd=workspace_dir, so the right session is found without it.
        common = [
            "--json",
            "--skip-git-repo-check",
            "--dangerously-bypass-approvals-and-sandbox",
            "-m", model,
        ]
        # Der PreToolUse-Hook (config.toml, CODEX_HOOK_SKRIPT) stammt aus dem
        # Abbild, nicht vom Nutzer — ohne diesen Schalter liefe er erst nach
        # einer interaktiven Vertrauensbestaetigung, also nie (#781). Eine
        # aeltere Codex-Version ohne den Schalter bricht sonst mit
        # "unexpected argument" ab; dann bleibt es beim Gate in den MCP-Servern.
        if await asyncio.to_thread(_codex_kennt_hook_trust):
            common.append("--dangerously-bypass-hook-trust")
        # Per-message reasoning level chosen by the user in the chat. Passed as a
        # CLI override (not the config TOML) so it applies to THIS turn only.
        # Stufe am Lauf gewinnt; sonst die Standard-Denktiefe des Agenten —
        # _run_codex bedient Chat UND Aufgaben, damit gilt sie fuer beide.
        _reasoning = getattr(self, "_reasoning", "") or settings.default_reasoning
        if _reasoning:
            # Codex benennt die Extreme anders (kein Denken = "minimal") und
            # kennt oberhalb von "xhigh" nichts. Die Zuordnung liegt zentral in
            # config.reasoning_fuer.
            from app.config import reasoning_fuer
            effort = reasoning_fuer("codex", _reasoning)
            common += ["-c", f'model_reasoning_effort="{effort}"']
        if resume_id:
            cmd = ["codex", "exec", "resume"] + common + [resume_id, "-"]
        elif resume:
            cmd = ["codex", "exec", "resume"] + common + ["--last", "-"]
        else:
            cmd = ["codex", "exec"] + common + ["-C", settings.workspace_dir, "-"]
        env = _codex_env()

        stderr_lines: list[str] = []
        text_output: list[str] = []
        result_data: dict = {"status": "completed", "result": ""}
        completed_seen = False
        # Werkzeugaufrufe mit Eingabe und Ausgabe fuer den gespeicherten Verlauf —
        # dieselbe Form wie Claude Code und Custom-LLM (#911).
        from app.werkzeug_eintrag import WerkzeugListe
        werkzeuge = WerkzeugListe()

        async def collect_stderr(proc: asyncio.subprocess.Process) -> None:
            if not proc.stderr:
                return
            while True:
                line = await proc.stderr.readline()
                if line:
                    self.log_publisher.last_activity_at = time.monotonic()
                if not line:
                    break
                decoded = line.decode("utf-8", errors="replace").strip()
                if decoded:
                    stderr_lines.append(decoded)
                    logger.warning("[Codex stderr] %s", decoded)

        try:
            self._process = await asyncio.create_subprocess_exec(
                *cmd,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=settings.workspace_dir,
                env=env,
                # Eigene Prozessgruppe: bei einem Abbruch gehen alle Kinder mit (#926).
                start_new_session=True,
            )
            # Feed the prompt via stdin, then close so codex starts processing.
            if self._process.stdin is not None:
                self._process.stdin.write(prompt.encode("utf-8"))
                await self._process.stdin.drain()
                self._process.stdin.close()
            stderr_task = asyncio.create_task(collect_stderr(self._process))

            async for event in _stream_jsonl(self._process, self.log_publisher):
                text = _extract_text(event)
                if text:
                    text_output.append(text)
                    # Jedes Textstueck von Codex ist eine FERTIGE Aeusserung,
                    # kein Teilstueck — deshalb immer ein eigener Block. Sonst
                    # verschmelzen mehrere Zwischenmeldungen in der Oberflaeche
                    # zu einem Fliesstext ohne Luecke.
                    await _publish(self.log_publisher, stream, target_id, "text",
                                   {"text": text, "neuer_block": True})

                tool_call = _extract_tool_call(event)
                if tool_call:
                    name, args, tool_id = tool_call
                    werkzeuge.aufruf(name, args, tool_id)
                    await _publish(
                        self.log_publisher,
                        stream,
                        target_id,
                        "tool_call",
                        {"tool_use_id": tool_id, "tool": name, "input": args},
                    )

                tool_result = _extract_tool_result(event)
                if tool_result:
                    tool_id, output = tool_result
                    werkzeuge.ergebnis(tool_id, output)
                    await _publish(
                        self.log_publisher,
                        stream,
                        target_id,
                        "tool_result",
                        {"tool_use_id": tool_id, "content": output},
                    )

                # Kennung des Fadens: damit setzt die naechste Chat-Nachricht genau
                # dieses Gespraech fort (``codex exec resume <id>``).
                if event.get("type") == "thread.started" and event.get("thread_id"):
                    self.thread_id = event["thread_id"]
                if str(event.get("type", "")).endswith("completed"):
                    completed_seen = True
                if event.get("type") == "turn.completed":
                    # Nur hier steht die Nutzung — ``item.completed`` hat keine
                    # und hat die Zahlen bisher wieder auf None gesetzt.
                    usage = event.get("usage", {}) if isinstance(event.get("usage"), dict) else {}
                    result_data.update(self._nutzung_dieses_laufs(usage, model))

            returncode = await self._process.wait()
            await stderr_task
            # Jedes Stueck ist eine eigene Aeusserung (siehe oben) — als Absatz
            # trennen, sonst klebt der gespeicherte Verlauf zusammen.
            final_text = "\n\n".join(t.strip("\n") for t in text_output)
            # Chat: der ganze Verlauf. Aufgabe: nur die SCHLUSSantwort — wie bei
            # Claude Code, dessen ``result`` ebenfalls nur die letzte Aeusserung
            # ist. Sonst stand in der Ergebnis-Kachel das Vorgeplaenkel
            # („Ich recherchiere …") vor dem eigentlichen Ergebnis (28.09.2026).
            letzte = text_output[-1].strip("\n") if text_output else ""
            result_data["result"] = letzte if stream == "task" else final_text
            result_data["text"] = final_text
            result_data["tool_calls"] = werkzeuge.liste()

            if returncode in (-2, -15, 130) or getattr(self, "_interrupted", False):
                # Interrupt for live steering (a new message arrived and we cut the turn
                # short to fold it) — not a failure. Codex may exit 1 on SIGINT, so we
                # also honor the explicit _interrupted flag set by interrupt().
                logger.info("Codex CLI interrupted (rc=%s) — folding new message(s)", returncode)
            elif returncode != 0:
                stderr_text = "\n".join(stderr_lines).strip()
                # The Codex CLI runs with stdin=DEVNULL: after finishing its turn it
                # tries to read more input, hits EOF and prints "Reading additional
                # input from stdin..." then exits non-zero. That is NOT a task
                # failure. Treat the run as successful when a completion event was
                # seen (or the only issue is that benign stdin-EOF after real
                # output); report an error only for a run that genuinely produced
                # neither a completion nor any output.
                benign_stdin = "reading additional input from stdin" in stderr_text.lower()
                if not (completed_seen or (benign_stdin and final_text.strip())):
                    # Exit-Code als stabiles Praefix statt versteckt im
                    # Freitext, damit sich danach filtern laesst (#710). Nie
                    # ein leeres "no output on stderr" ausliefern, ohne den
                    # Grund dafuer zu nennen — genau das war am 06.09.2026
                    # 16/16 Mal der Fall (Codex schrieb bei abgelaufenen
                    # Anmeldedaten nichts auf stderr). result/text bleiben
                    # erhalten statt hier verworfen zu werden: ein Lauf, der
                    # etwas ausgegeben hat, bevor er scheiterte, soll das
                    # Teilergebnis nicht verlieren.
                    grund = stderr_text or "keine Ausgabe auf stderr"
                    error = f"[codex_exit={returncode}] {grund}"
                    result_data["status"] = "error"
                    result_data["error"] = error
                    await _publish(self.log_publisher, stream, target_id, "error", {"message": error})
        except asyncio.CancelledError:
            await self.interrupt()
            await prozessgruppe_beenden(self._process)
            result_data = {"status": "cancelled"}
        except Exception as e:
            # Erst den Lauf wirklich beenden, dann „gescheitert“ melden: sonst
            # arbeitet er weiter, waehrend der Neuversuch schon startet (#926).
            await prozessgruppe_beenden(self._process)
            result_data = {"status": "error", "error": str(e)}
            await _publish(self.log_publisher, stream, target_id, "error", {"message": str(e)})

        # Hat die CLI waehrend des Laufs den Refresh-Token erneuert, lebt der neue
        # nur im Container. Auch nach einem Fehlerlauf melden — gerade der
        # abgelaufene Token loest die Erneuerung aus.
        try:
            await codex_auth_sync.push_if_rotated()
        except Exception:  # noqa: BLE001 — ein Lauf darf daran nie scheitern
            logger.debug("Codex-Erneuerung konnte nicht gesichert werden", exc_info=True)

        return result_data

    #: ``turn.completed.usage`` → Feld im Ergebnis (dieselben Namen wie Claude
    #: Code und Custom-LLM, damit Chat-Anzeige und Abrechnung sie lesen).
    _NUTZUNGSFELDER = {
        "input_tokens": "input_tokens",
        "output_tokens": "output_tokens",
        # Feinaufschlüsselung, sofern Codex sie meldet (GPT-Modelle):
        # gecachte Eingabe + „nachgedachte" Ausgabe.
        "cached_input_tokens": "cached_tokens",
        "reasoning_output_tokens": "reasoning_tokens",
    }

    def _nutzung_dieses_laufs(self, usage: dict, model: str) -> dict:
        """Token und Kosten DIESES Laufs aus der Fadensumme von Codex.

        Codex meldet keine Kosten (#896: im Chat stand ``NULL``). Sie entstehen
        hier aus Token × Preis — ueber ``model_registry``, dieselbe Tabelle wie
        bei Custom-LLM. Die Token sind die fortlaufende Summe des Fadens; der
        Anteil dieses Laufs ist die Differenz zum Stand nach dem vorigen.
        """
        from app import model_registry

        aus: dict = {"cache_write_tokens": 0}
        neuer_stand: dict[str, int] = {}
        for quelle, ziel in self._NUTZUNGSFELDER.items():
            gesamt = int(usage.get(quelle) or 0)
            neuer_stand[quelle] = gesamt
            aus[ziel] = int(model_registry.anteil_seit(
                gesamt, self._nutzung_stand.get(quelle, 0)) or 0)
        self._nutzung_stand = neuer_stand
        # Gecachte Eingabe steckt in input_tokens (OpenAI-Zaehlweise) und wird
        # hier zum vollen Preis gerechnet — lieber zu hoch als ein Budget, das
        # unbemerkt reisst.
        aus["cost_usd"] = model_registry.estimate_cost(
            model, aus["input_tokens"], aus["output_tokens"])
        return aus

    async def interrupt(self) -> None:
        self._interrupted = True
        if self._process and self._process.returncode is None:
            try:
                self._process.send_signal(signal.SIGINT)
                await asyncio.wait_for(self._process.wait(), timeout=10)
            except (asyncio.TimeoutError, ProcessLookupError):
                self._process.kill()


class CodexChatHandler:
    """Chat ueber Codex CLI — EIN Faden je Kanal, ueber Nachrichten hinweg.

    Bis v1.365 begann jede Chat-Nachricht ein neues ``codex exec``: der Agent
    kannte nicht einmal die vorige Nachricht desselben Gespraechs. Jetzt merkt
    sich der Handler die Kennung des Codex-Fadens (``session_id``, der Consumer
    speichert sie wie bei Claude Code in Redis) und setzt ihn mit
    ``codex exec resume <id>`` fort. Geht das nicht (Faden weg, Verlauf zu lang),
    beginnt ein neuer Faden mit Uebertrag (app/chat_uebertrag).
    """

    def __init__(self, log_publisher: LogPublisher):
        self.log_publisher = log_publisher
        self._process: asyncio.subprocess.Process | None = None
        self._runner: "CodexAgentRunner | None" = None
        self.is_running = False
        # Live steering: set by the ChatConsumer (see steering.py).
        self.pending_drain = None
        # Kennung des Codex-Fadens dieses Kanals und sein fortlaufender
        # Nutzungsstand (Codex zaehlt je Faden weiter — Kosten = Differenz).
        self.session_id: str | None = None
        self._nutzung_stand: dict = {}

    async def handle_message(self, message_id: str, text: str, model: str | None = None,
                             reasoning: str = "") -> dict:
        """Run a Codex chat turn with live steering.

        A message that arrives on this channel mid-turn interrupts the run (SIGINT,
        handled gracefully) and is folded into a follow-up turn that resumes the same
        thread. See steering.py.
        """
        from app.chat_uebertrag import mit_uebertrag
        from app.steering import run_turns_with_steering
        # Held for this turn (incl. steering follow-ups); read when the CLI args
        # are assembled.
        self._reasoning = reasoning or ""
        self.is_running = True
        model = model or settings.default_model
        # ONE runner for the whole (possibly multi-fold) exchange so interrupt() hits
        # the live process.
        self._runner = CodexAgentRunner(self.log_publisher)
        if self.session_id:
            self._runner._nutzung_stand = dict(self._nutzung_stand or {})

        async def _frisch(t: str, grund: str) -> dict:
            """Neuer Faden mit Uebertrag — der Faden ist weg oder zu lang."""
            self.session_id = None
            await self.log_publisher.publish_chat(message_id, "system", {"message": grund})
            fresh = getattr(self, "fresh_session_text", None) or t
            return await self._runner._run_codex(
                message_id, await mit_uebertrag(self, fresh), model, stream="chat")

        async def _run_turn(t: str, is_resume: bool) -> dict:
            from app.chat_handler import _is_context_length_error

            faden = self.session_id or (self._runner.thread_id if is_resume else None)
            res = await self._runner._run_codex(message_id, t, model, stream="chat", resume_id=faden)
            # Anmeldung abgelaufen/fehlt: ein neuer Faden scheitert genauso —
            # durchreichen statt „Sitzung nicht fortsetzbar“ zu melden.
            if faden and res.get("status") == "error" and not _codex_auth_problem():
                if _is_context_length_error(res.get("error", "")):
                    res = await _frisch(t, "Der Gesprächsverlauf war zu lang geworden — ich setze "
                                           "ihn in einem neuen Faden fort und nehme die letzten "
                                           "Nachrichten und den Stand des Gesprächs mit.")
                else:
                    logger.warning("Codex resume %s failed, retrying fresh: %s", faden,
                                   str(res.get("error"))[:120])
                    res = await _frisch(t, "Die vorige Sitzung war nicht mehr fortsetzbar — ich "
                                           "setze das Gespräch in einer neuen fort und nehme die "
                                           "letzten Nachrichten mit.")
            if self._runner.thread_id:
                self.session_id = self._runner.thread_id
                self._nutzung_stand = dict(getattr(self._runner, "_nutzung_stand", {}) or {})
            # Frischer Faden und trotzdem zu gross: die EINZELNE Nachricht sprengt
            # das Fenster — wiederholen ist zwecklos, erklaeren hilft (#623).
            if res.get("status") == "error" and _is_context_length_error(res.get("error", "")):
                res = {
                    "status": "error",
                    "error": ("Diese einzelne Nachricht ist zu gross fuer das "
                              "Kontextfenster des Modells. Bitte kuerzer fassen — "
                              "oder grosse Inhalte als Datei in den Workspace legen "
                              "und in der Nachricht darauf verweisen."),
                }
            return res

        try:
            result = await run_turns_with_steering(
                initial_text=text,
                run_turn=_run_turn,
                stop_current=self.stop_current,
                pending_drain=self.pending_drain,
                publish_system=lambda m: self.log_publisher.publish_chat(
                    message_id, "system", {"message": m}
                ),
            )
        finally:
            self.is_running = False
            self._runner = None
            self._process = None

        await report_result_status(result)
        await self.log_publisher.publish_chat(message_id, "done", result)
        return result

    async def stop_current(self) -> None:
        if self._runner:
            await self._runner.interrupt()

    async def reset_session(self) -> None:
        self.session_id = None
        self._nutzung_stand = {}
        await self.log_publisher.publish_chat(
            "", "system", {"message": "Codex chat session reset"}
        )


def _codex_env() -> dict[str, str]:
    env = os.environ.copy()
    codex_home = env.setdefault("CODEX_HOME", "/home/agent/.codex")
    desktop_mcp_active = _ensure_codex_mcp_config(codex_home, env)
    env[DESKTOP_MCP_ACTIVE_ENV] = "1" if desktop_mcp_active else "0"
    os.environ[DESKTOP_MCP_ACTIVE_ENV] = env[DESKTOP_MCP_ACTIVE_ENV]
    return env


_SAFE_MCP_NAME_RE = __import__("re").compile(r"^[A-Za-z0-9_]+$")


def _toml_escape(value: str) -> str:
    """Escape a value for embedding in a TOML double-quoted string."""
    return value.replace("\\", "\\\\").replace('"', '\\"')


def _valid_http_url(url: str) -> bool:
    import urllib.parse
    try:
        p = urllib.parse.urlparse(url)
        return p.scheme in ("http", "https") and bool(p.netloc) and "\n" not in url
    except Exception:
        return False


@functools.lru_cache(maxsize=1)
def _codex_kennt_hook_trust() -> bool:
    """Kennt das installierte Codex ``--dangerously-bypass-hook-trust``? Einmal je Prozess geprueft."""
    try:
        hilfe = subprocess.run(
            ["codex", "exec", "--help"], capture_output=True, text=True, timeout=20,
        ).stdout
    except Exception:  # noqa: BLE001
        return False
    if "--dangerously-bypass-hook-trust" in hilfe:
        return True
    logger.warning("Codex kennt --dangerously-bypass-hook-trust nicht — PreToolUse-Hook bleibt aus (#781)")
    return False


#: Weiterleitung von Codex' PreToolUse-Hook zum lokalen Entscheidungspunkt.
CODEX_HOOK_SKRIPT = "/opt/mcp/codex-hook.mjs"


def _codex_hook_abschnitt() -> list[str]:
    """PreToolUse-Hook fuer JEDEN Werkzeugaufruf (Issue #781 PR 3).

    Codex schickt Shell (``Bash``), Dateiaenderungen (``apply_patch``) und
    MCP-Werkzeuge durch diesen Hook; das Skript fragt denselben lokalen
    Entscheidungspunkt wie Claude Codes HTTP-Hook. Damit gilt die
    Autonomie-Matrix fuer Codex genauso fein wie fuer Claude Code — vorher
    lief Codex ganz ohne Durchsetzung. Tabellen (``[[...]]``) stehen am Ende
    der Datei, nach allen ``[mcp_servers.*]``-Abschnitten.
    """
    return [
        "",
        "# Autonomie-Gate fuer jeden Werkzeugaufruf (#781)",
        "[[hooks.PreToolUse]]",
        'matcher = ".*"',
        "",
        "[[hooks.PreToolUse.hooks]]",
        'type = "command"',
        f'command = "node {CODEX_HOOK_SKRIPT}"',
        "timeout = 15",
        "",
    ]


def _ensure_codex_mcp_config(codex_home: str, env: dict) -> bool:
    """Write ~/.codex/config.toml with built-in + custom MCP servers.

    Called once per Codex invocation. Idempotent — overwrites the server
    sections so new agents or updated CUSTOM_MCP_SERVERS are always current.
    """
    import pathlib

    # Restrict directory to owner-only — file will contain AGENT_TOKEN
    os.makedirs(codex_home, mode=0o700, exist_ok=True)
    config_path = pathlib.Path(codex_home) / "config.toml"

    orch_url = env.get("ORCHESTRATOR_URL", "http://ai-employee-orchestrator:8000")
    if not _valid_http_url(orch_url):
        orch_url = "http://ai-employee-orchestrator:8000"
    agent_token = env.get("AGENT_TOKEN", "")
    agent_id = env.get("AGENT_ID", "")
    agent_name = env.get("AGENT_NAME", "") or agent_id
    default_model = env.get("DEFAULT_MODEL", "")

    # Built-in AI Employee MCP servers (stdio)
    builtin_servers = {
        "brain": "/opt/mcp/brain-server.mjs",
        "skill": "/opt/mcp/skill-server.mjs",
        "memory": "/opt/mcp/memory-server.mjs",
        "notification": "/opt/mcp/notification-server.mjs",
        "orchestrator": "/opt/mcp/orchestrator-server.mjs",
        "desktop": "/opt/mcp/computer-use-server.mjs",
        "read_logs": "/opt/mcp/read-logs-server.mjs",
        # Fehlten bis 2026-08-12: ein Codex-Agent hatte KEIN Microsoft 365, keine
        # Mail und kein Video — Claude Code schon. Zwei Listen, die niemand
        # gegeneinander geprueft hat. Der Paritaetstest tut das jetzt.
        "msgraph": "/opt/mcp/msgraph-server.mjs",
        "email": "/opt/mcp/email-server.mjs",
        "hyperframes": "/opt/mcp/hyperframes-server.mjs",
    }

    lines: list[str] = [
        '[projects."/workspace"]',
        'trust_level = "trusted"',
        "",
    ]

    desktop_mcp_active = False
    # Welche Abschnitte bereits in der Datei stehen. Codex bricht beim ERSTEN
    # doppelten Schluessel ab und laedt dann GAR KEINE Konfiguration — der Agent
    # steht ohne jedes Werkzeug da und kann nur noch reden.
    geschrieben: set[str] = set()

    # Stdio built-in servers
    for name, script in builtin_servers.items():
        if not os.path.exists(script):
            continue
        if name == "desktop":
            desktop_mcp_active = True
        # Codex only exposes the env vars declared in this [env] block to the
        # MCP server — it does NOT inherit the agent container's environment.
        # The built-in servers authenticate to the orchestrator with the agent
        # HMAC token, which is keyed on AGENT_ID; if AGENT_ID is missing the
        # .mjs servers fall back to "unknown" and every call is rejected (401).
        server_env = [
            f"[mcp_servers.{name}.env]",
            f'ORCHESTRATOR_URL = "{_toml_escape(orch_url)}"',
            f'AGENT_ID = "{_toml_escape(agent_id)}"',
            f'AGENT_TOKEN = "{_toml_escape(agent_token)}"',
        ]
        if name == "orchestrator":
            server_env += [
                f'AGENT_NAME = "{_toml_escape(agent_name)}"',
                f'DEFAULT_MODEL = "{_toml_escape(default_model)}"',
            ]
        geschrieben.add(name)
        lines += [
            f"[mcp_servers.{name}]",
            'command = "node"',
            f'args = ["{script}"]',
            "",
            *server_env,
            "",
        ]

    # Custom HTTP MCP servers from CUSTOM_MCP_SERVERS env var
    custom_raw = env.get("CUSTOM_MCP_SERVERS", "")
    auth_raw = env.get("CUSTOM_MCP_AUTH", "")
    if custom_raw:
        try:
            custom_servers: dict = json.loads(custom_raw)
            auth_map: dict = json.loads(auth_raw) if auth_raw else {}
            for srv_name, srv_url in custom_servers.items():
                safe_name = srv_name.replace("-", "_").replace(" ", "_")
                if not _SAFE_MCP_NAME_RE.match(safe_name):
                    logger.warning("Skipping MCP server with unsafe name: %r", srv_name)
                    continue
                if not _valid_http_url(srv_url):
                    logger.warning("Skipping MCP server %r with invalid URL: %r", srv_name, srv_url)
                    continue
                if safe_name in geschrieben:
                    # Genau hier ging es am 2026-08-16 kaputt: ``msgraph`` ist
                    # seit dem 12.08. ein EINGEBAUTER Server und wird zusaetzlich
                    # eingeschleust, sobald ein Agent die Microsoft-Integration
                    # hat. Zwei Abschnitte gleichen Namens — und Codex verwarf
                    # die komplette Datei. Der Agent hatte danach kein einziges
                    # Werkzeug und antwortete auf jede Bitte nur mit „ich
                    # kuemmere mich darum", weil er buchstaeblich nichts tun
                    # konnte.
                    #
                    # Der eingebaute gewinnt: er ist in jedem Agenten vorhanden
                    # und in beiden Laufzeiten derselbe (Paritaet). Ein
                    # uebersprungener Doppelgaenger kostet nichts — eine
                    # unlesbare Konfiguration kostet alles.
                    logger.warning(
                        "MCP-Server %r ist bereits eingebaut — der eingeschleuste "
                        "wird uebersprungen (doppelter Abschnitt wuerde die ganze "
                        "config.toml unbrauchbar machen)", srv_name,
                    )
                    continue
                geschrieben.add(safe_name)
                lines += [
                    f"[mcp_servers.{safe_name}]",
                    f'url = "{_toml_escape(srv_url)}"',
                ]
                if srv_name in auth_map:
                    token_env_var = f"MCP_TOKEN_{safe_name}"
                    env[token_env_var] = auth_map[srv_name]
                    lines.append(f'bearer_token_env_var = "{token_env_var}"')
                lines.append("")
        except Exception as e:
            logger.warning("Failed to parse CUSTOM_MCP_SERVERS for Codex config: %s", e)

    lines.extend(_codex_hook_abschnitt())

    # Write with owner-only permissions (0o600) — contains AGENT_TOKEN
    data = "\n".join(lines).encode()
    fd = os.open(str(config_path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.write(fd, data)
    finally:
        os.close(fd)
    logger.debug("Written Codex MCP config to %s (%d built-in servers)", config_path, len(builtin_servers))
    return desktop_mcp_active


async def _publish(
    publisher: LogPublisher, stream: str, target_id: str, event_type: str, payload: dict
) -> None:
    if stream == "chat":
        await publisher.publish_chat(target_id, event_type, payload)
    else:
        await publisher.publish(target_id, event_type, payload)


async def _stream_jsonl(process: asyncio.subprocess.Process, log_publisher: LogPublisher) -> AsyncIterator[dict]:
    if not process.stdout:
        return
    buffer = b""
    while True:
        chunk = await process.stdout.read(4096)
        if chunk:
            # Lebenszeichen fuer den Stillstands-Wachhund (siehe chat_consumer).
            log_publisher.last_activity_at = time.monotonic()
        if not chunk:
            if buffer.strip():
                for line in buffer.decode("utf-8", errors="replace").splitlines():
                    parsed = _parse_line(line)
                    if parsed:
                        yield parsed
            break
        buffer += chunk
        while b"\n" in buffer:
            line_bytes, buffer = buffer.split(b"\n", 1)
            parsed = _parse_line(line_bytes.decode("utf-8", errors="replace"))
            if parsed:
                yield parsed


def _parse_line(line: str) -> dict | None:
    line = line.strip()
    if not line:
        return None
    try:
        return json.loads(line)
    except json.JSONDecodeError:
        return {"type": "raw", "text": line}
