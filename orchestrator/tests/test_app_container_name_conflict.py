"""Eine App muss neu baubar bleiben, wenn ein alter Recreate haengengeblieben ist.

Betreiberbericht vom 23.08.2026 (Issue #644): der Rebuild einer Docker-App
scheiterte mehrfach hintereinander, obwohl das Abbild sauber gebaut wurde.

Aus dem redigierten Anlagenlog, zwei Versuche im Abstand von sechs Sekunden:

    Container kraft-tracker Recreate
    Conflict. The container name "/36e1287bd0ab_kraft-tracker" is already in
    use by container "69122302a643…"

    Container 36e1287bd0ab_kraft-tracker Recreate
    Error when allocating new name: Conflict. The container name
    "/kraft-tracker" is already in use by container "36e1287bd0ab03f5…"

Das ist kein Zufall, sondern ein fester Zustand. Compose benennt den laufenden
Container vor dem Ersetzen in ``<hex>_<name>`` um und legt den neuen unter dem
echten Namen an. Bricht das dazwischen ab, bleibt die Sicherungskopie liegen —
MIT den Projektbezeichnungen. Ab da kollidiert jeder weitere ``--force-recreate``
erst mit der Sicherungskopie und dann mit dem echten Namen. Von allein geht das
nie wieder weg; der Betreiber sieht nur noch 500er.

Die bestehende ``test_container_name_conflict.py`` deckt den Nachbarfall ab —
AGENTEN-Container ueber ``AgentManager._create_or_adopt``. Der App-Lebenszyklus
in ``docker_apps.py`` laeuft nicht ueber den AgentManager, sondern ueber
``docker compose`` in einem Runner-Container, und hatte gar keine Aufraeumung.
"""

import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from app.api.docker_apps import (
    _is_name_conflict,
    _reconcile_stale_backups,
)
from docker.errors import APIError, NotFound

PROJECT = "agent-2ad91565-projects-kraft-tracker"

KONFLIKT_SICHERUNG = (
    'Container kraft-tracker Recreate  Error response from daemon: Conflict. '
    'The container name "/36e1287bd0ab_kraft-tracker" is already in use by '
    'container "69122302a6439bd1a829f3755ccd708eb3fe7d8a612d6373a59e8a318280f279". '
    "You have to remove (or rename) that container to be able to reuse that name."
)
KONFLIKT_ECHTNAME = (
    "Container 36e1287bd0ab_kraft-tracker Recreate  Error response from daemon: "
    'Error when allocating new name: Conflict. The container name "/kraft-tracker" '
    'is already in use by container "36e1287bd0ab03f5114ad07b31195e4952af55ffdb7f7".'
)


def _container(name, cid="deadbeef", service="web", nummer="1", remove=None):
    return SimpleNamespace(
        name=name,
        id=cid,
        labels={
            "com.docker.compose.project": PROJECT,
            "com.docker.compose.service": service,
            "com.docker.compose.container-number": nummer,
        },
        remove=remove or MagicMock(),
    )


def _docker(*containers):
    d = SimpleNamespace(client=SimpleNamespace(containers=MagicMock()))
    d.client.containers.list.return_value = list(containers)
    return d


class ErkennenDesKonfliktsTests(unittest.TestCase):
    """Nur ein Namenskonflikt darf einen zweiten Versuch ausloesen — ein voller
    Datentraeger oder ein kaputtes Dockerfile nicht."""

    def test_die_sicherungskopie_blockiert(self):
        self.assertTrue(_is_name_conflict(KONFLIKT_SICHERUNG))

    def test_der_echte_name_blockiert(self):
        self.assertTrue(_is_name_conflict(KONFLIKT_ECHTNAME))

    def test_ein_bauffehler_ist_kein_namenskonflikt(self):
        self.assertFalse(_is_name_conflict(
            "failed to solve: process /bin/sh -c npm ci did not complete successfully"
        ))

    def test_ein_voller_datentraeger_ist_kein_namenskonflikt(self):
        self.assertFalse(_is_name_conflict("no space left on device"))

    def test_leere_ausgabe(self):
        self.assertFalse(_is_name_conflict(""))
        self.assertFalse(_is_name_conflict(None))


