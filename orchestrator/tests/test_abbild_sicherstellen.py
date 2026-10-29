"""Der Orchestrator holt das Agenten-Abbild, wenn es lokal fehlt (#889).

Das Agenten-Abbild ist KEIN Compose-Dienst: ``docker compose pull`` erfasst es
nicht, und ``docker image prune`` nimmt es mit. Fehlte es, scheiterte das Anlegen
eines Agenten mit einer Docker-Meldung ueber einen unbekannten Namen — auf jeder
Anlage, die nie selbst gebaut hat. Geprueft wird hier das VERHALTEN: wann geholt
wird, wann bewusst nicht, und dass der Griff vor dem Start des Behaelters sitzt.
"""
import unittest
from unittest.mock import patch

from docker.errors import APIError, NotFound

from app.services.docker_service import DockerService


class _FakeImage:
    def __init__(self, ref):
        self.ref = ref
        self.tags = []

    def tag(self, name, tag):
        self.tags.append((name, tag))
        return True


class _FakeImages:
    def __init__(self, outer):
        self._outer = outer

    def get(self, name):
        if name in self._outer.vorhanden:
            return _FakeImage(name)
        raise NotFound(f"no such image: {name}")

    def pull(self, ref):
        self._outer.pulls.append(ref)
        self._outer.ablauf.append(("pull", ref))
        if self._outer.pull_fehler is not None:
            raise self._outer.pull_fehler
        bild = _FakeImage(ref)
        self._outer.geholt.append(bild)
        return bild


class _FakeContainers:
    def __init__(self, outer):
        self._outer = outer

    def run(self, **kwargs):
        self._outer.ablauf.append(("run", kwargs.get("image")))
        return type("C", (), {"id": "deadbeef", "name": kwargs.get("name")})()


class _FakeVolumes:
    def __init__(self, outer):
        self._outer = outer

    def get(self, name):
        return object()

    def create(self, name):
        return object()


class _FakeClient:
    def __init__(self, vorhanden=(), pull_fehler=None):
        self.vorhanden = set(vorhanden)
        self.pulls = []
        self.geholt = []
        self.ablauf = []
        self.pull_fehler = pull_fehler

    @property
    def images(self):
        return _FakeImages(self)

    @property
    def containers(self):
        return _FakeContainers(self)

    @property
    def volumes(self):
        return _FakeVolumes(self)


def _dienst(**kwargs):
    svc = DockerService.__new__(DockerService)  # __init__ braucht einen echten Daemon
    svc.client = _FakeClient(**kwargs)
    return svc


