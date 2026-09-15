#!/usr/bin/env python3
"""Gegenprobe fuer Batch 7 (#726): je Verhaltensaenderung EINE Mutation.

Batch 7 = Aufgaben-Abbruch (Router, Sprachfront, Agenten-Zuhoerer, UI-Knopf)
und Herzschlag/Waechter (Agenten-Schleife, Orchestrator-Handler, Stale-Tick).

Jede Mutation im PRODUKTIVCODE muss den ihr zugeordneten Test rot faerben; die
Kontrolle mit unveraendertem Code muss gruen sein. Dateien werden per
Hash-Vergleich wiederhergestellt (finally), damit keine Mutation liegen bleibt.
Je Zusicherung ist mindestens eine "auskommentiert"-Mutation dabei — das ist
die Klasse, die #726 ueberhaupt begruendet.
"""
import hashlib
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PY = {"orchestrator": "/workspace/.venv-orch/bin/python", "agent": "/workspace/.venv-orch/bin/python"}

O = "orchestrator/tests/"
A = "tests/"
ROUTER = "orchestrator/app/core/task_router.py"
VOICE = "orchestrator/app/services/realtime_voice_session.py"
SCHED = "orchestrator/app/services/scheduler_service.py"
WATCH = "orchestrator/app/services/watchdog.py"
CONSUMER = "agent/app/task_consumer.py"
SEITE = "frontend/src/app/tasks/page.tsx"
MAIN = "orchestrator/app/main.py"

CANCEL = O + "test_task_cancel_really_stops.py::"
HEART = O + "test_task_heartbeat_watchdog.py::"
LISTENER = A + "test_task_cancel_listener.py::"
LOOP = A + "test_task_heartbeat_loop.py::"