class AufraeumenDerSicherungskopienTests(unittest.TestCase):
    def test_die_liegengebliebene_kopie_wird_entfernt(self):
        """Der Fall aus dem Log: echter Container plus Sicherungskopie."""
        echt = _container("kraft-tracker", cid="36e1287bd0ab03f5")
        kopie = _container("36e1287bd0ab_kraft-tracker", cid="69122302a643")

        entfernt = _reconcile_stale_backups(_docker(echt, kopie), PROJECT)

        self.assertEqual(entfernt, ["36e1287bd0ab_kraft-tracker"])
        kopie.remove.assert_called_once_with(force=True)
        echt.remove.assert_not_called()

    def test_der_echte_container_wird_nie_angefasst(self):
        echt = _container("kraft-tracker")
        _reconcile_stale_backups(_docker(echt), PROJECT)
        echt.remove.assert_not_called()

    def test_ein_einzelner_container_bleibt_auch_bei_passendem_namen(self):
        """Falsch-Positiv-Schutz: jemand darf seinen Dienst wirklich
        ``36e1287bd0ab_myapp`` nennen. Solange es fuer diesen Platz nur EINEN
        Container gibt, ist er die App — und nicht der Rest eines Recreates."""
        einzeln = _container("36e1287bd0ab_myapp")
        entfernt = _reconcile_stale_backups(_docker(einzeln), PROJECT)
        self.assertEqual(entfernt, [])
        einzeln.remove.assert_not_called()

    def test_andere_dienste_bleiben_unberuehrt(self):
        """Zwei Container im Projekt, aber verschiedene Dienste — kein Recreate-Rest."""
        web = _container("app-web-1", service="web")
        db = _container("app-db-1", service="db")
        self.assertEqual(_reconcile_stale_backups(_docker(web, db), PROJECT), [])
        web.remove.assert_not_called()
        db.remove.assert_not_called()

    def test_eine_bereits_verschwundene_kopie_ist_kein_fehler(self):
        """Aus dem Issue: „die Entfernung laeuft noch oder der Zielcontainer ist
        inzwischen verschwunden". Ein paralleler Aufraeumer darf uns nicht
        umbringen."""
        echt = _container("kraft-tracker")
        kopie = _container(
            "36e1287bd0ab_kraft-tracker",
            remove=MagicMock(side_effect=NotFound("already gone")),
        )
        self.assertEqual(_reconcile_stale_backups(_docker(echt, kopie), PROJECT), [])

    def test_eine_laufende_entfernung_ist_kein_fehler(self):
        echt = _container("kraft-tracker")
        kopie = _container(
            "36e1287bd0ab_kraft-tracker",
            remove=MagicMock(side_effect=APIError("removal of container is already in progress")),
        )
        self.assertEqual(_reconcile_stale_backups(_docker(echt, kopie), PROJECT), [])

    def test_es_wird_nur_im_eigenen_projekt_aufgeraeumt(self):
        """Fremde Apps duerfen nicht mit abgeraeumt werden."""
        d = _docker(_container("kraft-tracker"), _container("36e1287bd0ab_kraft-tracker"))
        _reconcile_stale_backups(d, PROJECT)
        d.client.containers.list.assert_called_once_with(
            all=True,
            filters={"label": f"com.docker.compose.project={PROJECT}"},
        )


