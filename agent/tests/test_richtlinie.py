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