MUTATIONEN = [
    # (label, datei, alt, neu, suite, test)
    # --- Router
    ("router-lehnt-running-ab", ROUTER,
     "if task.status in (TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED):",
     "if task.status in (TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED, TaskStatus.RUNNING):",
     "orchestrator", CANCEL + "ARunningTaskCanBeStoppedTests::test_the_router_no_longer_refuses_running_tasks"),
    ("router-kanal-falsch", ROUTER,
     'f"agent:{task.agent_id}:task:cancel", task_id', 'f"agent:{task.agent_id}:task:stop", task_id',
     "orchestrator", CANCEL + "ARunningTaskCanBeStoppedTests::test_it_signals_the_agent_for_a_running_task"),
    ("router-json-nutzlast", ROUTER,
     'f"agent:{task.agent_id}:task:cancel", task_id', 'f"agent:{task.agent_id}:task:cancel", json.dumps({"task_id": task_id})',
     "orchestrator", CANCEL + "ARunningTaskCanBeStoppedTests::test_it_signals_the_agent_for_a_running_task"),
    ("auskommentiert-router-publish", ROUTER, None, None,
     "orchestrator", CANCEL + "ARunningTaskCanBeStoppedTests::test_it_signals_the_agent_for_a_running_task"),
    ("router-queue-nicht-entfernt", ROUTER,
     "            await self._remove_from_queue(task.agent_id, task_id)\n        task.status = TaskStatus.CANCELLED",
     "            pass  # await self._remove_from_queue(task.agent_id, task_id)\n        task.status = TaskStatus.CANCELLED",
     "orchestrator", CANCEL + "ARunningTaskCanBeStoppedTests::test_a_waiting_task_is_removed_without_a_signal"),
    ("router-status-bleibt", ROUTER,
     "        task.status = TaskStatus.CANCELLED\n        task.completed_at = datetime.now(timezone.utc)\n        await self.db.commit()\n        await self.db.refresh(task)\n        return task",
     "        task.completed_at = datetime.now(timezone.utc)\n        await self.db.commit()\n        await self.db.refresh(task)\n        return task",
     "orchestrator", CANCEL + "ARunningTaskCanBeStoppedTests::test_the_router_no_longer_refuses_running_tasks"),
    # --- Sprachfront
    ("voice-luegt-wieder", VOICE,
     "        if not uebrig:\n            return (f\"Ich habe {geschafft} Aufgabe(n) gestoppt. Es läuft nichts mehr.\"",
     "        if True:\n            return (f\"Ich habe {geschafft} Aufgabe(n) gestoppt. Es läuft nichts mehr.\"",
     "orchestrator", CANCEL + "TheVoiceTellsTheTruthTests::test_it_no_longer_reports_success_from_a_bare_publish"),
    ("voice-nur-laufende", VOICE,
     "Task.agent_id == self.agent_id, Task.status.in_(OFFEN)",
     "Task.agent_id == self.agent_id, Task.status == TaskStatus.RUNNING",
     "orchestrator", CANCEL + "TheVoiceTellsTheTruthTests::test_it_looks_at_all_open_tasks_not_only_this_session"),
    ("voice-nicht-nachsehen", VOICE,
     "        uebrig = await _offene()\n", "        uebrig = []\n",
     "orchestrator", CANCEL + "TheVoiceTellsTheTruthTests::test_it_checks_again_afterwards"),
    ("voice-nicht-nachsehen-sagt-es-nicht", VOICE,
     "        uebrig = await _offene()\n", "        uebrig = []\n",
     "orchestrator", CANCEL + "TheVoiceTellsTheTruthTests::test_it_says_so_when_something_survived"),
    ("voice-ohne-namen", VOICE,
     'f"{namen}. Die reagiert gerade nicht', 'f"Die reagiert gerade nicht',
     "orchestrator", CANCEL + "TheVoiceTellsTheTruthTests::test_it_names_what_is_still_running"),
    ("auskommentiert-voice-cancel", VOICE,
     "                    await TaskRouter(db, self.redis, LoadBalancer(self.redis)).cancel_task(tid)\n",
     "                    pass  # await TaskRouter(db, self.redis, LoadBalancer(self.redis)).cancel_task(tid)\n",
     "orchestrator", CANCEL + "TheVoiceTellsTheTruthTests::test_it_checks_again_afterwards"),
    # --- UI
    ("ui-knopf-versteckt-bei-laufend", SEITE,
     '? "bg-red-500/10 border-red-500/20 text-red-400 hover:bg-red-500/20"',
     '? "bg-red-500/10 border-red-500/20 text-red-400 hover:bg-red-500/20 opacity-0 group-hover:opacity-100"',
     "orchestrator", CANCEL + "TheUiHasAManualStopTests::test_the_stop_button_is_visible_without_hovering"),
    # --- Zuhoerer im Agenten
    ("listener-kanal-falsch", CONSUMER,
     'kanal = f"agent:{self.agent_id}:task:cancel"', 'kanal = f"agent:{self.agent_id}:task:abort"',
     "agent", LISTENER + "TheAgentListensOnTheChannelTheRouterSendsOnTests::test_it_subscribes_to_exactly_that_channel"),
    ("listener-stoppt-immer-alle", CONSUMER,
     "                    else [(wen, self._runner_by_task.get(wen))]",
     "                    else list(self._runner_by_task.items())",
     "agent", LISTENER + "TheAgentListensOnTheChannelTheRouterSendsOnTests::test_it_can_stop_one_task_not_only_everything"),
    ("auskommentiert-interrupt", CONSUMER,
     "                            await runner.interrupt()\n", "                            pass  # await runner.interrupt()\n",
     "agent", LISTENER + "TheAgentListensOnTheChannelTheRouterSendsOnTests::test_it_can_stop_one_task_not_only_everything"),
    ("listener-stoppt-fertige", CONSUMER,
     '                        if getattr(runner, "is_running", False):\n', '                        if True:\n',
     "agent", LISTENER + "TheAgentListensOnTheChannelTheRouterSendsOnTests::test_a_runner_that_already_finished_is_left_alone"),
    ("listener-stirbt-an-fehler", CONSUMER,
     '                        logger.warning("Aufgabe %s liess sich nicht stoppen: %s", tid, e)\n',
     '                        raise\n',
     "agent", LISTENER + "TheAgentListensOnTheChannelTheRouterSendsOnTests::test_a_failing_interrupt_does_not_kill_the_listener_either"),
    ("listener-nicht-nebenlaeufig", CONSUMER,
     "        abbruch = asyncio.create_task(self._cancel_listener())\n",
     "        abbruch = asyncio.create_task(self._herzschlag(None))\n",
     "agent", LISTENER + "TheListenerRunsAlongsideTheQueueTests::test_start_lets_it_loose_as_its_own_task"),
    ("mapping-nicht-geraeumt", CONSUMER,
     "                self._runner_by_task.pop(task_id, None)\n", "                pass\n",
     "agent", LISTENER + "TheMappingIsCleanedUpAfterwardsTests::test_the_runner_is_registered_during_and_gone_after_the_task"),
    ("mapping-nicht-gesetzt", CONSUMER,
     "            self._runner_by_task[task_id] = runner\n", "            pass\n",
     "agent", LISTENER + "TheMappingIsCleanedUpAfterwardsTests::test_the_runner_is_registered_during_and_gone_after_the_task"),
    # --- Herzschlag im Orchestrator
    ("handler-nicht-fortgeschrieben", ROUTER, None, None,
     "orchestrator", HEART + "DerOrchestratorNimmtEsEntgegenTests::test_der_handler_schiebt_die_zeile_weiter"),
    ("handler-belebt-wieder", ROUTER,
     "        if not task or task.status != TaskStatus.RUNNING:\n            return  # nur eine laufende",
     "        if not task:\n            return  # nur eine laufende",
     "orchestrator", HEART + "DerOrchestratorNimmtEsEntgegenTests::test_nur_fuer_eine_laufende_aufgabe"),
    ("auskommentiert-checkpoint", ROUTER,
     '            await checkpoint(self.db, f"task:{task_id}", kind="agent_task", ref_id=task_id)\n',
     '            pass  # await checkpoint(self.db, f"task:{task_id}", kind="agent_task", ref_id=task_id)\n',
     "orchestrator", HEART + "DerOrchestratorNimmtEsEntgegenTests::test_die_vorhandene_spalte_wird_endlich_gefuettert"),
    ("checkpoint-fehler-reisst-mit", ROUTER,
     '            logger.debug(f"job_state heartbeat fuer {task_id} nicht gesetzt: {e}")\n',
     '            raise\n',
     "orchestrator", HEART + "DerOrchestratorNimmtEsEntgegenTests::test_ein_fehler_in_der_spalte_reisst_das_lebenszeichen_nicht_mit"),
    ("auskommentiert-watchdog-cancel", SCHED, None, None,
     "orchestrator", HEART + "DerAgentWirdWirklichGestopptTests::test_beim_abraeumen_wird_abgebrochen"),
    ("watchdog-json-nutzlast", SCHED,
     '                            f"agent:{task.agent_id}:task:cancel", task.id\n',
     '                            f"agent:{task.agent_id}:task:cancel", _json.dumps({"task_id": task.id})\n',
     "orchestrator", HEART + "DerAgentWirdWirklichGestopptTests::test_die_nutzlast_passt_zum_zuhoerer"),
    ("watchdog-ruft-agent-None", SCHED,
     "                if self.redis and self.redis.client and task.agent_id:\n",
     "                if self.redis and self.redis.client:\n",
     "orchestrator", HEART + "DerAgentWirdWirklichGestopptTests::test_ohne_agent_wird_niemand_gerufen"),
    ("stale-bleibt-running", WATCH,
     "    task.status = TaskStatus.FAILED\n    task.completed_at = now\n", "    task.completed_at = now\n",
     "orchestrator", HEART + "DerAgentWirdWirklichGestopptTests::test_beim_abraeumen_wird_abgebrochen"),
    # --- Herzschlag im Agenten
    ("herzschlag-kanal-falsch", CONSUMER,
     '                        "task:heartbeat",\n', '                        "task:pulse",\n',
     "agent", LOOP + "DerAgentSendetEinLebenszeichenTests::test_es_gibt_eine_herzschlag_schleife"),
    ("auskommentiert-herzschlag-publish", CONSUMER, None, None,
     "agent", LOOP + "DerAgentSendetEinLebenszeichenTests::test_es_gibt_eine_herzschlag_schleife"),
    ("herzschlag-stirbt-bei-fehler", CONSUMER,
     '                logger.debug(f"Herzschlag fuer {task_id} nicht zugestellt: {e}")\n', '                raise\n',
     "agent", LOOP + "DerAgentSendetEinLebenszeichenTests::test_ein_fehlschlag_reisst_die_aufgabe_nicht_mit"),
    ("herzschlag-schluckt-abbruch", CONSUMER,
     "            except asyncio.CancelledError:\n                raise\n            except Exception as e:  # noqa: BLE001\n                logger.debug(f\"Herzschlag",
     "            except asyncio.CancelledError:\n                return\n            except Exception as e:  # noqa: BLE001\n                logger.debug(f\"Herzschlag",
     "agent", LOOP + "DerAgentSendetEinLebenszeichenTests::test_ein_abbruch_beendet_sie_aber"),
    ("takt-zu-lang", CONSUMER,
     "    HERZSCHLAG_SEKUNDEN = 60\n", "    HERZSCHLAG_SEKUNDEN = 600\n",
     "agent", LOOP + "DerAgentSendetEinLebenszeichenTests::test_der_takt_liegt_deutlich_unter_der_schwelle"),
    ("herzschlag-nicht-gestartet", CONSUMER,
     "            herzschlag = asyncio.create_task(self._herzschlag(task_id))\n",
     "            herzschlag = None  # asyncio.create_task(self._herzschlag(task_id))\n",
     "agent", LOOP + "DieSchleifeLebtGenauSoLangeWieDieAufgabeTests::test_sie_laeuft_neben_der_aufgabe"),
    ("herzschlag-erst-nach-der-arbeit", CONSUMER, None, None,
     "agent", LOOP + "DieSchleifeLebtGenauSoLangeWieDieAufgabeTests::test_sie_laeuft_neben_der_aufgabe"),
    ("herzschlag-nicht-beendet", CONSUMER,
     "            if herzschlag is not None:\n                herzschlag.cancel()\n",
     "            pass\n",
     "agent", LOOP + "DieSchleifeLebtGenauSoLangeWieDieAufgabeTests::test_sie_wird_am_ende_beendet"),
    # --- Gegenleser-Funde (15.09.2026): 12 stille Faelle + 2 Haenger gegen die
    #     erste Fassung dieser Tests. Jeder davon bleibt hier als Wache stehen.
    ("gl-main-subscribe-auskommentiert", MAIN,
     '            await pubsub.subscribe("task:heartbeat")\n',
     '            pass  # await pubsub.subscribe("task:heartbeat")\n',
     "orchestrator", HEART + "DerOrchestratorNimmtEsEntgegenTests::test_er_wird_abonniert"),
    ("gl-main-handler-auskommentiert", MAIN,
     '                        await router.handle_task_heartbeat(data)\n',
     '                        pass  # await router.handle_task_heartbeat(data)\n',
     "orchestrator", HEART + "DerOrchestratorNimmtEsEntgegenTests::test_und_einem_handler_zugeordnet"),
    ("gl-main-handler-vertauscht", MAIN,
     '                        await router.handle_task_heartbeat(data)\n',
     '                        await router.handle_task_completion(data)\n',
     "orchestrator", HEART + "DerOrchestratorNimmtEsEntgegenTests::test_und_einem_handler_zugeordnet"),
    ("gl-schwelle-fest-verdrahtet", SCHED,
     'schwelle = _td(minutes=max(1, int(getattr(_cfg, "watchdog_stale_task_minutes", 180))))',
     'schwelle = _td(minutes=30)  # watchdog_stale_task_minutes',
     "orchestrator", HEART + "DerAgentWirdWirklichGestopptTests::test_der_waechter_liest_die_schwelle_aus_der_einstellung"),
    ("gl-meldung-feste-minuten", SCHED,
     "            minuten = int(schwelle.total_seconds() // 60)\n",
     "            minuten = 30  # int(schwelle.total_seconds() // 60)\n",
     "orchestrator", HEART + "DerAgentWirdWirklichGestopptTests::test_die_meldung_nennt_die_wirkliche_schwelle"),
    ("gl-handler-commit-vor-fortschreiben", ROUTER,
     "        task.updated_at = datetime.now(timezone.utc)\n        await self.db.commit()\n",
     "        await self.db.commit()\n        task.updated_at = datetime.now(timezone.utc)\n",
     "orchestrator", HEART + "DerOrchestratorNimmtEsEntgegenTests::test_der_handler_schiebt_die_zeile_weiter"),
    ("gl-router-commit-vor-status", ROUTER,
     "        task.status = TaskStatus.CANCELLED\n        task.completed_at = datetime.now(timezone.utc)\n        await self.db.commit()\n",
     "        await self.db.commit()\n        task.status = TaskStatus.CANCELLED\n        task.completed_at = datetime.now(timezone.utc)\n",
     "orchestrator", CANCEL + "ARunningTaskCanBeStoppedTests::test_the_router_no_longer_refuses_running_tasks"),
    ("gl-voice-nachsehen-vor-abbruch", VOICE, None, None,
     "orchestrator", CANCEL + "TheVoiceTellsTheTruthTests::test_it_checks_again_afterwards"),
    ("gl-voice-fruehausstieg-weg", VOICE,
     "        if not vorher:\n", "        if False:\n",
     "orchestrator", CANCEL + "TheVoiceTellsTheTruthTests::test_nothing_open_is_not_an_error"),
    ("gl-voice-fehler-reisst-schleife", VOICE,
     '                logger.info("[Sprache] %s nicht abbrechbar: %s", tid, e)\n',
     '                raise\n',
     "orchestrator", CANCEL + "TheVoiceTellsTheTruthTests::test_one_that_just_finished_does_not_stop_the_others"),
    ("gl-ui-woerter-vertauscht", SEITE,
     '{laeuft ? "Stoppen" : "Abbrechen"}', '{laeuft ? "Abbrechen" : "Stoppen"}',
     "orchestrator", CANCEL + "TheUiHasAManualStopTests::test_the_words_distinguish_the_two_cases"),
    ("gl-ui-cancancel-verkuerzt", SEITE,
     'const canCancel = laeuft || task.status === "queued" || task.status === "pending";',
     'const canCancel = laeuft || false; // const canCancel = laeuft || task.status === "queued" || task.status === "pending";',
     "orchestrator", CANCEL + "TheUiHasAManualStopTests::test_a_running_task_can_be_stopped_from_the_list"),
    ("gl-ui-laeuft-auskommentiert", SEITE,
     'const laeuft = task.status === "running";',
     'const laeuft = false; // const laeuft = task.status === "running";',
     "orchestrator", CANCEL + "TheUiHasAManualStopTests::test_a_running_task_can_be_stopped_from_the_list"),
    ("gl-listener-endlos", CONSUMER,
     "            while self.running:\n                nachricht = await pubsub.get_message(",
     "            while True:\n                nachricht = await pubsub.get_message(",
     "agent", LISTENER + "TheAgentListensOnTheChannelTheRouterSendsOnTests::test_it_subscribes_to_exactly_that_channel"),
    ("gl-listener-typfilter-weg", CONSUMER,
     '                if not nachricht or nachricht.get("type") != "message":\n',
     '                if not nachricht:\n',
     "agent", LISTENER + "TheAgentListensOnTheChannelTheRouterSendsOnTests::test_a_subscribe_confirmation_is_not_read_as_a_task_id"),
    ("gl-herzschlag-fruehausstieg-weg", CONSUMER,
     "        if not task_id:\n            return\n",
     "        if False:\n            return\n",
     "agent", LOOP + "DerAgentSendetEinLebenszeichenTests::test_ohne_kennung_schlaegt_nichts"),
]


def sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def pytest(suite: str, test: str) -> int:
    """0 = gruen, 1 = der Test ist ROT, 2 = gar nicht gelaufen (Sammel-/Syntaxfehler).

    Eine Mutation, die den Import zerbricht, darf nicht als 'erkannt' zaehlen —
    dann hat der Test nichts geprueft."""
    pfad = test if suite == "agent" else test.replace("orchestrator/", "", 1)
    r = subprocess.run([PY[suite], "-m", "pytest", "-q", "-x", "-p", "no:cacheprovider", pfad],
                       cwd=ROOT / suite, capture_output=True, text=True, timeout=300)
    if r.returncode == 0:
        return 0
    return 1 if " failed" in r.stdout else 2


def _auskommentieren(text: str, anfang: str, ende: str, ab: str = "") -> str:
    """Den Block von `anfang` bis einschliesslich `ende` (erste Fundstelle
    hinter `ab`) Zeile fuer Zeile auskommentieren, Einrueckung erhalten."""
    start = text.index(ab) if ab else 0
    a = text.index(anfang, start)
    b = text.index(ende, a) + len(ende)
    block = text[a:b]
    einzug = block[:len(block) - len(block.lstrip(" "))]
    neu = "".join(einzug + "# " + z.lstrip(" ") for z in block.splitlines(True))
    return text[:a] + einzug + "pass\n" + neu + text[b:]


