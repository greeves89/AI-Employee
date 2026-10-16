"""Issue #714, Punkt 1: ein Disk-Quota-gestoppter Agent hatte keinen Weg
zurueck — Aufraeumen brauchte einen laufenden Container, und genau den
verhinderte der Zustand, der aufgeraeumt werden musste.

``cleanup_workspace_volume`` raeumt direkt im Docker-VOLUME auf (gleiches
Muster wie ``copy_workspace_volume``: ein kurzlebiger Helfer-Container, der
NUR das Volume mountet) — braucht den Agenten-Container selbst nicht.
"""
import unittest

from app.services.docker_service import DockerService


class _FakeContainersApi:
    def __init__(self, output=b"42\n", raise_on_run=False, outputs=None):
        # ``outputs``: je Helfer-Lauf eine eigene Ausgabe (erst Worktree-Lauf,
        # dann Cache/Log-Lauf) — ``output`` gilt fuer alle Laeufe.
        self._output = output
        self._outputs = list(outputs) if outputs is not None else None
        self._raise_on_run = raise_on_run
        self.run_calls = []

    def run(self, **kwargs):
        self.run_calls.append(kwargs)
        if self._raise_on_run:
            raise RuntimeError("docker daemon unreachable")
        if self._outputs is not None:
            return self._outputs.pop(0) if self._outputs else b""
        return self._output


class _FakeClient:
    def __init__(self, **kwargs):
        self.containers = _FakeContainersApi(**kwargs)


def _service(**kwargs):
    svc = DockerService.__new__(DockerService)  # bypass __init__ (needs a live daemon)
    svc.client = _FakeClient(**kwargs)
    return svc


def test_returns_the_workspace_size_after_cleanup():
    svc = _service(output=b"42\n")
    assert svc.cleanup_workspace_volume("workspace-a1") == 42


def test_mounts_only_the_volume_not_the_agent_container():
    # Der ganze Witz des Fixes: kein container_id noetig, nur der Volume-Name.
    svc = _service()
    svc.cleanup_workspace_volume("workspace-a1")
    kwargs = svc.client.containers.run_calls[0]
    assert kwargs["volumes"] == {"workspace-a1": {"bind": "/workspace", "mode": "rw"}}
    assert kwargs["remove"] is True


def test_the_cleanup_script_clears_caches_tmp_and_logs():
    svc = _service()
    svc.cleanup_workspace_volume("workspace-a1")
    # Zwei Helfer-Laeufe jetzt (#830): zuerst das Worktree-Aufraeumen
    # (Python), danach dieser Cache/Log-Lauf (sh) — die letzte Ausfuehrung.
    script = svc.client.containers.run_calls[-1]["command"][-1]
    assert "/workspace/data/cache" in script
    assert "/workspace/tmp" in script
    assert "*.log" in script


def test_a_failed_helper_run_returns_none_not_raises():
    svc = _service(raise_on_run=True)
    assert svc.cleanup_workspace_volume("workspace-a1") is None


def test_unparseable_output_returns_none():
    svc = _service(output=b"")
    assert svc.cleanup_workspace_volume("workspace-a1") is None


class ProtokollDesWorktreeLaufsTests(unittest.TestCase):
    """Issue #968 Punkt 2: der ``GIT_FEHLER``-Zweig aus #967 war ungetestet.

    Ein fehlgeschlagener git-Aufruf im Aufraeumlauf laesst die Worktree stehen
    (sichere Richtung) und ist damit genau die Art Fehler, die man nur merkt,
    wenn sie protokolliert wird — sonst sieht niemand, dass hier seit Wochen
    nichts mehr aufgeraeumt wird. Geprueft wird deshalb der WARNING selbst, nicht
    die Schreibweise der Zeile.
    """

    def _lauf(self, prune_ausgabe: bytes):
        svc = _service(outputs=[prune_ausgabe, b"42\n"])
        with self.assertLogs("app.services.docker_service", level="INFO") as protokoll:
            ergebnis = svc.cleanup_workspace_volume("workspace-a1")
        return ergebnis, protokoll.output

    def test_eine_git_fehler_zeile_wird_als_warnung_protokolliert(self):
        ergebnis, zeilen = self._lauf(b"GIT_FEHLER /workspace/projects/wt-x: rc=128 dubious ownership\n")

        warnungen = [z for z in zeilen if z.startswith("WARNING")]
        self.assertTrue(warnungen, f"keine Warnung protokolliert: {zeilen}")
        self.assertIn("wt-x", warnungen[0])
        self.assertEqual(ergebnis, 42, "der Cache/Log-Lauf muss trotzdem weiterlaufen")

    def test_eine_pruned_zeile_ist_kein_fehler_sondern_eine_meldung(self):
        """Trennschaerfe: ein Zweig, der JEDE Zeile warnt, wuerde den Test oben
        auch gruen faerben — hier muss der Erfolgsfall INFO bleiben."""
        _, zeilen = self._lauf(b"PRUNED /workspace/projects/wt-y (branch fix/x)\n")

        self.assertEqual([z for z in zeilen if z.startswith("WARNING")], [])
        self.assertTrue([z for z in zeilen if z.startswith("INFO") and "wt-y" in z],
                        f"Erfolg nicht protokolliert: {zeilen}")

    def test_beide_zeilenarten_in_einem_lauf_landen_getrennt(self):
        _, zeilen = self._lauf(b"PRUNED /workspace/projects/wt-y\nGIT_FEHLER /workspace/projects/wt-z: rc=1\n")

        self.assertEqual(len([z for z in zeilen if z.startswith("WARNING")]), 1, zeilen)
        self.assertEqual(len([z for z in zeilen if z.startswith("INFO")]), 1, zeilen)

    def test_unbekannte_zeilen_erzeugen_kein_rauschen(self):
        svc = _service(outputs=[b"irgendwas\n\n", b"42\n"])
        with self.assertNoLogs("app.services.docker_service", level="INFO"):
            self.assertEqual(svc.cleanup_workspace_volume("workspace-a1"), 42)


if __name__ == "__main__":
    unittest.main()
