"""Gemeinsame Test-Vorgaben fuer die Agenten-Laufzeit."""

import pytest


@pytest.fixture(autouse=True)
def _richtlinie_vorhanden(request, monkeypatch):
    """Die verwalteten Hook-Dateien liegen nur im Abbild (/etc, /opt/mcp). Tests,
    die einen CLI-Start nachstellen, sollen daran nicht scheitern; die Pruefung
    selbst testet ``test_richtlinie.py`` mit echten Dateien."""
    if request.node.get_closest_marker("echte_richtlinie"):
        return
    from app import richtlinie

    monkeypatch.setattr(richtlinie, "fehlt", lambda: None)


def pytest_configure(config):
    config.addinivalue_line("markers", "echte_richtlinie: prueft die echten Hook-Dateien")
