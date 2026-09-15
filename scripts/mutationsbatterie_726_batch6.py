#!/usr/bin/env python3
"""Gegenprobe fuer Batch 6 (#726): je Verhaltensaenderung EINE Mutation.

Jede Mutation im PRODUKTIVCODE muss den ihr zugeordneten Test rot faerben; die
Kontrolle mit unveraendertem Code muss gruen sein. Dateien werden per
Hash-Vergleich wiederhergestellt (finally), damit keine Mutation liegen bleibt.
"""
import hashlib
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PY = "/workspace/.venv-orch/bin/python"

O = "orchestrator/tests/"
MUTATIONEN = [
    # (label, datei, alt, neu, suite, test)
    ("acl-praefix", "orchestrator/app/services/redis_service.py",
     'f"redis-acl:{agent_id}"', 'f"redis-acl2:{agent_id}"', "orchestrator",
     O + "test_redis_acl_survives_restart.py::DasPasswortIstAbleitbarTests::test_es_haengt_am_serverschluessel_und_der_kennung"),
    ("acl-alle-statt-fehlend", "orchestrator/app/services/scheduler_service.py",
     "fehlend = [a for a in ids if agent_acl_username(a) not in vorhanden]", "fehlend = list(ids)", "orchestrator",
     O + "test_redis_acl_survives_restart.py::AuchEinReinerRedisNeustartWirdGeheiltTests::test_er_prueft_erst_und_setzt_dann"),
    ("acl-still-bei-ok", "orchestrator/app/services/scheduler_service.py",
     "fehlend = [a for a in ids if agent_acl_username(a) not in vorhanden]", "fehlend = list(ids)", "orchestrator",
     O + "test_redis_acl_survives_restart.py::AuchEinReinerRedisNeustartWirdGeheiltTests::test_er_ist_still_wenn_alles_stimmt"),
    ("acl-jeder-takt", "orchestrator/app/services/scheduler_service.py",
     "self._letzte_acl_pruefung = jetzt", "self._letzte_acl_pruefung = 0.0", "orchestrator",
     O + "test_redis_acl_survives_restart.py::AuchEinReinerRedisNeustartWirdGeheiltTests::test_er_laeuft_nicht_bei_jedem_takt"),
    ("acl-schalter-ignoriert", "orchestrator/app/services/scheduler_service.py",
     "if not settings.redis_acl_enabled or not self.redis", "if not self.redis", "orchestrator",
     O + "test_redis_acl_survives_restart.py::AuchEinReinerRedisNeustartWirdGeheiltTests::test_ohne_eingeschaltete_acl_tut_er_nichts"),
    ("acl-fehler-setzt-blind", "orchestrator/app/services/scheduler_service.py",
     'logger.debug("[Scheduler] ACL-Liste nicht lesbar: %s", e)\n            return',
     'logger.debug("[Scheduler] ACL-Liste nicht lesbar: %s", e)\n            vorhanden = set()', "orchestrator",
     O + "test_redis_acl_survives_restart.py::AuchEinReinerRedisNeustartWirdGeheiltTests::test_eine_unlesbare_liste_setzt_nichts_blind"),
    ("acl-start-nicht-alle", "orchestrator/app/main.py",
     "_sel_acl(_AgentACL.id))).scalars()", "_sel_acl(_AgentACL.id_))).scalars()", "orchestrator",
     O + "test_redis_acl_survives_restart.py::DieAclWirdBeimStartWiederhergestelltTests::test_er_gilt_fuer_ALLE_agenten"),
    ("secret-in-antwort", "orchestrator/app/api/my_ai_credentials.py",
     '"last_status": row.last_status,', '"secret": row.secret_encrypted, "last_status": row.last_status,', "orchestrator",
     O + "test_my_ai_credentials_ui.py::TheSecretIsNeverShownTests::test_the_api_returns_everything_except_the_secret"),
    ("login-fremder-nutzer", "orchestrator/app/services/codex_device_auth_service.py",
     "UserAiCredential(user_id=session.for_user_id", "UserAiCredential(user_id=None", "orchestrator",
     O + "test_my_ai_credentials_ui.py::TheCodexLoginCompletesByItselfTests::test_a_personal_login_lands_in_the_personal_store"),
    ("sync-im-personal-zweig", "orchestrator/app/services/codex_device_auth_service.py",
     "                    session.account_label = row.label\n",
     "                    session.account_label = row.label\n                await CodexAuthService().sync_auth_json()\n", "orchestrator",
     O + "test_my_ai_credentials_ui.py::TheCodexLoginCompletesByItselfTests::test_the_shared_file_stays_platform_only"),
    ("upsert-unbewacht", "orchestrator/app/api/my_ai_credentials.py",
     None, None, "orchestrator",
     O + "test_personal_credentials_are_governable.py::TheApiRefusesToStoreSomethingUselessTests::test_every_creating_endpoint_is_guarded"),
    ("delete-bewacht", "orchestrator/app/api/my_ai_credentials.py",
     None, None, "orchestrator",
     O + "test_personal_credentials_are_governable.py::ReadingAndDeletingStayOpenTests::test_deleting_is_not_guarded"),
    ("meldung-anderer-chat", "orchestrator/app/api/agents.py",
     'session_id="meldungen"', 'session_id="notizen"', "orchestrator",
     O + "test_telegram_chat_hijack.py::NoBorrowingAtAllTests::test_the_message_is_still_delivered_via_the_chat"),
    ("lead-nicht-genannt", "orchestrator/app/api/agents.py",
     "lead_id = await team_lead_for(_db, agent_id)", "lead_id = None", "orchestrator",
     O + "test_telegram_chat_hijack.py::TeamLeadIsTheWayTests::test_the_answer_names_the_actual_lead"),
    ("py-warnung-weg", "agent/app/tools/definitions.py",
     'OWN /workspace and cannot see "', 'OWN /workspace and cannot spy "', "orchestrator",
     O + "test_workspace_is_private.py::TheToolItselfWarnsTests::test_custom_llm_definition_warns"),
    ("mcp-warnung-weg", "agent/mcp/orchestrator-server.mjs",
     "cannot see yours", "cannot see mine", "orchestrator",
     O + "test_workspace_is_private.py::TheToolItselfWarnsTests::test_the_mcp_server_warns_identically"),
    ("refresh-nach-stop", "orchestrator/app/core/agent_manager.py",
     None, None, "agent",
     "tests/test_auth_rotation_retry.py::RecreateOrderTests::test_update_agent_refreshes_before_it_recreates"),
]


def sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def pytest(suite: str, test: str) -> int:
    pfad = test if suite == "agent" else test.replace("orchestrator/", "", 1)
    r = subprocess.run([PY, "-m", "pytest", "-q", "-x", "-p", "no:cacheprovider", pfad],
                       cwd=ROOT / suite, capture_output=True, text=True, timeout=300)
    return r.returncode


def sonderfall(label: str, text: str) -> str:
    """Mutationen, die nicht als einfache Ersetzung ausdrueckbar sind."""
    if label == "upsert-unbewacht":
        kopf = text.index("async def upsert_my_credential(")
        stelle = text.index("_eigene_zugaenge_erlaubt(user)", kopf)
        return text[:stelle] + "pass  # " + text[stelle:]
    if label == "delete-bewacht":
        kopf = text.index("async def delete_my_credential(")
        doppelpunkt = text.index("):\n", kopf) + 3
        return text[:doppelpunkt] + "    _eigene_zugaenge_erlaubt(user)\n" + text[doppelpunkt:]
    if label == "refresh-nach-stop":
        kopf = text.index("async def update_agent(")
        a = text.index("refresh_access_token", kopf)
        return text[:a] + "renew_token" + text[a + len("refresh_access_token"):]
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
                assert alt in text, f"Anker fehlt fuer {label}: {alt!r}"
                mut = text.replace(alt, neu, 1)
            assert mut != text
            p.write_text(mut)
            try:
                rc = pytest(suite, test)
            finally:
                p.write_bytes(orig[p])
            ergebnis.append((label, rc != 0))
            print(f"  {'ROT (erwartet)' if rc != 0 else 'STILL -- Test sieht die Mutation NICHT'} {label}")
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
