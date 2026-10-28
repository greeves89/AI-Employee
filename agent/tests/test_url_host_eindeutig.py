"""URL-Freigabeliste des Agenten: mehrdeutige Adressen kommen nicht durch.

``https://evil.com\\.example.com/`` ist für ``urlparse`` ein Host unter
``example.com``, für Chromium ``evil.com`` — Gegenstück zum gleichnamigen Test
im Orchestrator.
"""

import asyncio
import time

from app.tools import executor
from app.tools.executor import ToolExecutor, _eindeutiger_host

MEHRDEUTIG = (
    "https://evil.com\\.example.com/",
    "https://evil.com\\@example.com/",
    "https://evil.com%2f.example.com/",
    "https://evil.com\t.example.com/",
)


def _pruefen(url, liste):
    ex = ToolExecutor.__new__(ToolExecutor)
    ex._url_allowlist_cache = (liste, time.time() + 60)
    return asyncio.run(ex._check_url_allowlist(url))


def test_host():
    assert _eindeutiger_host("https://A.example.com/x") == "a.example.com"
    for url in MEHRDEUTIG:
        assert _eindeutiger_host(url) is None, url


def test_freigabeliste_lehnt_mehrdeutige_adressen_ab():
    for url in MEHRDEUTIG:
        assert _pruefen(url, ["*.example.com", "example.com"]) is not None, url
    assert _pruefen("https://a.example.com/", ["*.example.com"]) is None
    assert _pruefen("https://example.com/", ["example.com"]) is None


def test_leere_eintraege_lassen_nichts_durch():
    assert _pruefen("https://evil.com\\.example.com/", [""]) is not None
    assert _pruefen("https://example.com/", [""]) is not None
    assert _pruefen("https://evil.com\\.example.com/", ["*"]) is None  # "*" heißt alles


def test_modul_hat_den_helfer():
    assert executor._eindeutiger_host is _eindeutiger_host
