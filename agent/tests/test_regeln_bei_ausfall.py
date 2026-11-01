"""Ist der Orchestrator nicht erreichbar, gilt der letzte bekannte Stand — sonst gesperrt (P1-8).

Bis 1.421 liessen Freigabeliste und Befehlsregeln bei jedem Abruffehler ALLES
durch; ein Fehlschlag wurde sogar 10 s lang als "keine Regeln" gemerkt. Wer den
Orchestrator fuer den Agenten unerreichbar machte, war jede Pruefung los.
"""

import asyncio
from unittest import mock

import pytest

from app.tools import executor, pretooluse_hook


@pytest.fixture(autouse=True)
def _frisch():
    for f in (executor._get_allowed_categories, executor._get_command_policies):
        f._cache = (None if f is executor._get_allowed_categories else [], 0.0)
        f._bekannt = (executor._UNBEKANNT, 0.0)
    yield


class _Antwort:
    def __init__(self, daten):
        self._daten = daten

    def read(self):
        import json
        return json.dumps(self._daten).encode()

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _orchestrator(regeln):
    return mock.patch("urllib.request.urlopen", return_value=_Antwort({"rules": regeln}))


_AUSFALL = mock.patch("urllib.request.urlopen", side_effect=OSError("Verbindung abgelehnt"))


def test_freigabeliste_ohne_bekannten_stand_sperrt():
    with _AUSFALL:
        assert executor._get_allowed_categories() is executor.KEIN_STAND
        bash = pretooluse_hook.decide("Bash", {"command": "ls"})
        unbekannt = pretooluse_hook.decide("mcp__fremd__irgendwas", {})
        lesen = pretooluse_hook.decide("Read", {"file_path": "/workspace/x"})
    assert bash["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert "nicht abrufbar" in bash["hookSpecificOutput"]["permissionDecisionReason"]
    assert unbekannt["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert lesen["hookSpecificOutput"]["permissionDecision"] == "allow", "Lesen bleibt moeglich"


def test_freigabeliste_letzter_stand_gilt_beim_ausfall():
    with _orchestrator([{"name": "Shell", "category": "shell_exec"}]):
        assert executor._get_allowed_categories() == {"shell_exec"}
    executor._get_allowed_categories._cache = (None, 0.0)  # Cache abgelaufen
    with _AUSFALL:
        assert executor._get_allowed_categories() == {"shell_exec"}
        assert pretooluse_hook.decide("Bash", {"command": "ls"})["hookSpecificOutput"]["permissionDecision"] == "allow"
        assert pretooluse_hook.decide("Write", {"file_path": "/workspace/x"})["hookSpecificOutput"][
            "permissionDecision"] == "deny"


def test_freigabeliste_l4_bleibt_l4_beim_ausfall():
    with _orchestrator([{"name": "Alles erlaubt", "category": "shell_exec"}]):
        assert executor._get_allowed_categories() is None
    executor._get_allowed_categories._cache = (None, 0.0)
    with _AUSFALL:
        assert executor._get_allowed_categories() is None


def test_freigabeliste_zu_alter_stand_sperrt():
    with _orchestrator([{"name": "Shell", "category": "shell_exec"}]):
        executor._get_allowed_categories()
    wert, zeit = executor._get_allowed_categories._bekannt
    executor._get_allowed_categories._bekannt = (wert, zeit - executor.LETZTER_STAND_HOECHSTENS - 1)
    executor._get_allowed_categories._cache = (None, 0.0)
    with _AUSFALL:
        assert executor._get_allowed_categories() is executor.KEIN_STAND


class _Client:
    def __init__(self, antwort=None, fehler=None):
        self._antwort, self._fehler = antwort, fehler

    def __call__(self, *a, **k):
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def get(self, *a, **k):
        if self._fehler:
            raise self._fehler
        return self._antwort


class _Http:
    def __init__(self, daten):
        self._daten = daten

    def raise_for_status(self):
        return None

    def json(self):
        return self._daten


def _regeln(client):
    return mock.patch("httpx.AsyncClient", client)


def test_befehlsregeln_ohne_bekannten_stand_sperren_jeden_befehl():
    with _regeln(_Client(fehler=OSError("weg"))):
        wirkung, grund = asyncio.run(executor._evaluate_command_policy("ls -la"))
    assert wirkung == "blocked"
    assert "nicht abrufbar" in grund


def test_befehlsregeln_fehlschlag_wird_kurz_als_sperre_gemerkt():
    """Nicht als "keine Regeln" (bis 1.421: 10 s alles frei), sondern als Sperre —
    und nur kurz, damit im Ausfall nicht jeder Befehl erneut auf die Zeitgrenze wartet."""
    with _regeln(_Client(fehler=OSError("weg"))):
        asyncio.run(executor._get_command_policies())
    with _regeln(_Client(_Http({"policies": []}))):
        assert asyncio.run(executor._get_command_policies()) is executor.KEINE_BEFEHLSREGELN
        executor._get_command_policies._cache = (executor._get_command_policies._cache[0], 0.0)
        assert asyncio.run(executor._get_command_policies()) == [], "danach fragt er wieder nach"


def test_absage_des_orchestrators_hebt_den_alten_stand_auf():
    """401/403/404 (Token widerrufen, Agent geloescht) ist kein Ausfall."""
    import urllib.error

    with _orchestrator([{"name": "Shell", "category": "shell_exec"}]):
        executor._get_allowed_categories()
    executor._get_allowed_categories._cache = (None, 0.0)
    absage = urllib.error.HTTPError("u", 403, "Forbidden", {}, None)
    with mock.patch("urllib.request.urlopen", side_effect=absage):
        assert executor._get_allowed_categories() is executor.KEIN_STAND
    import httpx

    regel = {"pattern": "rm", "effect": "blocked"}
    with _regeln(_Client(_Http({"policies": [regel]}))):
        asyncio.run(executor._get_command_policies())
    executor._get_command_policies._cache = ([], 0.0)
    antwort = httpx.Response(401, request=httpx.Request("GET", "http://o"))
    with _regeln(_Client(fehler=httpx.HTTPStatusError("401", request=antwort.request, response=antwort))):
        assert asyncio.run(executor._get_command_policies()) is executor.KEINE_BEFEHLSREGELN


def test_kein_stand_ist_ein_eigenes_objekt():
    assert executor.KEIN_STAND is not frozenset()
    assert len(executor.KEIN_STAND) == 0


def test_befehlsregeln_letzter_stand_gilt_beim_ausfall():
    regel = {"pattern": "rm -rf", "effect": "blocked", "name": "Kein rm"}
    with _regeln(_Client(_Http({"policies": [regel]}))):
        asyncio.run(executor._get_command_policies())
    executor._get_command_policies._cache = ([], 0.0)
    with _regeln(_Client(fehler=OSError("weg"))):
        assert asyncio.run(executor._evaluate_command_policy("ls")) == (None, None)
        assert asyncio.run(executor._evaluate_command_policy("rm -rf /"))[0] == "blocked"
