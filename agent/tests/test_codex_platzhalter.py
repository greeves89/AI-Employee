"""Codex-Zugang hinter dem Egress-Proxy, Seite des Agenten.

In der auth.json stehen Platzhalter. Die CLI darf nie selbst erneuern wollen
(``last_refresh`` vor jedem Lauf „jetzt“), die Vorprüfung darf den Platzhalter nicht
als kaputtes Token ablehnen, und er wird nie als „erneuerter Zugang“ zurückgemeldet.
"""

import asyncio
import json
from datetime import datetime, timezone
from unittest.mock import patch

import pytest

from app import codex_auth_sync as sync
from app import codex_runner

PLATZHALTER = {"auth_mode": "chatgpt", "last_refresh": "2026-01-01T00:00:00Z",
               "tokens": {"id_token": "a.b.c", "access_token": "aie-geheim-k2-0123456789ab",
                          "refresh_token": "aie-geheim-ungenutzt", "account_id": "acc"}}


@pytest.fixture
def codex_home(tmp_path, monkeypatch):
    monkeypatch.setenv("CODEX_HOME", str(tmp_path))
    return tmp_path


def _schreiben(home, daten):
    (home / "auth.json").write_text(json.dumps(daten))


def test_last_refresh_wird_vor_jedem_lauf_aufgefrischt(codex_home):
    _schreiben(codex_home, PLATZHALTER)
    sync.platzhalter_frisch_halten()
    neu = json.loads((codex_home / "auth.json").read_text())
    stand = datetime.fromisoformat(neu["last_refresh"].replace("Z", "+00:00"))
    assert (datetime.now(timezone.utc) - stand).total_seconds() < 5
    assert neu["tokens"] == PLATZHALTER["tokens"]


def test_echter_zugang_bleibt_unberuehrt(codex_home):
    echt = {**PLATZHALTER, "tokens": {**PLATZHALTER["tokens"], "access_token": "x.y.z", "refresh_token": "rt"}}
    _schreiben(codex_home, echt)
    sync.platzhalter_frisch_halten()
    assert json.loads((codex_home / "auth.json").read_text())["last_refresh"] == "2026-01-01T00:00:00Z"


def test_vorpruefung_laesst_den_platzhalter_durch(codex_home):
    _schreiben(codex_home, PLATZHALTER)
    assert codex_runner._codex_auth_problem() is None


def test_platzhalter_wird_nie_zurueckgemeldet(codex_home):
    _schreiben(codex_home, PLATZHALTER)
    sync.record_start("own")  # selbst wenn als eigener Zugang markiert
    _schreiben(codex_home, {**PLATZHALTER, "last_refresh": "2026-11-02T00:00:00Z"})
    with patch("httpx.AsyncClient") as client:
        assert asyncio.run(sync.push_if_rotated()) is False
    client.assert_not_called()
