#!/usr/bin/env python3
"""Gegenprobe zu #726 Batch 8: je Zusicherung mindestens EINE Mutation vom Typ
"auskommentieren" und eine vom Typ "Wert/Zeile ersetzen" am Produktivcode.

Vier Testdateien wurden von Zeichenfenstern auf Verhalten / AST-Bloecke
umgestellt. Diese Batterie beweist, dass die neuen Zusicherungen NICHT leer
sind: jede Mutation muss mindestens einen Test rot machen.

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
AGENT = REPO / "agent"
PY_ORCH = "/workspace/.venv-orch/bin/python"
PY_AGENT = "/workspace/.venv-orch/bin/python"  # System-python3 hat kein pytest-asyncio

ORCH_TESTS = [
    "tests/test_context_tokens_persist.py",
    "tests/test_github_customer_name_guard.py",
    "tests/test_mcp_token_refresh_parity.py",
]
AGENT_TESTS = ["tests/test_context_editing_beta_guard.py"]

WS = "orchestrator/app/api/ws.py"
CHAT = "frontend/src/components/agents/chat.tsx"
GH = "scripts/check_github_customer_names.py"
AMAIN = "agent/app/main.py"
CODEX = "agent/app/codex_runner.py"
PROV = "agent/app/providers/anthropic_provider.py"

# (Name, Datei, Suchmuster, Ersatz) — jeweils genau EIN Mechanismus zerstoert.
MUTATIONEN = [
    # --- test_context_tokens_persist.py: ws.py (Verhalten) ---
    ("ws_context_tokens_auskommentiert", WS,
     '                    **({"context_tokens": edata["context_tokens"]}\n'
     '                       if edata.get("context_tokens") else {}),\n',
     '                    # **({"context_tokens": edata["context_tokens"]}\n'
     '                    #    if edata.get("context_tokens") else {}),  # MUTATION\n'),
    ("ws_context_tokens_ist_die_summe", WS,
     '**({"context_tokens": edata["context_tokens"]}',
     '**({"context_tokens": edata["input_tokens"]}  # MUTATION'),
    ("ws_null_wird_als_stand_gespeichert", WS,
     '                    **({"context_tokens": edata["context_tokens"]}\n'
     '                       if edata.get("context_tokens") else {}),\n',
     '                    **({"context_tokens": edata.get("context_tokens", 0)}),  # MUTATION\n'),
    # --- test_context_tokens_persist.py: chat.tsx (Klammerblock ohne Kommentare) ---
    ("ui_stand_auskommentiert", CHAT,
     "        setLiveContextTokens(letzteMitStand?.meta?.context_tokens ?? null);",
     "        // setLiveContextTokens(letzteMitStand?.meta?.context_tokens ?? null); // MUTATION"),
    ("ui_liest_die_summe", CHAT,
     "        setLiveContextTokens(letzteMitStand?.meta?.context_tokens ?? null);",
     "        setLiveContextTokens(letzteMitStand?.meta?.input_tokens ?? null); // MUTATION"),
    ("ui_faellt_stumpf_auf_null", CHAT,
     "        setLiveContextTokens(letzteMitStand?.meta?.context_tokens ?? null);",
     "        setLiveContextTokens(null); // MUTATION"),
    # --- test_github_customer_name_guard.py: main() (Verhalten) ---
    ("gh_kein_zugang_return_auskommentiert", GH,
     "        return 0  # kein Zugang ist kein Fund\n",
     "        pass  # return 0 — MUTATION\n"),
    ("gh_kein_zugang_scheitert", GH,
     "        return 0  # kein Zugang ist kein Fund\n",
     "        return 1  # MUTATION\n"),
    ("gh_fund_return_auskommentiert", GH,
     '        return 1\n    print("Keine Kundennamen',
     '        pass  # return 1 — MUTATION\n    print("Keine Kundennamen'),
    ("gh_fund_scheitert_nicht", GH,
     '        return 1\n    print("Keine Kundennamen',
     '        return 0  # MUTATION\n    print("Keine Kundennamen'),
    # --- test_mcp_token_refresh_parity.py: agent/app/main.py (AST-Rumpf) ---
    ("agent_auth_env_auskommentiert", AMAIN,
     '        os.environ["CUSTOM_MCP_AUTH"] = json.dumps(auth)\n',
     '        # os.environ["CUSTOM_MCP_AUTH"] = json.dumps(auth)  # MUTATION\n'),
    ("agent_auth_env_falscher_wert", AMAIN,
     '        os.environ["CUSTOM_MCP_AUTH"] = json.dumps(auth)\n',
     '        os.environ["CUSTOM_MCP_AUTH"] = json.dumps(headers)  # MUTATION\n'),
    ("agent_servers_vor_auth_geschrieben", AMAIN,
     '        os.environ["CUSTOM_MCP_AUTH"] = json.dumps(auth)\n'
     '        os.environ["CUSTOM_MCP_HEADERS"] = json.dumps(headers)\n'
     '        os.environ["CUSTOM_MCP_SERVERS"] = json.dumps(servers)\n',
     '        os.environ["CUSTOM_MCP_SERVERS"] = json.dumps(servers)  # MUTATION\n'
     '        os.environ["CUSTOM_MCP_AUTH"] = json.dumps(auth)\n'
     '        os.environ["CUSTOM_MCP_HEADERS"] = json.dumps(headers)\n'),
    # --- test_mcp_token_refresh_parity.py: codex_runner.py (AST-Rumpf) ---
    ("codex_env_kopie_auskommentiert", CODEX,
     "    env = os.environ.copy()\n",
     "    # env = os.environ.copy()  # MUTATION\n    env = {}\n"),
    ("codex_env_keine_kopie", CODEX,
     "    env = os.environ.copy()\n",
     "    env = {}  # MUTATION: os.environ.copy() nur noch im Kommentar\n"),
    # --- test_context_editing_beta_guard.py: anthropic_provider.py (Verhalten) ---
    ("beta_body_auskommentiert", PROV,
     '            body["context_management"] = {"edits": [{"type": "clear_tool_uses_20250919"}]}\n',
     '            pass  # body["context_management"] = ... — MUTATION\n'),
    ("beta_header_auskommentiert", PROV,
     '            headers["anthropic-beta"] = "context-management-2025-06-27"\n',
     '            pass  # headers["anthropic-beta"] = ... — MUTATION\n'),
    ("beta_wird_unbedingt_gesetzt", PROV,
     "        if not _CONTEXT_EDITING_AUS:\n            body[\"context_management\"]",
     "        if True:  # MUTATION\n            body[\"context_management\"]"),
    ("ablehnung_schaltet_nicht_ab", PROV,
     "                        _CONTEXT_EDITING_AUS = True\n",
     "                        pass  # _CONTEXT_EDITING_AUS = True — MUTATION\n"),
    ("ablehnung_wird_nicht_erkannt", PROV,
     "                            and _betrifft_context_editing(text)):",
     "                            and False):  # MUTATION"),
    ("jeder_eingabefehler_schaltet_ab", PROV,
     "                            and _betrifft_context_editing(text)):",
     "                            and True):  # MUTATION"),
    # --- Gegenleser-Funde (frischer Subagent) ---
    ("ui_aeltester_statt_juengster_stand", CHAT,
     "        const letzteMitStand = [...history].reverse().find(",
     "        const letzteMitStand = history.find(  // MUTATION"),
    ("ui_faellt_auf_0_statt_null", CHAT,
     "        setLiveContextTokens(letzteMitStand?.meta?.context_tokens ?? null);",
     "        setLiveContextTokens(letzteMitStand?.meta?.context_tokens ?? 0); // MUTATION"),
    ("agent_env_nur_noch_bei_cli", AMAIN,
     '        os.environ["CUSTOM_MCP_AUTH"] = json.dumps(auth)\n'
     '        os.environ["CUSTOM_MCP_HEADERS"] = json.dumps(headers)\n'
     '        os.environ["CUSTOM_MCP_SERVERS"] = json.dumps(servers)\n',
     '        if register_via_cli:  # MUTATION\n'
     '            os.environ["CUSTOM_MCP_AUTH"] = json.dumps(auth)\n'
     '            os.environ["CUSTOM_MCP_HEADERS"] = json.dumps(headers)\n'
     '            os.environ["CUSTOM_MCP_SERVERS"] = json.dumps(servers)\n'),
    ("agent_codex_vom_start_ausgeschlossen", AMAIN,
     '    if os.environ.get("CUSTOM_MCP_SERVERS"):\n        mcp_refresh_task',
     '    if mode != "codex_cli" and os.environ.get("CUSTOM_MCP_SERVERS"):  # MUTATION\n'
     '        mcp_refresh_task'),
    ("agent_start_ohne_server", AMAIN,
     '    if os.environ.get("CUSTOM_MCP_SERVERS"):\n        mcp_refresh_task',
     '    if True:  # MUTATION\n        mcp_refresh_task'),
    ("agent_codex_registriert_per_cli", AMAIN,
     'register_via_cli=(mode not in ("custom_llm", "codex_cli"))',
     'register_via_cli=(mode != "custom_llm")  # MUTATION'),
    ("gh_except_faengt_alles", GH,
     "    except (subprocess.CalledProcessError, FileNotFoundError) as e:",
     "    except Exception as e:  # MUTATION"),
    ("gh_fund_nennt_den_begriff", GH,
     '                funde.append(f"{art} #{e[\'number\']}")',
     '                funde.append(f"{art} #{e[\'number\']}: {text.splitlines()[0]}")  # MUTATION'),
    ("provider_grund_nicht_protokolliert", PROV,
     '                            "Grund im Wortlaut: %s", text[:300],\n',
     '                            "Grund liegt vor.",  # MUTATION\n'),
    ("provider_nutzer_ohne_anleitung", PROV,
     '                                 "schick die Nachricht einfach nochmal.",\n',
     '                                 "",  # MUTATION\n'),
    # --- zweite Gegenlese-Runde ---
    # Unbedingter Koeder VOR der Aenderungserkennung, die echten drei unter if.
    ("agent_koeder_unbedingt_echte_unter_cli", AMAIN, [
        ('        servers = data.get("servers") or {}\n',
         '        os.environ["CUSTOM_MCP_AUTH"] = "{}"  # MUTATION Koeder\n'
         '        servers = data.get("servers") or {}\n'),
        ('        os.environ["CUSTOM_MCP_AUTH"] = json.dumps(auth)\n'
         '        os.environ["CUSTOM_MCP_HEADERS"] = json.dumps(headers)\n'
         '        os.environ["CUSTOM_MCP_SERVERS"] = json.dumps(servers)\n',
         '        if register_via_cli:  # MUTATION\n'
         '            os.environ["CUSTOM_MCP_AUTH"] = json.dumps(auth)\n'
         '            os.environ["CUSTOM_MCP_HEADERS"] = json.dumps(headers)\n'
         '            os.environ["CUSTOM_MCP_SERVERS"] = json.dumps(servers)\n'),
    ], None),
    # Koeder-AUTH hinter SERVERS: die erste AUTH steht zwar vor SERVERS, die
    # letzte nicht — Ordnung muss fuer ALLE gelten.
    ("agent_zweite_auth_nach_servers", AMAIN,
     '        os.environ["CUSTOM_MCP_SERVERS"] = json.dumps(servers)\n',
     '        os.environ["CUSTOM_MCP_SERVERS"] = json.dumps(servers)\n'
     '        os.environ["CUSTOM_MCP_AUTH"] = json.dumps(auth)  # MUTATION\n'),
    ("gh_hinweissatz_nur_im_kommentar", GH,
     '              "Der getroffene Begriff steht hier bewusst nicht.")',
     '              )  # Der getroffene Begriff steht hier bewusst nicht. — MUTATION'),
]

# Harmlose Aenderungen (Kommentar-Einfuegungen) — hier MUSS alles gruen bleiben.
# Sonst ist die Fenster-Krankheit (a) nur verschoben: rot ohne Verhaltensaenderung.
HARMLOS = [
    ("harmlos_kommentar_mit_klammer_vor_loadHistory", CHAT,
     "    const loadHistory = async () => {",
     "    // Meta-Form: { meta?: { context_tokens?: number } } — siehe unten\n"
     "    const loadHistory = async () => {"),
    ("harmlos_langer_kommentar_im_done_zweig", WS,
     '            elif etype == "done":\n',
     '            elif etype == "done":\n'
     '                # ' + "Erlaeuterung " * 120 + '\n'),
    ("harmlos_kommentar_mit_servers_vor_auth", AMAIN,
     '        os.environ["CUSTOM_MCP_AUTH"] = json.dumps(auth)\n',
     '        # os.environ["CUSTOM_MCP_SERVERS"] = json.dumps(servers)  # NICHT zuerst\n'
     '        os.environ["CUSTOM_MCP_AUTH"] = json.dumps(auth)\n'),
    ("harmlos_kommentar_vor_return_0", GH,
     "        return 0  # kein Zugang ist kein Fund\n",
     "        # return 1 waere hier falsch\n        return 0  # kein Zugang ist kein Fund\n"),
    ("harmlos_logger_debug_im_done_zweig", WS,
     '                resp = _streaming_responses.pop(mid, {})\n',
     '                resp = _streaming_responses.pop(mid, {})\n'
     '                logger.debug("done %s", mid)\n'),
    ("harmlos_kommentar_im_beta_block", PROV,
     '        if not _CONTEXT_EDITING_AUS:\n',
     '        if not _CONTEXT_EDITING_AUS:\n'
     '            # headers["anthropic-beta"] = None  (nur ein Hinweis)\n'),
]


def _deckel():
    import resource
    resource.setrlimit(resource.RLIMIT_AS, (2 << 30, 2 << 30))


def _lauf(python: str, cwd: Path, tests: list[str]) -> tuple[bool, set[str]]:
    """(erkannt, rote Tests). Erkannt NUR bei ` failed` in der Ausgabe."""
    try:
        p = subprocess.run(
            [python, "-m", "pytest", *tests, "-q", "--tb=no", "-rf", "-p", "no:cacheprovider"],
            cwd=cwd, capture_output=True, text=True, timeout=180, preexec_fn=_deckel,
        )
    except subprocess.TimeoutExpired:
        return False, {"<HAENGT>"}
    rote = set(re.findall(r"^(?:SUB)?FAILED(?:\[.*\])? \S+::\w+::(\w+)", p.stdout, re.M))
    erkannt = " failed" in p.stdout
    if p.returncode != 0 and not erkannt:
        rote.add(f"<SAMMELFEHLER rc={p.returncode}>")
    return erkannt, rote


def laufe_tests() -> tuple[bool, set[str]]:
    e1, r1 = _lauf(PY_ORCH, ORCH, ORCH_TESTS)
    e2, r2 = _lauf(PY_AGENT, AGENT, AGENT_TESTS)
    return (e1 or e2), (r1 | r2)


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
            print(f"  {marke} {name} [{Path(rel).name}] -> {sorted(rot)}")

        print("\n--- Harmlose Aenderungen (muessen gruen bleiben) ---")
        for name, rel, suche, ersatz in HARMLOS:
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
            print(f"  {marke} {name} [{Path(rel).name}] -> {sorted(rot)}")
    finally:
        for rel, original in dateien.items():
            (REPO / rel).write_text(original)
        heil = {rel: sha(rel) == h for rel, h in hashes.items()}
        print(f"\nWiederhergestellt (SHA-256): {all(heil.values())} {heil}")

    stille = [n for n, e in ergebnis.items() if not e.get("harmlos") and not e["erkannt"]]
    fehlalarme = [n for n, e in ergebnis.items() if e.get("harmlos") and (e["erkannt"] or e["rot"])]
    echte = {n: e for n, e in ergebnis.items() if not e.get("harmlos")}
    print(f"\n{len(echte) - len(stille)}/{len(echte)} Mutationen erkannt; still: {stille}")
    print(f"{len(HARMLOS) - len(fehlalarme)}/{len(HARMLOS)} harmlose Aenderungen gruen; Fehlalarme: {fehlalarme}")
    print(json.dumps(ergebnis, indent=2, ensure_ascii=False))
    return 1 if (stille or fehlalarme) else 0


if __name__ == "__main__":
    sys.exit(main())
