#!/usr/bin/env python3
"""Gegenprobe fuer Batch 5 (#726): je Verhaltensaenderung EINE Mutation.

Jede Mutation muss GENAU den ihr zugeordneten Test roct faerben -- kein Test
darf bei ALLEN Mutationen still bleiben (er saehe dann nichts zu), und keine
zwei Mutationen duerfen exakt dieselbe Testmenge faerben (sie wuerden dann
nichts trennen). Dateien werden per Hash-Vergleich wiederhergestellt.
"""
import hashlib
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PYTEST = "/workspace/.venv-orch/bin/python"

FILES = {
    "view": ROOT / "frontend/src/app/settings/view.tsx",
    "prompt": ROOT / "frontend/src/components/agents/approval-prompt.tsx",
    "seite": ROOT / "frontend/src/app/approvals/page.tsx",
    "api": ROOT / "frontend/src/lib/api.ts",
    "mcp": ROOT / "agent/mcp/notification-server.mjs",
}


def sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


ORIG_HASH = {k: sha(v) for k, v in FILES.items()}


def run_pytest(testpaths: list[str]) -> tuple[int, str]:
    r = subprocess.run(
        [PYTEST, "-m", "pytest", "-q", *testpaths],
        cwd=ROOT / "orchestrator", capture_output=True, text=True, timeout=120,
    )
    return r.returncode, r.stdout + r.stderr


TESTS = [
    "tests/test_member_sees_only_own_things.py::EmptyTabsAreHiddenTests::test_voice_and_system_are_admin_only",
    "tests/test_approval_answer_reaches_the_agent.py::TheUiCanActuallyAnswerTests::test_the_options_are_buttons",
    "tests/test_approval_answer_reaches_the_agent.py::TheUiCanActuallyAnswerTests::test_the_list_renders_them_as_buttons_too",
    "tests/test_approval_answer_reaches_the_agent.py::TheUiCanActuallyAnswerTests::test_the_answer_is_sent_to_the_server",
    "tests/test_approval_answer_reaches_the_agent.py::TheUiCanActuallyAnswerTests::test_the_mcp_runtime_passes_the_answer_to_the_agent",
]


def kontrolle():
    print("=== KONTROLLE: unveraenderte Vorlage, alle 5 muessen gruen sein ===")
    rc, out = run_pytest(TESTS)
    print(out[-1500:])
    assert rc == 0, "Kontrolle bereits rot -- Testbau selbst kaputt"
    print("OK: Kontrolle gruen.\n")


def mutieren(label, path: Path, alt: str, neu: str):
    text = path.read_text()
    assert alt in text, f"Mutationsanker fehlt in {path}: {alt!r}"
    assert text.count(alt) == 1, f"Anker nicht eindeutig in {path}: {alt!r}"
    path.write_text(text.replace(alt, neu, 1))
    print(f"--- Mutation [{label}] gesetzt in {path.name} ---")
    rc, out = run_pytest(TESTS)
    faerbt = []
    for line in out.splitlines():
        if "FAILED" in line:
            faerbt.append(line.strip())
    print(f"rc={rc}  faerbt: {faerbt if faerbt else '(nichts!)'}")
    # wiederherstellen
    path.write_text(text)
    assert sha(path) == ORIG_HASH[[k for k, v in FILES.items() if v == path][0]], \
        f"Wiederherstellung fehlgeschlagen fuer {path}"
    return faerbt


def main():
    kontrolle()

    ergebnisse = {}

    # 1) Voice/System aus der isAdmin-Klammer HERAUS in den immer sichtbaren Teil ziehen.
    ergebnisse["voice_ausserhalb_admin"] = mutieren(
        "voice_ausserhalb_admin", FILES["view"],
        '{ id: "meine" as const, label: "Meine KI-Zugänge", icon: KeyRound },',
        '{ id: "meine" as const, label: "Meine KI-Zugänge", icon: KeyRound },\n'
        '            { id: "voice" as const, label: "Voice", icon: Mic },',
    )

    # 2) Der antworten(opt)-Aufruf im Options-Button verschwindet (Klick tut nichts mehr).
    ergebnisse["prompt_onclick_entfernt"] = mutieren(
        "prompt_onclick_entfernt", FILES["prompt"],
        "onClick={() => antworten(opt)}",
        "onClick={() => {}}",
    )

    # 3) Der handleAnswerInline-Aufruf in der Listenansicht verschwindet.
    ergebnisse["seite_handleanswer_entfernt"] = mutieren(
        "seite_handleanswer_entfernt", FILES["seite"],
        "handleAnswerInline(approval.approval_id, opt);",
        "/* handleAnswerInline entfernt */",
    )

    # 4) api.ts: answer wird nicht mehr an den Server geschickt.
    ergebnisse["api_answer_entfernt"] = mutieren(
        "api_answer_entfernt", FILES["api"],
        'body: JSON.stringify({ answer: answer || null }),',
        'body: JSON.stringify({}),',
    )

    # 5) notification-server.mjs: user_response wird nicht mehr an den Agenten gereicht.
    ergebnisse["mcp_user_response_entfernt"] = mutieren(
        "mcp_user_response_entfernt", FILES["mcp"],
        'const antwort = (decision.user_response || "").trim();',
        'const antwort = "";',
    )

    print("\n=== ZUSAMMENFASSUNG ===")
    for label, faerbt in ergebnisse.items():
        print(f"{label}: {len(faerbt)} Test(s) rot")

    # Trennschaerfe: jede Mutation muss mindestens einen Test faerben, und
    # keine zwei Mutationen duerfen exakt dieselbe Menge faerben.
    signaturen = [tuple(sorted(v)) for v in ergebnisse.values()]
    leer = [k for k, v in ergebnisse.items() if not v]
    dubletten = len(signaturen) != len(set(signaturen))
    print(f"\nLeere (kein Test reagiert): {leer}")
    print(f"Signaturen paarweise verschieden: {not dubletten}")

    for k in FILES:
        assert sha(FILES[k]) == ORIG_HASH[k], f"{k} nicht sauber wiederhergestellt!"
    print("\nAlle Dateien per Hash wiederhergestellt.")

    if leer or dubletten:
        sys.exit(1)


if __name__ == "__main__":
    main()
