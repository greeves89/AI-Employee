#!/usr/bin/env python3
"""Gegenprobe zu #726 Batch 9: je Zusicherung mindestens EINE Mutation vom Typ
"auskommentieren / durch pass ersetzen" und eine vom Typ "Wert/Zeile ersetzen"
am Produktivcode (nicht an den Tests).

Sechs Testdateien wurden von Zeichenfenstern auf Verhalten / syntaktische
Bloecke umgestellt. Diese Batterie beweist, dass die neuen Zusicherungen NICHT
leer sind: jede Mutation muss mindestens einen Test rot machen.

"Erkannt" gilt NUR, wenn ` failed` in der pytest-Ausgabe steht — ein rc != 0
allein kann auch ein Sammelfehler (Import, Syntax) sein und zaehlt nicht.
Die Urfassung wird im finally zurueckgeschrieben und per SHA-256 belegt.
"""
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
ORCH = REPO / "orchestrator"
PY_ORCH = "/workspace/.venv-orch/bin/python"  # System-python3 hat kein pytest-asyncio

ORCH_TESTS = [
    "tests/test_agent_checks_its_roster.py",
    "tests/test_delegation_to_unknown_agent.py",
    "tests/test_host_memory_visibility.py",
    "tests/test_questions_reach_a_human.py",
    "tests/test_vault_import_export.py",
    "tests/test_workspace_file_editing.py",
]

AM = "orchestrator/app/core/agent_manager.py"
MAIN = "orchestrator/app/main.py"
WFE = "orchestrator/app/services/workflow_engine.py"
TR = "orchestrator/app/core/task_router.py"
BRAINS = "orchestrator/app/api/brains.py"
AGENTS = "orchestrator/app/api/agents.py"
PREVIEW = "frontend/src/components/files/file-preview.tsx"

LANG = "Erlaeuterung " * 120  # sprengt jedes alte Zeichenfenster (>1400 Zeichen)