class DerRebuildRaeumtAufUndVersuchtNochmalTests(unittest.IsolatedAsyncioTestCase):
    """Das ist die eigentliche Luecke: ``_rebuild_core`` hat den Konflikt bisher
    unveraendert als 500 durchgereicht, ohne je aufzuraeumen."""

    def _umgebung(self, laeufe):
        agent = SimpleNamespace(container_id="c1", volume_name="workspace-x")
        docker = SimpleNamespace(
            client=SimpleNamespace(containers=MagicMock()),
            exec_in_container=MagicMock(return_value=(0, "")),
        )
        docker.client.containers.list.return_value = []
        return agent, docker, MagicMock(side_effect=laeufe)

    async def test_nach_einem_namenskonflikt_wird_aufgeraeumt_und_wiederholt(self):
        from app.api import docker_apps

        agent, docker, compose = self._umgebung([(1, KONFLIKT_SICHERUNG), (0, "Container Started")])
        aufraeumen = MagicMock(return_value=["36e1287bd0ab_kraft-tracker"])

        with patch.object(docker_apps, "_run_compose", compose), \
             patch.object(docker_apps, "_reconcile_stale_backups", aufraeumen), \
             patch.object(docker_apps, "_resolve_compose_file", MagicMock(return_value="/workspace/p/docker-compose.yml")), \
             patch.object(docker_apps, "_prepare_free_port_compose", MagicMock(return_value="/workspace/p/docker-compose.yml")), \
             patch.object(docker_apps, "_connect_containers_to_network", MagicMock()), \
             patch.object(docker_apps, "_get_project_containers", MagicMock(return_value=[])):
            ergebnis = await docker_apps._rebuild_core(docker, agent, "a1", "projects/kraft-tracker")

        self.assertEqual(ergebnis["status"], "running")
        self.assertEqual(compose.call_count, 2, "der zweite Versuch fehlt")
        self.assertGreaterEqual(aufraeumen.call_count, 1, "es wurde nie aufgeraeumt")

    async def test_ein_echter_baufehler_wird_nicht_wiederholt(self):
        """Sonst dauert jeder kaputte Build doppelt so lang."""
        from fastapi import HTTPException

        from app.api import docker_apps

        agent, docker, compose = self._umgebung([(1, "npm ci did not complete successfully")] * 2)

        with patch.object(docker_apps, "_run_compose", compose), \
             patch.object(docker_apps, "_reconcile_stale_backups", MagicMock(return_value=[])), \
             patch.object(docker_apps, "_resolve_compose_file", MagicMock(return_value="/workspace/p/docker-compose.yml")), \
             patch.object(docker_apps, "_prepare_free_port_compose", MagicMock(return_value="/workspace/p/docker-compose.yml")):
            with self.assertRaises(HTTPException):
                await docker_apps._rebuild_core(docker, agent, "a1", "projects/kraft-tracker")

        self.assertEqual(compose.call_count, 1, "ein Baufehler darf nicht wiederholt werden")

    async def test_zwei_rebuilds_derselben_app_ueberholen_sich_nicht(self):
        """Aus dem Issue: „mehrere Versuche innerhalb weniger Sekunden erzeugen
        wechselnde Konfliktzustaende". Genau dieses Ueberlappen erzeugt die
        liegengebliebenen Kopien ueberhaupt erst."""
        from app.api import docker_apps

        gleichzeitig = 0
        hoechststand = 0

        def langsamer_compose(*_a, **_k):
            nonlocal gleichzeitig, hoechststand
            gleichzeitig += 1
            hoechststand = max(hoechststand, gleichzeitig)
            try:
                import time
                time.sleep(0.05)
                return 0, "Container Started"
            finally:
                gleichzeitig -= 1

        agent = SimpleNamespace(container_id="c1", volume_name="workspace-x")
        docker = SimpleNamespace(
            client=SimpleNamespace(containers=MagicMock()),
            exec_in_container=MagicMock(return_value=(0, "")),
        )
        docker.client.containers.list.return_value = []

        with patch.object(docker_apps, "_run_compose", MagicMock(side_effect=langsamer_compose)), \
             patch.object(docker_apps, "_reconcile_stale_backups", MagicMock(return_value=[])), \
             patch.object(docker_apps, "_resolve_compose_file", MagicMock(return_value="/workspace/p/docker-compose.yml")), \
             patch.object(docker_apps, "_prepare_free_port_compose", MagicMock(return_value="/workspace/p/docker-compose.yml")), \
             patch.object(docker_apps, "_connect_containers_to_network", MagicMock()), \
             patch.object(docker_apps, "_get_project_containers", MagicMock(return_value=[])):
            await asyncio.gather(*[
                docker_apps._rebuild_core(docker, agent, "a1", "projects/kraft-tracker")
                for _ in range(4)
            ])

        self.assertEqual(hoechststand, 1, "vier Rebuilds liefen gleichzeitig auf dieselbe App")

    async def test_verschiedene_apps_blockieren_sich_nicht_gegenseitig(self):
        """Die Sperre gilt pro App — sonst wartet eine kleine App auf den Build
        einer grossen."""
        from app.api import docker_apps

        gleichzeitig = 0
        hoechststand = 0

        def langsamer_compose(*_a, **_k):
            nonlocal gleichzeitig, hoechststand
            gleichzeitig += 1
            hoechststand = max(hoechststand, gleichzeitig)
            try:
                import time
                time.sleep(0.05)
                return 0, "Container Started"
            finally:
                gleichzeitig -= 1

        agent = SimpleNamespace(container_id="c1", volume_name="workspace-x")
        docker = SimpleNamespace(
            client=SimpleNamespace(containers=MagicMock()),
            exec_in_container=MagicMock(return_value=(0, "")),
        )
        docker.client.containers.list.return_value = []

        with patch.object(docker_apps, "_run_compose", MagicMock(side_effect=langsamer_compose)), \
             patch.object(docker_apps, "_reconcile_stale_backups", MagicMock(return_value=[])), \
             patch.object(docker_apps, "_resolve_compose_file", MagicMock(return_value="/workspace/p/docker-compose.yml")), \
             patch.object(docker_apps, "_prepare_free_port_compose", MagicMock(return_value="/workspace/p/docker-compose.yml")), \
             patch.object(docker_apps, "_connect_containers_to_network", MagicMock()), \
             patch.object(docker_apps, "_get_project_containers", MagicMock(return_value=[])):
            await asyncio.gather(
                docker_apps._rebuild_core(docker, agent, "a1", "projects/app-eins"),
                docker_apps._rebuild_core(docker, agent, "a1", "projects/app-zwei"),
            )

        self.assertEqual(hoechststand, 2, "zwei verschiedene Apps haben sich gegenseitig blockiert")