def sonderfall(label: str, text: str) -> str:
    """Mutationen, die nicht als einfache Ersetzung ausdrueckbar sind."""
    if label == "auskommentiert-router-publish":
        return _auskommentieren(text, "                    await self.redis.client.publish(\n",
                                "                    )\n", ab="async def cancel_task")
    if label == "handler-nicht-fortgeschrieben":
        kopf = text.index("async def handle_task_heartbeat")
        a = text.index("        task.updated_at = datetime.now(timezone.utc)\n", kopf)
        return text[:a] + "        pass  # task.updated_at = datetime.now(timezone.utc)\n" + text[a + len("        task.updated_at = datetime.now(timezone.utc)\n"):]
    if label == "auskommentiert-watchdog-cancel":
        return _auskommentieren(text, "                        await self.redis.client.publish(\n",
                                "                        )\n", ab="async def _tick_stale_task_watchdog")
    if label == "auskommentiert-herzschlag-publish":
        return _auskommentieren(text, "                    await self.redis.publish(\n",
                                "                    )\n", ab="async def _herzschlag")
    if label == "herzschlag-erst-nach-der-arbeit":
        zeile = "            herzschlag = asyncio.create_task(self._herzschlag(task_id))\n"
        a = text.index(zeile)
        text = text[:a] + text[a + len(zeile):]
        marke = "            status = result_data.get(\"status\", \"unknown\")\n"
        b = text.index(marke)
        return text[:b] + zeile + text[b:]
    if label == "gl-voice-nachsehen-vor-abbruch":
        zeile = "        uebrig = await _offene()\n"
        a = text.index(zeile)
        text = text[:a] + text[a + len(zeile):]
        marke = "        for tid, _titel in vorher:\n"
        b = text.index(marke)
        return text[:b] + zeile + text[b:]
    raise KeyError(label)


