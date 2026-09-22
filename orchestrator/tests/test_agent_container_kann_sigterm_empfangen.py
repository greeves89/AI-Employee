"""Issue #835: Agenten-Container endeten bei jedem Stopp mit Exit 1 und
``[FATAL tini (7)] Unexpected error when forwarding signal: 'Operation not
permitted'`` als letzter Logzeile -- der Agent bekam SIGTERM nie zu sehen und
hatte keine Gelegenheit, laufende Arbeit zu sichern.

Die Signalkette hat ZWEI Init-Schichten, und nur die zweite ist das Problem:

1. ``create_container`` setzt ``init=True`` -- Dockers eigenes ``docker-init``
   ist PID 1.
2. ``agent/Dockerfile``: ``ENTRYPOINT ["tini", "--", "/entrypoint.sh"]`` ist
   die Schicht DARUNTER. Deshalb meldet sich tini im Fehlerfall als PID 7,
   nicht als PID 1 -- genau so steht es auch in der Logzeile des Issues.
   PID 1 -> tini ist harmlos, beide laufen als root.
3. ``agent/entrypoint.sh``: schliesst mit ``exec gosu agent "$@"`` -- der
   Agentenprozess laeuft unter einer ANDEREN UID als tini. DIESE Weitergabe
   ueberschreitet die UID-Grenze.
4. ``docker_service.create_container``: ``cap_drop=["ALL"]``.

Ein Signal ueber die UID-Grenze verlangt CAP_KILL. Mit cap_drop=ALL fehlt sie
auch root, deshalb scheitert ``kill()`` mit EPERM, tini bricht ab, und der
Container endet mit Exit 1 statt mit 143.

Die ersten drei Tests pruefen das VERHALTEN der Erzeugung (welche Capabilities
gehen wirklich an den Daemon). Die uebrigen sichern die Annahmen, auf denen
die Begruendung ruht -- denn faellt eine davon weg, schuetzt die Capability
etwas, das es nicht mehr gibt, und niemand merkt es.

Die Annahmen-Tests werten den Dockerfile-/Skript-Text ABSICHTLICH nicht per
blosser Teilstring-Suche aus: ein spaeteres ``ENTRYPOINT`` gewinnt gegen ein
frueheres, und eine auskommentierte Zeile ist keine Zusicherung. Beides wird
hier ausgefiltert.
"""
from pathlib import Path

from app.services.docker_service import DockerService

_REPO = Path(__file__).resolve().parents[2]


class _FakeContainersApi:
    def __init__(self):
        self.run_calls = []

    def run(self, **kwargs):
        self.run_calls.append(kwargs)
        return object()


class _FakeVolumesApi:
    def get(self, name):
        return object()

    def create(self, name):
        return object()


class _FakeClient:
    def __init__(self):
        self.containers = _FakeContainersApi()
        self.volumes = _FakeVolumesApi()


def _run_kwargs(**overrides):
    svc = DockerService.__new__(DockerService)  # __init__ braucht einen echten Daemon
    svc.client = _FakeClient()
    params = dict(
        image="ai-employee-agent:latest",
        name="agent-a1b2c3d4",
        environment={"AGENT_ID": "a1b2c3d4"},
        volume_name="workspace-a1b2c3d4",
        network="ai-employee-net",
    )
    params.update(overrides)
    svc.create_container(**params)
    return svc.client.containers.run_calls[0]


def test_der_container_darf_signale_an_fremde_uid_senden():
    # Das ist der Fix: ohne KILL kann tini SIGTERM nicht weiterreichen.
    assert "KILL" in _run_kwargs()["cap_add"]


def test_die_uebrige_haertung_bleibt_unangetastet():
    # Gegenprobe zum Fix: er darf den Rechte-Entzug nicht aufweichen.
    kwargs = _run_kwargs()
    assert kwargs["cap_drop"] == ["ALL"]
    # Keine zusaetzlichen Rechte ausser den vorher schon noetigen + KILL.
    assert set(kwargs["cap_add"]) == {
        "CHOWN", "SETUID", "SETGID", "DAC_OVERRIDE", "FOWNER", "KILL",
    }
    assert kwargs["pids_limit"] == 512