class DerStartWegHatDieselbeLueckeTests(unittest.IsolatedAsyncioTestCase):
    """``_start_core`` faehrt dieselbe Compose-Maschinerie und kann denselben
    Konflikt erben. Genau solche Doppelungen sind in diesem Modul schon einmal
    auseinandergelaufen (siehe test_docker_apps_output_scrub_guard.py)."""

    async def test_der_start_raeumt_nach_einem_konflikt_ebenfalls_auf(self):
        from app.api import docker_apps

        agent = SimpleNamespace(container_id="c1", volume_name="workspace-x")
        docker = SimpleNamespace(
            client=SimpleNamespace(containers=MagicMock()),
            exec_in_container=MagicMock(return_value=(0, "")),
        )
        docker.client.containers.list.return_value = []
        compose = MagicMock(side_effect=[(1, KONFLIKT_ECHTNAME), (0, "Container Started")])
        aufraeumen = MagicMock(return_value=["36e1287bd0ab_kraft-tracker"])

        with patch.object(docker_apps, "_run_compose", compose), \
             patch.object(docker_apps, "_reconcile_stale_backups", aufraeumen), \
             patch.object(docker_apps, "_resolve_compose_file", MagicMock(return_value="/workspace/p/docker-compose.yml")), \
             patch.object(docker_apps, "_ensure_env_files", MagicMock()), \
             patch.object(docker_apps, "_prepare_free_port_compose", MagicMock(return_value="/workspace/p/docker-compose.yml")), \
             patch.object(docker_apps, "_connect_containers_to_network", MagicMock()), \
             patch.object(docker_apps, "_get_project_containers", MagicMock(return_value=[])):
            ergebnis = await docker_apps._start_core(docker, agent, "a1", "projects/kraft-tracker")

        self.assertEqual(ergebnis["status"], "running")
        self.assertEqual(compose.call_count, 2)
        aufraeumen.assert_called()


if __name__ == "__main__":
    unittest.main()
