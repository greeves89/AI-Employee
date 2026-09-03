"""Ein Projekt mit Dockerfile, aber ohne compose-Datei, darf nicht unsichtbar sein.

Gemeldet aus dem Betrieb: Ein Agent sollte ein Docker-Image bauen und scheiterte
mit "Docker-CLI nicht vorhanden". Das fehlende Kommando ist Absicht — ein
Docker-Zugang im Agenten-Behaelter waere Vollzugriff auf den Rechner. Der Weg
ueber den Orchestrator existierte auch, nur sah der Agent sein eigenes Projekt
dort nicht: Die Suche fand ausschliesslich compose-Dateien, ein Verzeichnis mit
blossem Dockerfile tauchte in `list_apps` gar nicht auf. Fuer den Agenten sah
das aus wie "kein Projekt vorhanden", also griff er zum Kommando.

Solche Projekte werden dem Agenten jetzt mit Status `needs_compose` und einem
Hinweis gemeldet. Die Verwaltungsoberflaeche bekommt sie NICHT — dort waeren es
Eintraege, die niemand starten kann.
"""

import asyncio
import unittest
from unittest.mock import MagicMock

from app.api.docker_apps import _discover_core

COMPOSE_TREFFER = "/workspace/projects/mit-compose/docker-compose.yml"
COMPOSE_INHALT = b"services:\n  web:\n    image: nginx\n"


class _Docker:
    """Antwortet auf die beiden find-Aufrufe unterschiedlich."""

    def __init__(self, compose_zeilen, dockerfile_zeilen):
        self._compose = compose_zeilen
        self._dockerfiles = dockerfile_zeilen
        self.aufrufe = []

    def exec_in_container(self, container_id, cmd):
        self.aufrufe.append(cmd)
        if "Dockerfile" in cmd:
            return (0, "\n".join(self._dockerfiles)) if self._dockerfiles else (1, "")
        return (0, "\n".join(self._compose)) if self._compose else (1, "")

    def get_file_from_container(self, container_id, path):
        return COMPOSE_INHALT

    @property
    def client(self):
        class _Containers:
            def list(self, *a, **kw):
                return []

        class _Client:
            containers = _Containers()

        return _Client()


def _agent():
    a = MagicMock()
    a.container_id = "c1"
    return a


def _lauf(compose, dockerfiles, *, fuer_agent):
    docker = _Docker(compose, dockerfiles)
    return asyncio.run(_discover_core(
        docker, _agent(), "agent-1", include_dockerfile_only=fuer_agent,
    ))


class DockerfileOhneComposeTests(unittest.TestCase):
    def test_agent_sieht_das_projekt_mit_hinweis(self):
        ergebnis = _lauf([], ["/workspace/projects/nur-dockerfile/Dockerfile"],
                         fuer_agent=True)
        pfade = {a["path"]: a for a in ergebnis["apps"]}
        self.assertIn("projects/nur-dockerfile", pfade)
        eintrag = pfade["projects/nur-dockerfile"]
        self.assertEqual("needs_compose", eintrag["status"])
        self.assertIn("docker-compose.yml", eintrag["hint"])

    def test_verwaltungsoberflaeche_sieht_es_nicht(self):
        """Sonst stuenden dort Eintraege, die sich nicht starten lassen."""
        ergebnis = _lauf([], ["/workspace/projects/nur-dockerfile/Dockerfile"],
                         fuer_agent=False)
        self.assertEqual([], ergebnis["apps"])

    def test_projekt_mit_compose_wird_nicht_doppelt_gemeldet(self):
        """Ein Dockerfile NEBEN der compose-Datei ist der Normalfall."""
        ergebnis = _lauf(
            [COMPOSE_TREFFER],
            ["/workspace/projects/mit-compose/Dockerfile"],
            fuer_agent=True,
        )
        pfade = [a["path"] for a in ergebnis["apps"]]
        self.assertEqual(["projects/mit-compose"], pfade)
        self.assertNotEqual("needs_compose", ergebnis["apps"][0]["status"])

    def test_beides_nebeneinander(self):
        ergebnis = _lauf(
            [COMPOSE_TREFFER],
            ["/workspace/projects/mit-compose/Dockerfile",
             "/workspace/projects/nur-dockerfile/Dockerfile"],
            fuer_agent=True,
        )
        nach_pfad = {a["path"]: a["status"] for a in ergebnis["apps"]}
        self.assertEqual(2, len(nach_pfad))
        self.assertEqual("needs_compose", nach_pfad["projects/nur-dockerfile"])
        self.assertNotEqual("needs_compose", nach_pfad["projects/mit-compose"])

    def test_fehlende_dockerfile_suche_kippt_die_liste_nicht(self):
        """Eine Zusatzinformation darf die Hauptauskunft nie scheitern lassen."""
        class _Kaputt(_Docker):
            def exec_in_container(self, container_id, cmd):
                if "Dockerfile" in cmd:
                    raise RuntimeError("Behaelter weg")
                return super().exec_in_container(container_id, cmd)

        docker = _Kaputt([COMPOSE_TREFFER], [])
        ergebnis = asyncio.run(_discover_core(
            docker, _agent(), "agent-1", include_dockerfile_only=True))
        self.assertEqual(["projects/mit-compose"], [a["path"] for a in ergebnis["apps"]])


class AnleitungTests(unittest.TestCase):
    def test_die_anleitung_nennt_den_ersatzweg(self):
        """Ohne diesen Hinweis greift der Agent weiter zum fehlenden Kommando."""
        from app.core.agent_manager import DEFAULT_CLAUDE_MD

        self.assertIn("rebuild_app", DEFAULT_CLAUDE_MD)
        self.assertIn("needs_compose", DEFAULT_CLAUDE_MD)
        # Der Kern: dass es KEIN docker-Kommando gibt, muss dastehen.
        self.assertRegex(DEFAULT_CLAUDE_MD, r"KEIN\s+`docker`-Kommando")


if __name__ == "__main__":
    unittest.main()