# (Name, Datei, Suchmuster, Ersatz) — jeweils genau EIN Mechanismus zerstoert.
MUTATIONEN = [
    # --- test_agent_checks_its_roster.py: DEFAULT_CLAUDE_MD, Roster-Absatz ---
    # Bei Prosa ist "Zeile loeschen" das Gegenstueck zu "auskommentieren".
    ("roster_vor_delegation_geloescht", AM,
     "  - before you delegate, send a message, or name a colleague to the user\n",
     ""),
    ("roster_vor_memory_geloescht", AM,
     "  - before you write anything about the team into memory or into a file\n",
     ""),
    ("roster_kollege_weg_plant_weiter", AM,
     "do not queue work for a name that is no longer there",
     "keep the plan as it is"),
    ("roster_vergessen_per_save", AM,
     "`memory_delete`; leaving it there means you will believe it again tomorrow.",
     "`memory_save`; leaving it there means you will believe it again tomorrow."),
    # Satz mit memory_delete HINTER die naechste Top-Level-Aufzaehlung geschoben:
    # die Absatzgrenze muss echt sein, nicht ein Fenster von 900 Zeichen.
    ("roster_vergessen_hinter_absatzgrenze", AM, [
        (" If a\nmemory of yours turns out to be wrong about the team, delete it with\n"
         "`memory_delete`; leaving it there means you will believe it again tomorrow.\n",
         "\n"),
        ("- **send_message** - Send a text message to another agent\n",
         "- **send_message** - Send a text message to another agent\n"
         "If a memory of yours turns out to be wrong about the team, delete it with\n"
         "`memory_delete`; leaving it there means you will believe it again tomorrow.\n"),
    ], None),

    # --- test_questions_reach_a_human.py: DEFAULT_CLAUDE_MD, Listenpunkt "In a task" ---
    # request_approval steht ZWEIMAL in der Naehe (auch im Nachbarpunkt
    # "Which way to go"); nur der im eigenen Punkt darf zaehlen.
    ("frage_falscher_kanal", AM,
     "required before you can continue, call `request_approval` — that one reaches the human and",
     "required before you can continue, call `escalate_if_unsure` — that one reaches the human and"),
    ("frage_liefer_satz_geloescht", AM,
     "    So: **do the work** with what you have and pick the safest reasonable default, then say in\n"
     "    ONE line what you were missing and which decision you made. If a decision is genuinely\n"
     "    required before you can continue, call `request_approval` — that one reaches the human and\n"
     "    waits. Plain text does not.\n",
     ""),
    ("frage_kein_default", AM,
     "pick the safest reasonable default",
     "stop and wait for an answer"),
    # Anweisung in den Nachbarpunkt verschoben: Fenster ab "Asking the user
    # something" haette sie noch gesehen, der Listenpunkt nicht.
    ("frage_anweisung_im_nachbarpunkt", AM, [
        ("    So: **do the work** with what you have and pick the safest reasonable default, then say in\n"
         "    ONE line what you were missing and which decision you made. If a decision is genuinely\n"
         "    required before you can continue, call `request_approval` — that one reaches the human and\n"
         "    waits. Plain text does not.\n",
         ""),
        ("  - **Which way to go: ask how REVERSIBLE the action is.**",
         "  - **Which way to go: ask how REVERSIBLE the action is.** Pick the safest reasonable default."),
    ], None),

    # --- test_delegation_to_unknown_agent.py: main.py Handler (Verhalten) ---
    ("handler_rumpf_auskommentiert", MAIN,
     '    return JSONResponse(status_code=400, content={"detail": str(exc)})\n',
     '    pass  # return JSONResponse(status_code=400, content={"detail": str(exc)}) — MUTATION\n'),
    ("handler_antwortet_500", MAIN,
     'return JSONResponse(status_code=400, content={"detail": str(exc)})',
     'return JSONResponse(status_code=500, content={"detail": str(exc)})  # MUTATION'),
    ("handler_verschluckt_den_grund", MAIN,
     'return JSONResponse(status_code=400, content={"detail": str(exc)})',
     'return JSONResponse(status_code=400, content={"detail": "unknown agent"})  # MUTATION'),
    # --- workflow_engine.py advance_run (Verhalten) ---
    ("wf_status_auskommentiert", WFE,
     '                    run.status = "failed"\n                    run.error = f\'Schritt',
     '                    pass  # run.status = "failed" — MUTATION\n                    run.error = f\'Schritt'),
    ("wf_status_bleibt_running", WFE,
     '                    run.status = "failed"\n                    run.error = f\'Schritt',
     '                    run.status = "running"  # MUTATION\n                    run.error = f\'Schritt'),
    ("wf_error_auskommentiert", WFE,
     "                    run.error = f'Schritt „{run.current_step}“: {e}'[:2000]\n",
     "                    pass  # run.error = ... — MUTATION\n"),
    ("wf_error_ohne_grund", WFE,
     "                    run.error = f'Schritt „{run.current_step}“: {e}'[:2000]\n",
     "                    run.error = f'Schritt „{run.current_step}“ fehlgeschlagen'  # MUTATION\n"),
    ("wf_commit_auskommentiert", WFE,
     "                    run.completed_at = _now()\n                    await db.commit()\n"
     "                    logger.warning(\n                        \"workflow %s gestoppt",
     "                    run.completed_at = _now()\n                    pass  # await db.commit() — MUTATION\n"
     "                    logger.warning(\n                        \"workflow %s gestoppt"),
    # --- main.py _resume_agent_task except-Block (AST-Block ohne Kommentare) ---
    ("resume_delete_job_auskommentiert", MAIN,
     '                logger.warning("[Resume] Job %s verworfen: %s", job.id, e)\n'
     '                await delete_job(db, job.id)\n                return\n',
     '                logger.warning("[Resume] Job %s verworfen: %s", job.id, e)\n'
     '                # await delete_job(db, job.id)  # MUTATION\n                return\n'),
    ("resume_loescht_falschen_job", MAIN,
     '                logger.warning("[Resume] Job %s verworfen: %s", job.id, e)\n'
     '                await delete_job(db, job.id)\n                return\n',
     '                logger.warning("[Resume] Job %s verworfen: %s", job.id, e)\n'
     '                await delete_job(db, job.ref_id)  # MUTATION\n                return\n'),
    ("resume_faengt_alles_statt_unknown", MAIN,
     "            except UnknownAgentError as e:\n                # Der Agent wurde geloescht, waehrend",
     "            except Exception as e:  # MUTATION\n                # Der Agent wurde geloescht, waehrend"),

    # --- test_host_memory_visibility.py: main.py Start (AST-try-Block) ---
    ("start_ohne_try", MAIN,
     "    try:\n        from app.core.host_memory import beim_start_melden\n\n"
     "        beim_start_melden()\n    except Exception as e:  # noqa: BLE001\n"
     '        logger.debug("Host-Speicherpruefung nicht moeglich: %s", e)\n',
     "    from app.core.host_memory import beim_start_melden  # MUTATION: kein try\n\n"
     "    beim_start_melden()\n"),
    ("start_faengt_nur_valueerror", MAIN,
     "        beim_start_melden()\n    except Exception as e:  # noqa: BLE001\n",
     "        beim_start_melden()\n    except ValueError as e:  # MUTATION\n"),
    ("start_except_exception_nur_im_kommentar", MAIN,
     "        beim_start_melden()\n    except Exception as e:  # noqa: BLE001\n",
     "        beim_start_melden()\n    except ValueError as e:  # except Exception waere zu breit — MUTATION\n"),
    # --- task_router.py handle_task_completion (Verhalten) ---
    ("abriss_zusatz_ersetzt_original", TR,
     '                task.error = f"{task.error} — {zusatz}"\n',
     '                task.error = zusatz  # MUTATION\n'),
    ("abriss_zusatz_auskommentiert", TR,
     '                task.error = f"{task.error} — {zusatz}"\n',
     '                pass  # task.error = f"{task.error} — {zusatz}" — MUTATION\n'),
    ("abriss_aufruf_auskommentiert", TR,
     "            zusatz = erklaerung_fuer_abriss(task.error)\n",
     "            zusatz = None  # erklaerung_fuer_abriss(task.error) — MUTATION\n"),
    ("abriss_reihenfolge_vertauscht", TR,
     '                task.error = f"{task.error} — {zusatz}"\n',
     '                task.error = f"{zusatz} — {task.error}"  # MUTATION\n'),

    # --- test_vault_import_export.py: brains.py (Verhalten) ---
    ("import_reindex_auskommentiert", BRAINS,
     "        stats = await vault_indexer.reindex_vault(db, brain.label, brain.host_path)\n"
     "    except Exception as e:  # noqa: BLE001\n        log.warning(\"[Vault] Neuindizierung",
     "        stats = {}  # await vault_indexer.reindex_vault(db, brain.label, brain.host_path) — MUTATION\n"
     "    except Exception as e:  # noqa: BLE001\n        log.warning(\"[Vault] Neuindizierung"),
    ("import_reindex_ohne_fangnetz", BRAINS,
     "    try:\n        stats = await vault_indexer.reindex_vault(db, brain.label, brain.host_path)\n"
     "    except Exception as e:  # noqa: BLE001\n"
     '        log.warning("[Vault] Neuindizierung nach Import fehlgeschlagen brain=%s: %s",\n'
     "                    brain.slug, vault.safe_log(e))\n"
     '        stats = {"error": "Neuindizierung fehlgeschlagen — bitte manuell anstossen"}\n',
     "    stats = await vault_indexer.reindex_vault(db, brain.label, brain.host_path)  # MUTATION\n"),
    ("import_reindex_fehler_verschwiegen", BRAINS,
     '        stats = {"error": "Neuindizierung fehlgeschlagen — bitte manuell anstossen"}\n',
     '        stats = {}  # MUTATION\n'),
    ("import_reindex_fehler_anderer_text", BRAINS,
     '        stats = {"error": "Neuindizierung fehlgeschlagen — bitte manuell anstossen"}\n',
     '        stats = {"error": "Indexfehler"}  # MUTATION\n'),
    ("import_ok_false_bei_reindexfehler", BRAINS,
     '    return {"ok": True, "brain": brain.slug, **bericht.als_dict(), "index": stats}\n',
     '    return {"ok": "error" not in stats, "brain": brain.slug, **bericht.als_dict(), "index": stats}  # MUTATION\n'),
    ("import_admin_auskommentiert", BRAINS,
     "    import io\n    import zipfile\n\n    _require_admin(user)\n",
     "    import io\n    import zipfile\n\n    pass  # _require_admin(user) — MUTATION\n"),
    ("import_admin_nach_db", BRAINS,
     "    import io\n    import zipfile\n\n    _require_admin(user)\n    brain = await db.get(SecondBrain, brain_id)\n",
     "    import io\n    import zipfile\n\n    brain = await db.get(SecondBrain, brain_id)\n    _require_admin(user)  # MUTATION\n"),
    ("export_admin_auskommentiert", BRAINS,
     "    from fastapi.responses import Response\n\n    _require_admin(user)\n",
     "    from fastapi.responses import Response\n\n    pass  # _require_admin(user) — MUTATION\n"),
    ("export_admin_nach_db", BRAINS,
     "    from fastapi.responses import Response\n\n    _require_admin(user)\n    brain = await db.get(SecondBrain, brain_id)\n",
     "    from fastapi.responses import Response\n\n    brain = await db.get(SecondBrain, brain_id)\n    _require_admin(user)  # MUTATION\n"),
    ("admin_pruefung_laesst_member_durch", BRAINS,
     '    if not (hasattr(user, "role") and user.role == UserRole.ADMIN):\n        raise HTTPException(status_code=403, detail="Admin only")\n',
     '    if not hasattr(user, "role"):  # MUTATION\n        raise HTTPException(status_code=403, detail="Admin only")\n'),

    # --- test_workspace_file_editing.py: agents.py save_file_content (Verhalten) ---
    ("save_check_owner_auskommentiert", AGENTS,
     "    await _check_owner(agent_id, user, db)\n    agent = await manager._get_agent(agent_id)\n    if not agent.container_id:\n        raise HTTPException(status_code=400, detail=\"Agent has no container\")\n    try:\n        geschrieben",
     "    pass  # await _check_owner(agent_id, user, db) — MUTATION\n    agent = await manager._get_agent(agent_id)\n    if not agent.container_id:\n        raise HTTPException(status_code=400, detail=\"Agent has no container\")\n    try:\n        geschrieben"),
    ("save_login_nur_get_db", AGENTS,
     "async def save_file_content(\n    agent_id: str,\n    body: DateiInhalt,\n    user=Depends(require_auth),\n",
     "async def save_file_content(\n    agent_id: str,\n    body: DateiInhalt,\n    user=Depends(get_db),  # MUTATION\n"),
    ("save_am_filemanager_vorbei", AGENTS,
     "        geschrieben = file_mgr.write_file(agent.container_id, body.path, body.content)\n",
     "        geschrieben = len(body.content.encode())  # file_mgr.write_file(...) — MUTATION\n"),
    ("save_valueerror_wird_400_auskommentiert", AGENTS,
     "    except ValueError as e:\n        raise HTTPException(status_code=400, detail=str(e))\n    except FileNotFoundError",
     "    except ValueError as e:\n        pass  # raise HTTPException(status_code=400, ...) — MUTATION\n    except FileNotFoundError"),
    ("save_valueerror_wird_422", AGENTS,
     "    except ValueError as e:\n        raise HTTPException(status_code=400, detail=str(e))\n    except FileNotFoundError",
     "    except ValueError as e:\n        raise HTTPException(status_code=422, detail=str(e))  # MUTATION\n    except FileNotFoundError"),
    ("save_antwort_ohne_bytes", AGENTS,
     '    return {"path": body.path, "bytes": geschrieben}\n',
     '    return {"path": body.path}  # MUTATION\n'),
    # --- brains.py: der Import muss im Vault landen, nicht irgendwo ---
    ("import_schreibt_am_vault_vorbei", BRAINS,
     "            bericht = vault_transfer.importiere_zip(brain.host_path, archiv, ersetzen=replace)\n",
     "            bericht = vault_transfer.importiere_zip(__import__(\"tempfile\").mkdtemp(), archiv, ersetzen=replace)  # MUTATION\n"),
    # --- file-preview.tsx (Klammerblock ohne Kommentare; manuelle Probe bestaetigt) ---
    ("ui_entwurf_beim_wechsel_auskommentiert", PREVIEW,
     '    setHtmlTab("rendered");\n    setEntwurf(null);\n',
     '    setHtmlTab("rendered");\n    // setEntwurf(null); // MUTATION\n'),
    ("ui_entwurf_beim_wechsel_bleibt", PREVIEW,
     '    setHtmlTab("rendered");\n    setEntwurf(null);\n',
     '    setHtmlTab("rendered");\n    setSpeichert(false); // MUTATION\n'),
    ("ui_fehler_verwirft_entwurf", PREVIEW,
     '      setSpeicherFehler(e instanceof Error ? e.message : "Speichern fehlgeschlagen");\n',
     '      setSpeicherFehler(e instanceof Error ? e.message : "Speichern fehlgeschlagen");\n'
     '      setEntwurf(null); // MUTATION\n'),
    ("ui_fehler_wird_nicht_gemeldet", PREVIEW,
     '      setSpeicherFehler(e instanceof Error ? e.message : "Speichern fehlgeschlagen");\n',
     '      console.error(e); // setSpeicherFehler(...) — MUTATION\n'),
    # Verdacht: der Klammerblock ab setHtmlTab reicht bis zum Ende des Effekts
    # und schliesst verschachtelte Bloecke ein — setEntwurf(null) in einem
    # inneren Zweig (nur bei Bildern) bestuende dann trotzdem.
    ("ui_entwurf_nur_bei_bildern_verworfen", PREVIEW, [
        ('    setHtmlTab("rendered");\n    setEntwurf(null);\n',
         '    setHtmlTab("rendered");\n'),
        ("          // Images use <img src> directly, binary shows download\n",
         "          // Images use <img src> directly, binary shows download\n"
         "          setEntwurf(null); // MUTATION: nur noch in diesem Zweig\n"),
    ], None),
]

