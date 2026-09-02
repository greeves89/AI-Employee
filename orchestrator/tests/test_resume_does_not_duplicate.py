"""Ein Neustart darf keine laufende Aufgabe ein zweites Mal starten.

Befund #695 (Nacht zum 01.09.2026, echte Kennungen): Der Orchestrator startete
neu, nahm eine laufende Aufgabe als „unterbrochen" an und disponierte 775 ms
spaeter einen Ersatz. Das Original endete **36 Sekunden nach** seinem eigenen
Ersatz — beide mit vollstaendigem Tagesabschluss-Bericht. Der Nutzer bekam ihn
doppelt, die Kosten ebenso; bei fuenf Neustarts hintereinander lief ein
Planblock fuenfmal durch (rund 14 statt knapp 4 USD).

Ursache: Der Agent steckt in einem EIGENEN Container und ueberlebt den Neustart
muehelos. Die Stilllegung fasste nur die Datenbankzeile an — fuer den fremden,
lebenden Container rein kosmetisch. Und `classify_on_startup` stufte gerade die
FRISCHESTEN Lebenszeichen am sichersten als wiederaufnehmbar ein.
"""

import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.services import job_state

_MAIN = (Path(__file__).resolve().parents[1] / "app" / "main.py").read_text()
_RESUME = _MAIN.split("async def _resume_agent_task", 1)[1][:9000]


class _Job:
    def __init__(self, status="running", last_heartbeat=None):
        self.status = status
        self.last_heartbeat = last_heartbeat
        self.id = "task:abc"


class EinFrischerHerzschlagSchuetztVorDemErsatzTests(unittest.TestCase):
    def test_gerade_eben_geschlagen(self):
        jetzt = datetime.now(timezone.utc)
        job = _Job(last_heartbeat=jetzt - timedelta(seconds=20))
        self.assertTrue(job_state.wahrscheinlich_noch_am_leben(job, jetzt))

    def test_zwei_verpasste_schlaege_sind_noch_kein_beweis(self):
        """Bei 60 s Takt: 90 s bedeutet einen verpassten Schlag, nicht den Tod."""
        jetzt = datetime.now(timezone.utc)
        job = _Job(last_heartbeat=jetzt - timedelta(seconds=90))
        self.assertTrue(job_state.wahrscheinlich_noch_am_leben(job, jetzt))

    def test_eine_viertelstunde_stille_ist_einer(self):
        jetzt = datetime.now(timezone.utc)
        job = _Job(last_heartbeat=jetzt - timedelta(minutes=14))
        self.assertFalse(job_state.wahrscheinlich_noch_am_leben(job, jetzt))

    def test_ohne_lebenszeichen_keine_schutzbehauptung(self):
        jetzt = datetime.now(timezone.utc)
        self.assertFalse(job_state.wahrscheinlich_noch_am_leben(_Job(), jetzt))

    def test_nur_laufende_arbeiten(self):
        jetzt = datetime.now(timezone.utc)
        job = _Job(status="completed", last_heartbeat=jetzt)
        self.assertFalse(job_state.wahrscheinlich_noch_am_leben(job, jetzt))

    def test_das_alte_urteil_bleibt_unveraendert(self):
        """`is_resumable` darf sich nicht mitverschieben — es beantwortet eine
        andere Frage (ist das Lebenszeichen uralt?)."""
        jetzt = datetime.now(timezone.utc)
        job = _Job(last_heartbeat=jetzt - timedelta(minutes=10))
        self.assertTrue(job_state.is_resumable(job, jetzt))
        self.assertFalse(job_state.wahrscheinlich_noch_am_leben(job, jetzt))


class VorDemErsetzenWirdGefragtTests(unittest.TestCase):
    def test_der_agent_wird_nach_der_aufgabe_gefragt(self):
        """`_agent_claims_task` gab es laengst — im Resume-Pfad fragte sie nur
        niemand. Genau das ist der Fehler."""
        self.assertIn("_agent_claims_task(orig.agent_id, orig.id)", _RESUME)

    def test_bei_einem_ja_wird_nicht_ersetzt(self):
        self.assertIn("if lebt_noch:", _RESUME)
        # Der zweite Block ist der entscheidende — er verlaesst die Funktion.
        block = _RESUME.split("if lebt_noch:")[2][:500]
        self.assertIn("return", block)
        self.assertIn("delete_job(db, job.id)", block)

    def test_eine_fehlende_zeile_bringt_die_meldung_nicht_zu_fall(self):
        """`orig` kann geloescht sein — dann gibt es kein `orig.id`."""
        block = _RESUME.split("if lebt_noch:")[2][:500]
        self.assertIn("orig.id if orig is not None else", block)

    def test_im_zweifel_wird_nicht_ersetzt(self):
        """Ein Doppellauf kostet Geld und verwirrt; ein ausgelassener Ersatz
        kostet nur Zeit. Also faellt die Unsicherheit auf die sichere Seite."""
        block = _RESUME.split("Lebensfrage fuer", 1)[1][:400]
        self.assertIn("lebt_noch = True", block)

    def test_der_frische_herzschlag_wird_zusaetzlich_geprueft(self):
        self.assertIn("wahrscheinlich_noch_am_leben(job", _RESUME)


class DerAgentErfaehrtVonSeinerStilllegungTests(unittest.TestCase):
    def test_das_abbruchsignal_geht_raus(self):
        """Die Zeile auf FAILED zu setzen erreicht einen fremden Container
        nicht — er arbeitet ahnungslos weiter."""
        self.assertIn('f"agent:{orig.agent_id}:task:cancel"', _RESUME)

    def test_es_geht_vor_der_stilllegung_raus(self):
        signal = _RESUME.index("task:cancel")
        failed = _RESUME.index("orig.status = TaskStatus.FAILED\n                orig.error = \"Superseded")
        self.assertLess(signal, failed)

    def test_die_nutzlast_ist_die_rohe_kennung(self):
        """Der Zuhoerer im Agenten liest sie als ID; JSON stoppt nichts."""
        stelle = _RESUME.index("task:cancel")
        self.assertIn("orig.id", _RESUME[stelle:stelle + 150])

    def test_ein_fehlschlag_haelt_die_wiederaufnahme_nicht_auf(self):
        block = _RESUME.split("task:cancel", 1)[1][:400]
        self.assertIn("except Exception", block)


if __name__ == "__main__":
    unittest.main()
