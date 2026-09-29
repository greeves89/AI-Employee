"""Welcher Container ist der Web-Einstieg einer App?

„Öffnen" und der Freigabe-Link zeigten bis 29.09.2026 auf den ERSTEN laufenden
Container mit irgendeinem Port. Bei einer App aus Worker, API, Datenbank, Web,
Mail und nginx war das der Worker (Port 8000, keine Oberfläche) — der geteilte
Link lieferte „Bad Gateway", während der eigene Link über nginx funktionierte.
"""
from types import SimpleNamespace

from app.api.apps_overview import _web_einstieg


def _container(dienst, exposed, veroeffentlicht=(), status="running"):
    ports = {f"{p}/tcp": None for p in exposed}
    for p in veroeffentlicht:
        ports[f"{p}/tcp"] = [{"HostIp": "0.0.0.0", "HostPort": "40505"}]
    return SimpleNamespace(
        name=f"agent-2ad91565-projects-aios-quick-eva-{dienst}-1",
        status=status,
        labels={"com.docker.compose.service": dienst},
        attrs={
            "Config": {"ExposedPorts": {f"{p}/tcp": {} for p in exposed}},
            "NetworkSettings": {"Ports": ports},
        },
    )


def _name(ergebnis):
    c, port = ergebnis
    return (c.labels["com.docker.compose.service"] if c else None), port


def test_nginx_mit_veroeffentlichtem_port_schlaegt_den_worker_davor():
    # Reihenfolge wie Docker sie lieferte: der Worker zuerst.
    apps = [
        _container("worker", ["8000"]),
        _container("api", ["8000"]),
        _container("db", ["5432"]),
        _container("web", ["3000"]),
        _container("mailpit", ["1025", "8025"]),
        _container("nginx", ["80"], veroeffentlicht=["80"]),
    ]
    assert _name(_web_einstieg(apps)) == ("nginx", "80")


def test_ohne_veroeffentlichten_port_gewinnt_der_web_dienst():
    apps = [_container("worker", ["8000"]), _container("db", ["5432"]), _container("frontend", ["3000"])]
    assert _name(_web_einstieg(apps)) == ("frontend", "3000")


def test_datenbank_worker_und_mail_nie_wenn_es_etwas_anderes_gibt():
    apps = [_container("db", ["5432"]), _container("worker", ["8000"]), _container("server", ["8080"])]
    assert _name(_web_einstieg(apps)) == ("server", "8080")


def test_teilstring_zaehlt_nicht_als_web_name():
    # „quick" enthält „ui", „build" auch — beides sind keine Web-Dienste.
    apps = [_container("quickbuild", ["9000"]), _container("site", ["9000"])]
    assert _name(_web_einstieg(apps)) == ("site", "9000")


def test_gestoppte_und_portlose_container_zaehlen_nicht():
    apps = [
        _container("nginx", ["80"], veroeffentlicht=["80"], status="exited"),
        _container("cron", []),
        _container("api", ["8000"]),
    ]
    assert _name(_web_einstieg(apps)) == ("api", "8000")


def test_einzelner_container_bleibt_der_einstieg():
    assert _name(_web_einstieg([_container("db", ["5432"])])) == ("db", "5432")


def test_nichts_laeuft():
    assert _web_einstieg([]) == (None, None)