# Harmlose Aenderungen (Kommentare, lange Saetze) — hier MUSS alles gruen
# bleiben. Sonst ist die Fenster-Krankheit nur verschoben: rot ohne
# Verhaltensaenderung.
HARMLOS = [
    ("harmlos_langer_satz_im_roster_absatz", AM,
     "asks you about it:\n  - before you delegate,",
     "asks you about it (" + LANG + "):\n  - before you delegate,"),
    ("harmlos_langer_satz_im_frage_punkt", AM,
     "    work was done.\n    So: **do the work**",
     "    work was done. " + LANG + "\n    So: **do the work**"),
    ("harmlos_langer_kommentar_vor_delete_job", MAIN,
     '                logger.warning("[Resume] Job %s verworfen: %s", job.id, e)\n                await delete_job(db, job.id)\n',
     '                logger.warning("[Resume] Job %s verworfen: %s", job.id, e)\n'
     "                # " + LANG + "\n"
     "                await delete_job(db, job.id)\n"),
    ("harmlos_langer_kommentar_vor_except_exception", MAIN,
     "        beim_start_melden()\n    except Exception as e:  # noqa: BLE001\n",
     "        beim_start_melden()\n        # " + LANG + "\n    except Exception as e:  # noqa: BLE001\n"),
    ("harmlos_kommentar_mit_except_valueerror", MAIN,
     "        beim_start_melden()\n    except Exception as e:  # noqa: BLE001\n",
     "        beim_start_melden()\n        # except ValueError waere zu eng\n    except Exception as e:  # noqa: BLE001\n"),
    ("harmlos_langer_kommentar_im_wf_except", WFE,
     '                    run.status = "failed"\n                    run.error = f\'Schritt',
     "                    # " + LANG + '\n                    run.status = "failed"\n                    run.error = f\'Schritt'),
    ("harmlos_langer_kommentar_vor_reindex", BRAINS,
     "    try:\n        stats = await vault_indexer.reindex_vault(db, brain.label, brain.host_path)\n",
     "    # " + LANG + "\n    try:\n        stats = await vault_indexer.reindex_vault(db, brain.label, brain.host_path)\n"),
    ("harmlos_langer_docstring_vor_check_owner", AGENTS,
     "    derselben Stelle wie beim Lesen und Hochladen.\n    \"\"\"\n    await _check_owner(agent_id, user, db)\n",
     "    derselben Stelle wie beim Lesen und Hochladen.\n    " + LANG + "\n    \"\"\"\n    await _check_owner(agent_id, user, db)\n"),
    ("harmlos_kommentar_setEntwurf_im_catch", PREVIEW,
     '      setSpeicherFehler(e instanceof Error ? e.message : "Speichern fehlgeschlagen");\n',
     '      setSpeicherFehler(e instanceof Error ? e.message : "Speichern fehlgeschlagen");\n'
     '      // setEntwurf(null) bewusst NICHT — der Text bleibt erhalten\n'),
    ("harmlos_blockkommentar_vor_setEntwurf", PREVIEW,
     '    setHtmlTab("rendered");\n    setEntwurf(null);\n',
     '    setHtmlTab("rendered");\n    /* ' + LANG + ' */\n    setEntwurf(null);\n'),
]