class AbbildSicherstellen(unittest.TestCase):
    AGENT = "ai-employee-agent:latest"

    def _lauf(self, dienst, abbild=None, version="1.394.2",
              registry="ghcr.io/greeves89", holen=True, abgelehnt=""):
        with patch("app.config.settings.ai_employee_registry", registry), \
             patch("app.config.settings.ai_employee_abbild_holen", holen), \
             patch("app.config.settings.ai_employee_abbild_abgelehnt", abgelehnt), \
             patch("app.config.get_agent_version", return_value=version):
            return dienst.abbild_sicherstellen(abbild or self.AGENT)

    def test_vorhandenes_abbild_wird_nicht_geholt(self):
        d = _dienst(vorhanden=[self.AGENT])
        self.assertEqual("lokal", self._lauf(d))
        self.assertEqual([], d.client.pulls)

    def test_fehlendes_abbild_wird_in_genau_dieser_version_geholt(self):
        """Nicht "latest": ein beweglicher Tag koennte nach der Freigabe auf ein
        anderes Abbild zeigen."""
        d = _dienst()
        self.assertEqual("registry", self._lauf(d, version="1.394.2"))
        self.assertEqual(["ghcr.io/greeves89/ai-employee-agent:1.394.2"], d.client.pulls)

    def test_geholtes_abbild_wird_auf_den_lokalen_namen_getaggt(self):
        """Ohne den Tag laeuft der naechste Agent in denselben Fehler: AGENT_IMAGE
        zeigt auf den lokalen Namen, nicht auf die Registry-Referenz."""
        d = _dienst()
        self._lauf(d)
        self.assertEqual([("ai-employee-agent", "latest")], d.client.geholt[0].tags)

    def test_lokaler_name_ohne_tag_wird_latest(self):
        """AGENT_IMAGE=ai-employee-agent (ohne Tag) ergab sonst den Tag
        "ai-employee-agent" — ein Name, auf den nichts zeigt."""
        d = _dienst()
        with patch("app.config.settings.agent_image", "ai-employee-agent"):
            self.assertEqual("registry", self._lauf(d, abbild="ai-employee-agent"))
        self.assertEqual([("ai-employee-agent", "latest")], d.client.geholt[0].tags)

    def test_eigener_registry_host_wird_direkt_geholt(self):
        """"host:5000/ai-employee-agent" ist schon eine vollstaendige Referenz
        (eigener Spiegel mit Port). Dann wird genau sie geholt und nichts
        umbenannt — eine aus AI_EMPLOYEE_REGISTRY gebaute Referenz wuerde am
        ausdruecklich gesetzten AGENT_IMAGE vorbeigreifen."""
        d = _dienst()
        with patch("app.config.settings.agent_image", "host:5000/ai-employee-agent"):
            self.assertEqual("registry", self._lauf(d, abbild="host:5000/ai-employee-agent"))
        self.assertEqual(["host:5000/ai-employee-agent"], d.client.pulls)
        self.assertEqual([], d.client.geholt[0].tags)

    def test_eigener_spiegel_wird_benutzt(self):
        d = _dienst()
        self._lauf(d, registry="registry.example.com/ai")
        self.assertEqual(["registry.example.com/ai/ai-employee-agent:1.394.2"], d.client.pulls)

    def test_abgeschaltet_wird_nicht_geholt(self):
        """Abgeschottete Anlage: nur selbst gebaute Abbilder."""
        d = _dienst()
        self.assertEqual("fehlt", self._lauf(d, holen=False))
        self.assertEqual([], d.client.pulls)

    def test_fremdes_abbild_wird_nicht_geholt(self):
        """Fuer jeden anderen Namen waere die Registry-Referenz geraten."""
        d = _dienst()
        self.assertEqual("fehlt", self._lauf(d, abbild="postgres:16"))
        self.assertEqual([], d.client.pulls)

    def test_unbrauchbare_version_holt_nichts(self):
        """get_agent_version() faellt ohne VERSION-Datei auf "0.0.0" bzw. Unsinn
        zurueck — dann darf keine Referenz daraus entstehen."""
        for version in ("latest", "", "0.0.0", "1.2", "1.2.3; rm -rf /", "../../etc"):
            with self.subTest(version=version):
                d = _dienst()
                self.assertEqual("fehlt", self._lauf(d, version=version))
                self.assertEqual([], d.client.pulls)

    def test_unbrauchbare_registry_holt_nichts(self):
        """Der Wert landet in einer Abbild-Referenz, die an den Docker-Dienst
        geht. Schema, Leerzeichen und Rueckwaertsschritte haben dort nichts zu
        suchen, auch nicht aus einer verunglueckten .env."""
        for registry in ("https://ghcr.io/greeves89", "ghcr.io/..", "", "ghcr.io",
                         "ghcr.io/greeves89 && curl evil", "GHCR.IO/Greeves89"):
            with self.subTest(registry=registry):
                d = _dienst()
                self.assertEqual("fehlt", self._lauf(d, registry=registry))
                self.assertEqual([], d.client.pulls)

    def test_nicht_abrufbares_abbild_bricht_nicht_ab(self):
        """Registry weg oder Version dort nie gebaut: der Aufrufer soll in den
        Docker-Fehler des Starts laufen, nicht hier in eine Ausnahme."""
        for fehler in (NotFound("weg"), APIError("kaputt")):
            with self.subTest(fehler=type(fehler).__name__):
                d = _dienst(pull_fehler=fehler)
                self.assertEqual("fehlt", self._lauf(d))

    def test_ein_fehler_bei_der_pruefung_verhindert_keinen_start(self):
        """Der Griff ist eine Bequemlichkeit. Ein Start, der ohne ihn gelaufen
        waere, darf nicht an ihm scheitern. Genau das passierte beim Bau dieser
        Aenderung: ein Docker-Zugang ohne ``images`` liess ``create_container``
        mit AttributeError sterben — drei bestehende Tests wurden rot, und in
        Betrieb waere jeder Agentenstart daran gescheitert."""

        class _ClientOhneImages(_FakeClient):
            @property
            def images(self):
                raise AttributeError("kein images an diesem Zugang")

        d = DockerService.__new__(DockerService)
        d.client = _ClientOhneImages()
        with patch("app.config.settings.ai_employee_registry", "ghcr.io/greeves89"), \
             patch("app.config.settings.ai_employee_abbild_holen", True), \
             patch("app.config.get_agent_version", return_value="1.395.0"):
            behaelter = d.create_container(
                image=self.AGENT,
                name="ai-agent-test-abc123",
                environment={},
                volume_name="vol-abc123",
                network="ai-employee-network",
            )
        self.assertEqual("deadbeef", behaelter.id)
        self.assertEqual([("run", self.AGENT)], d.client.ablauf)

    def test_verworfene_version_kommt_nicht_durch_die_hintertuer(self):
        """#938 verwirft ein Abbild mit ungueltiger Signatur und baut lokal. Ohne
        diese Schranke holte ein spaeteres "docker image prune" genau dieses
        Abbild wieder herein — die Ablehnung waere nicht dauerhaft."""
        d = _dienst()
        self.assertEqual("fehlt", self._lauf(d, version="1.395.0", abgelehnt="1.395.0"))
        self.assertEqual([], d.client.pulls)

    def test_eine_andere_verworfene_version_blockiert_nicht(self):
        """Sonst bliebe die Anlage nach einem einzigen Fehlschlag dauerhaft am
        lokalen Bau haengen, auch nach der naechsten, geprueften Freigabe."""
        d = _dienst()
        self.assertEqual("registry", self._lauf(d, version="1.395.1", abgelehnt="1.395.0"))
        self.assertEqual(["ghcr.io/greeves89/ai-employee-agent:1.395.1"], d.client.pulls)

    def test_volle_registry_referenz_wird_genau_so_geholt(self):
        """Die Community-Zusammenstellung setzt AGENT_IMAGE auf
        ghcr.io/<konto>/ai-employee-agent:<version> UND bindet die VERSION-Datei
        nicht ein. Eine aus VERSION gebaute Nummer waere dort "0.0.0" — also
        eine Referenz, die es nirgends gibt."""
        d = _dienst()
        voll = "ghcr.io/greeves89/ai-employee-agent:1.394.2"
        with patch("app.config.settings.agent_image", voll):
            self.assertEqual("registry", self._lauf(d, abbild=voll, version="0.0.0"))
        self.assertEqual([voll], d.client.pulls)
        # Nichts umzubenennen: AGENT_IMAGE zeigt schon auf genau diese Referenz.
        self.assertEqual([], d.client.geholt[0].tags)

    def test_griff_sitzt_vor_dem_start_des_behaelters(self):
        """Die Gegenprobe gegen das Wegfallen des Aufrufs in create_container:
        erst holen, dann starten. Umgekehrt waere der Griff wirkungslos."""
        d = _dienst()
        with patch("app.config.settings.ai_employee_registry", "ghcr.io/greeves89"), \
             patch("app.config.settings.ai_employee_abbild_holen", True), \
             patch("app.config.settings.agent_image", self.AGENT), \
             patch("app.config.get_agent_version", return_value="1.394.2"):
            d.create_container(
                image=self.AGENT,
                name="ai-agent-test-abc123",
                environment={},
                volume_name="vol-abc123",
                network="ai-employee-network",
            )
        # Reihenfolge, nicht blosses Vorkommen: ein Pull NACH dem Start waere
        # wirkungslos, ein Test auf "beides passierte" wuerde das durchlassen.
        self.assertEqual(
            [("pull", "ghcr.io/greeves89/ai-employee-agent:1.394.2"), ("run", self.AGENT)],
            d.client.ablauf,
        )
        self.assertEqual([("ai-employee-agent", "latest")], d.client.geholt[0].tags)


if __name__ == "__main__":
    unittest.main()