def main() -> int:
    dateien = {ROOT / m[1] for m in MUTATIONEN}
    orig = {p: p.read_bytes() for p in dateien}
    ergebnis = []
    try:
        print("=== KONTROLLE (unveraenderter Code): alle Zieltests muessen gruen sein")
        for label, _, _, _, suite, test in MUTATIONEN:
            rc = pytest(suite, test)
            print(f"  {'ok ' if rc == 0 else 'ROT'} {label}")
            if rc != 0:
                print("Kontrolle rot -- Testbau selbst kaputt"); return 2
        print("=== MUTATIONEN: jede muss ihren Test rot faerben")
        for label, datei, alt, neu, suite, test in MUTATIONEN:
            p = ROOT / datei
            text = orig[p].decode()
            if alt is None:
                mut = sonderfall(label, text)
            else:
                assert text.count(alt) == 1, f"Anker fuer {label} nicht eindeutig: {text.count(alt)}x"
                mut = text.replace(alt, neu, 1)
            assert mut != text
            p.write_text(mut)
            try:
                rc = pytest(suite, test)
            finally:
                p.write_bytes(orig[p])
            ergebnis.append((label, rc == 1))
            wort = {0: "STILL -- Test sieht die Mutation NICHT", 1: "ROT (erwartet)",
                    2: "NICHT GELAUFEN (Sammelfehler) -- zaehlt nicht"}[rc]
            print(f"  {wort} {label}")
    finally:
        for p, inhalt in orig.items():
            p.write_bytes(inhalt)
        for p, inhalt in orig.items():
            assert sha(p) == hashlib.sha256(inhalt).hexdigest(), f"nicht wiederhergestellt: {p}"
    stumm = [l for l, rot in ergebnis if not rot]
    print(f"=== {len(ergebnis) - len(stumm)}/{len(ergebnis)} Mutationen erkannt; stumm: {stumm or 'keine'}")
    return 1 if stumm else 0


if __name__ == "__main__":
    sys.exit(main())