def _deckel():
    import resource
    resource.setrlimit(resource.RLIMIT_AS, (2 << 30, 2 << 30))


def _lauf(python: str, cwd: Path, tests: list[str]) -> tuple[bool, set[str]]:
    """(erkannt, rote Tests). Erkannt NUR bei ` failed` in der Ausgabe."""
    try:
        p = subprocess.run(
            [python, "-m", "pytest", *tests, "-q", "--tb=no", "-rf", "-p", "no:cacheprovider"],
            cwd=cwd, capture_output=True, text=True, timeout=240, preexec_fn=_deckel,
        )
    except subprocess.TimeoutExpired:
        return False, {"<HAENGT>"}
    rote = set(re.findall(r"^(?:SUB)?FAILED(?:\[.*\])? \S+::\w+::(\w+)", p.stdout, re.M))
    erkannt = " failed" in p.stdout
    if p.returncode != 0 and not erkannt:
        rote.add(f"<SAMMELFEHLER rc={p.returncode}>")
    return erkannt, rote


def laufe_tests() -> tuple[bool, set[str]]:
    return _lauf(PY_ORCH, ORCH, ORCH_TESTS)


def sha(rel: str) -> str:
    return hashlib.sha256((REPO / rel).read_bytes()).hexdigest()


def _anwenden(original: str, suche, ersatz) -> str | None:
    """Ersetzung(en) anwenden; None, wenn ein Muster nicht genau einmal vorkommt."""
    paare = suche if isinstance(suche, list) else [(suche, ersatz)]
    text = original
    for such, ers in paare:
        if text.count(such) != 1:
            return None
        text = text.replace(such, ers, 1)
    return text