def test_auch_der_sudo_faehige_container_bekommt_kill():
    # needs_sudo schaltet no-new-privileges ab; die Capability-Liste ist
    # davon unabhaengig und muss KILL trotzdem enthalten.
    assert "KILL" in _run_kwargs(needs_sudo=True)["cap_add"]


def _wirksame_zeilen(text: str) -> list[str]:
    """Zeilen ohne Kommentare und Leerzeilen.

    Eine auskommentierte Zeile ist keine Zusicherung -- ohne diesen Filter
    wuerde ``# frueher: exec gosu agent "$@"`` den Test gruen halten, obwohl
    der UID-Wechsel laengst weg ist.
    """
    zeilen = []
    for zeile in text.splitlines():
        ohne = zeile.strip()
        if not ohne or ohne.startswith("#"):
            continue
        # Auch ANGEHAENGTE Kommentare abschneiden: sonst haelt
        # `exec "$@"  # frueher: exec gosu agent "$@"` den Test gruen.
        # Der Schnitt ist bewusst grob -- schneidet er zu viel weg, faellt der
        # Test rot aus, nicht still gruen.
        ohne = ohne.split(" #", 1)[0].rstrip()
        if not ohne:
            continue
        zeilen.append(ohne)
    return zeilen


def _letzte_direktive(dockerfile: str, name: str) -> str | None:
    """Die WIRKSAME Direktive: bei mehreren gewinnt in Docker die letzte."""
    treffer = [z for z in _wirksame_zeilen(dockerfile) if z.startswith(name + " ") or z.startswith(name + "[")]
    return treffer[-1] if treffer else None


def test_die_annahme_tini_signalisiert_den_agenten_gilt_noch():
    dockerfile = (_REPO / "agent" / "Dockerfile").read_text(encoding="utf-8")
    entrypoint = _letzte_direktive(dockerfile, "ENTRYPOINT")
    assert entrypoint is not None, "Das Image hat gar kein ENTRYPOINT mehr."
    assert "tini" in entrypoint, (
        f"Die wirksame (letzte) ENTRYPOINT-Zeile lautet {entrypoint!r} und "
        "startet kein tini mehr. Dann reicht nicht mehr tini das Signal ueber "
        "die UID-Grenze weiter, und die Begruendung fuer CAP_KILL gehoert neu "
        "bewertet."
    )


def test_die_annahme_tini_laeuft_als_root_gilt_noch():
    # Ein USER-Statement wuerde tini als NICHT-root starten. Dann waere
    # CAP_KILL gegenstandslos (und gosu ohnehin kaputt) -- der Fix wuerde ein
    # Problem loesen, das es nicht mehr gibt, ohne dass es jemand merkt.
    dockerfile = (_REPO / "agent" / "Dockerfile").read_text(encoding="utf-8")
    user = _letzte_direktive(dockerfile, "USER")
    assert user is None, (
        f"Das Dockerfile setzt {user!r}. Laeuft tini nicht mehr als root, "
        "traegt die Begruendung fuer CAP_KILL nicht mehr."
    )


def test_die_annahme_uid_wechsel_per_gosu_gilt_noch():
    entrypoint = (_REPO / "agent" / "entrypoint.sh").read_text(encoding="utf-8")
    wirksam = _wirksame_zeilen(entrypoint)
    assert any("exec gosu agent" in z for z in wirksam), (
        "Keine WIRKSAME (nicht auskommentierte) Zeile wechselt mehr per gosu "
        "den Nutzer. Laeuft der Agent unter derselben UID wie tini, ist "
        "CAP_KILL ueberfluessig und sollte wieder entfallen."
    )
