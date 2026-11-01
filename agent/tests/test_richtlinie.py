"""Ohne wirksamen Pruef-Hook startet keine CLI (P1-8).

Claude Code und Codex laufen ohne eigene Rueckfragen; die einzige Pruefung ist
der verwaltete PreToolUse-Hook. Fehlt seine Richtlinie im Abbild, ist sie
beschreibbar oder traegt sie den Hook nicht ein, wird der Lauf abgelehnt.
"""

import asyncio
import os

import pytest

from app import richtlinie

pytestmark = pytest.mark.echte_richtlinie


@pytest.fixture
def abbild(tmp_path, monkeypatch):
    hook = tmp_path / "pretooluse-hook.mjs"
    hook.write_text("// hook")
    dateien = []
    for name in ("managed-settings.json", "requirements.toml"):
        datei = tmp_path / name
        datei.write_text(f'command = "/usr/bin/node {hook}"')
        datei.chmod(0o644)
        dateien.append(datei)
    monkeypatch.setattr(richtlinie, "HOOK", str(hook))
    monkeypatch.setattr(richtlinie, "DATEIEN", tuple(str(d) for d in dateien))
    monkeypatch.setattr(richtlinie, "EIGENTUEMER", os.getuid())
    tmp_path.chmod(0o755)
    return hook, dateien


def test_vollstaendig(abbild):
    assert richtlinie.fehlt() is None
    richtlinie.sicherstellen()


def test_hook_skript_fehlt(abbild):
    abbild[0].unlink()
    assert "fehlt" in richtlinie.fehlt()


def test_richtlinie_fehlt(abbild):
    abbild[1][1].unlink()
    assert "requirements.toml fehlt" in richtlinie.fehlt()


def test_richtlinie_beschreibbar(abbild):
    abbild[1][0].chmod(0o666)
    assert "nicht schreibgeschützt" in richtlinie.fehlt()


def test_falscher_eigentuemer(abbild, monkeypatch):
    monkeypatch.setattr(richtlinie, "EIGENTUEMER", os.getuid() + 1)
    assert "nicht schreibgeschützt" in richtlinie.fehlt()


def test_hook_skript_beschreibbar(abbild):
    abbild[0].chmod(0o666)
    assert "nicht schreibgeschützt" in richtlinie.fehlt()


def test_ordner_beschreibbar(abbild):
    ordner = abbild[0].parent
    ordner.chmod(0o777)
    try:
        assert "nicht schreibgeschützt" in richtlinie.fehlt()
    finally:
        ordner.chmod(0o755)


def test_symlink_zaehlt_nicht(abbild, tmp_path):
    ziel = tmp_path / "anderswo.toml"
    ziel.write_text(abbild[1][1].read_text())
    abbild[1][1].unlink()
    abbild[1][1].symlink_to(ziel)
    assert "nicht schreibgeschützt" in richtlinie.fehlt()


def test_hook_nicht_eingetragen(abbild):
    abbild[1][0].write_text("{}")
    assert "trägt den Prüf-Hook nicht ein" in richtlinie.fehlt()
    with pytest.raises(RuntimeError, match="Lauf aus Sicherheitsgründen abgelehnt"):
        richtlinie.sicherstellen()


def test_chat_startet_die_cli_nicht(abbild, monkeypatch):
    """Ein echter Chat-Zug: Fehler an den Nutzer, kein Prozess."""
    from app import chat_handler as ch

    abbild[1][0].unlink()
    gestartet = []

    async def _exec(*cmd, **kw):
        gestartet.append(cmd)
        raise AssertionError("CLI darf nicht starten")

    class _Publisher:
        def __init__(self):
            self.ereignisse = []

        async def publish_chat(self, mid, art, daten):
            self.ereignisse.append((art, daten))

    monkeypatch.setattr(asyncio, "create_subprocess_exec", _exec)
    pub = _Publisher()
    ergebnis = asyncio.run(ch.ChatHandler(pub)._execute_cli("m1", "x", "claude-sonnet-5"))
    assert gestartet == []
    assert ergebnis["status"] == "error"
    assert "Prüf-Hook nicht aktiv" in ergebnis["error"]
    assert any(art == "error" and "Prüf-Hook" in d["message"] for art, d in pub.ereignisse)


def test_abbild_traegt_den_hook_ein():
    """Die Dateien im Repo, die ins Abbild kopiert werden, tragen genau dieses Skript ein."""
    from pathlib import Path

    basis = Path(__file__).resolve().parents[1] / "richtlinie"
    for name in ("managed-settings.json", "requirements.toml"):
        assert richtlinie.HOOK in (basis / name).read_text(), name


@pytest.mark.skipif(not os.path.exists("/proc/self/mem"), reason="nur unter Linux")
def test_pruefserver_ist_fuer_die_eigene_shell_unzugaenglich():
    """Ein Prozess derselben Kennung (wie die Shell des Agenten) kommt nach dem
    Abschirmen nicht mehr an den Speicher — vorher schon."""
    import subprocess
    import sys
    import time

    if os.geteuid() == 0:
        pytest.skip("root hat CAP_SYS_PTRACE und darf ohnehin alles")
    kind = subprocess.Popen(
        [sys.executable, "-c",
         "import sys, time; sys.path.insert(0, sys.argv[1]);"
         "from app.richtlinie import pruefserver_abschirmen;"
         "print(pruefserver_abschirmen(), flush=True); time.sleep(30)",
         os.path.dirname(os.path.dirname(os.path.abspath(__file__)))],
        stdout=subprocess.PIPE, text=True)
    try:
        assert kind.stdout.readline().strip() == "True"
        time.sleep(0.1)
        with pytest.raises(PermissionError):
            open(f"/proc/{kind.pid}/mem", "rb")
    finally:
        kind.kill()
        kind.wait()