def main() -> int:
    nur = set(sys.argv[1:])  # optional: nur diese Mutationsnamen fahren
    dateien = {rel: (REPO / rel).read_text() for _, rel, _, _ in MUTATIONEN + HARMLOS}
    hashes = {rel: sha(rel) for rel in dateien}
    ergebnis: dict[str, dict] = {}
    try:
        erkannt, rot = laufe_tests()
        if erkannt or rot:
            print(f"ABBRUCH: Tests schon ohne Mutation rot: {sorted(rot)}")
            return 1
        print("Ausgangslage gruen.\n")

        for name, rel, suche, ersatz in MUTATIONEN:
            if nur and name not in nur:
                continue
            original = dateien[rel]
            mutiert = _anwenden(original, suche, ersatz)
            if mutiert is None:
                print(f"  {name}: MUSTER NICHT GENAU 1x GEFUNDEN — nicht angewandt!")
                ergebnis[name] = {"erkannt": None, "rot": []}
                continue
            (REPO / rel).write_text(mutiert)
            try:
                erkannt, rot = laufe_tests()
            finally:
                (REPO / rel).write_text(original)
            ergebnis[name] = {"datei": rel, "erkannt": erkannt, "rot": sorted(rot)}
            marke = "ERKANNT" if erkannt else "STILL  "
            print(f"  {marke} {name} [{Path(rel).name}] -> {sorted(rot)}", flush=True)

        print("\n--- Harmlose Aenderungen (muessen gruen bleiben) ---")
        for name, rel, suche, ersatz in HARMLOS:
            if nur and name not in nur:
                continue
            original = dateien[rel]
            mutiert = _anwenden(original, suche, ersatz)
            if mutiert is None:
                print(f"  {name}: MUSTER NICHT GENAU 1x GEFUNDEN — nicht angewandt!")
                ergebnis[name] = {"erkannt": None, "rot": [], "harmlos": True}
                continue
            (REPO / rel).write_text(mutiert)
            try:
                erkannt, rot = laufe_tests()
            finally:
                (REPO / rel).write_text(original)
            ergebnis[name] = {"datei": rel, "erkannt": erkannt, "rot": sorted(rot), "harmlos": True}
            marke = "FEHLALARM" if (erkannt or rot) else "GRUEN    "
            print(f"  {marke} {name} [{Path(rel).name}] -> {sorted(rot)}", flush=True)
    finally:
        for rel, original in dateien.items():
            (REPO / rel).write_text(original)
        heil = {rel: sha(rel) == h for rel, h in hashes.items()}
        print(f"\nWiederhergestellt (SHA-256): {all(heil.values())} {heil}")

    stille = [n for n, e in ergebnis.items() if not e.get("harmlos") and not e["erkannt"]]
    fehlalarme = [n for n, e in ergebnis.items() if e.get("harmlos") and (e["erkannt"] or e["rot"])]
    echte = {n: e for n, e in ergebnis.items() if not e.get("harmlos")}
    harmlose = {n: e for n, e in ergebnis.items() if e.get("harmlos")}
    print(f"\n{len(echte) - len(stille)}/{len(echte)} Mutationen erkannt; still: {stille}")
    print(f"{len(harmlose) - len(fehlalarme)}/{len(harmlose)} harmlose Aenderungen gruen; Fehlalarme: {fehlalarme}")
    print(json.dumps(ergebnis, indent=2, ensure_ascii=False))
    return 1 if (stille or fehlalarme) else 0


if __name__ == "__main__":
    sys.exit(main())
