"""Das Kurzzeitgedaechtnis fuer Infrastruktur-Fehler (#746 Punkt 3).

Der Sentinel-Alarm vom 15.09.2026 kam mitten in einem DNS-Aussetzer und nannte
die Ursache nicht. Der Handler hier soll genau die Frage beantworten koennen:
„Standen im selben Zeitraum DNS-/Redis-/DB-Fehler im Protokoll?"

Die Faelle sind dem echten Protokoll nachgebaut — insbesondere der gaierror,
der NICHT in der Nachricht stand, sondern nur im angehaengten ``exc_info``.
"""

import logging
import socket
import unittest
from datetime import timedelta

from app.core import infra_error_window as iew
from app.core.infra_error_window import (
    InfraErrorWindow,
    klassifiziere,
    setup_infra_error_window,
    ursachen_hinweis,
)


def _record(msg: str, *, exc: BaseException | None = None,
            level: int = logging.ERROR, created: float | None = None) -> logging.LogRecord:
    exc_info = (type(exc), exc, None) if exc is not None else None
    rec = logging.LogRecord("asyncio", level, __file__, 1, msg, None, exc_info)
    if created is not None:
        rec.created = created
    return rec


class TheClassifierTests(unittest.TestCase):
    def test_a_name_resolution_failure_is_dns(self):
        self.assertEqual(klassifiziere("[Errno -3] Temporary failure in name resolution"), "dns")

    def test_a_redis_timeout_is_redis(self):
        self.assertEqual(
            klassifiziere("Failed to release lock arm_plan_blocks: Timeout connecting to server"),
            "redis",
        )

    def test_dns_wins_over_redis_when_both_appear(self):
        """Ein Redis-Fehler WEGEN Namensaufloesung ist ein DNS-Fehler."""
        self.assertEqual(
            klassifiziere("redis.exceptions.ConnectionError: Error -3 connecting to redis:6379. "
                          "Temporary failure in name resolution."),
            "dns",
        )

    def test_an_ordinary_warning_is_nothing(self):
        self.assertIsNone(klassifiziere("[Scheduler] Sentinel verstummt — letztes Lebenszeichen: 1789485467"))


class TheHandlerTests(unittest.TestCase):
    def setUp(self):
        self.h = InfraErrorWindow()

    def test_the_gaierror_hidden_in_exc_info_is_seen(self):
        """So sah der 15.09. aus: Nachricht nichtssagend, Ursache nur im Traceback."""
        self.h.emit(_record(
            "Future exception was never retrieved",
            exc=socket.gaierror(-3, "Temporary failure in name resolution"),
        ))
        self.assertEqual(self.h.zusammenfassung()["dns"], 1)

    def test_a_plain_message_signature_is_seen(self):
        self.h.emit(_record("[Scheduler] Plan-Bloecke scharf stellen fehlgeschlagen: "
                            "Timeout connecting to server", level=logging.WARNING))
        self.assertEqual(self.h.zusammenfassung()["redis"], 1)

    def test_unrelated_records_are_not_stored(self):
        self.h.emit(_record("Exception happened while polling for updates."))
        self.assertEqual(sum(self.h.zusammenfassung().values()), 0)
        self.assertEqual(len(self.h._eintraege), 0)

    def test_old_entries_fall_out_of_the_window(self):
        jetzt = 1_789_485_000.0
        self.h.emit(_record("gaierror", created=jetzt - 11 * 60))
        self.h.emit(_record("gaierror", created=jetzt - 9 * 60))
        z = self.h.zusammenfassung(timedelta(minutes=10), jetzt=jetzt)
        self.assertEqual(z["dns"], 1)

    def test_every_class_is_present_even_at_zero(self):
        self.assertEqual(set(self.h.zusammenfassung()), {"dns", "redis", "db"})

    def test_the_buffer_is_bounded(self):
        h = InfraErrorWindow(maxlen=5)
        for _ in range(50):
            h.emit(_record("gaierror"))
        self.assertEqual(h.zusammenfassung()["dns"], 5)

    def test_a_cause_chain_is_followed(self):
        """``raise RuntimeError(...) from gaierror`` — die Ursache steckt eine
        Ebene tiefer und ist trotzdem DNS."""
        innen = socket.gaierror(-3, "Temporary failure in name resolution")
        aussen = RuntimeError("Sperre konnte nicht freigegeben werden")
        aussen.__cause__ = innen
        self.h.emit(_record("Failed to release lock", exc=aussen))
        self.assertEqual(self.h.zusammenfassung()["dns"], 1)

    def test_a_record_that_asks_to_be_ignored_is_ignored(self):
        """Die Alarmzeile des Wachhunds zitiert den Hinweis („1x Redis-…") —
        sie darf sich nicht selbst als Redis-Fehler zaehlen."""
        rec = _record("Timeout connecting to server")
        rec.infra_fenster_ignorieren = True
        self.h.emit(rec)
        self.assertEqual(sum(self.h.zusammenfassung().values()), 0)

    def test_the_word_redis_alone_is_no_signature(self):
        """Eine WARNING, die Redis nur ERWAEHNT, ist kein Verbindungsfehler."""
        self.h.emit(_record("KEINE Redis-/DNS-/DB-Fehler protokolliert", level=logging.WARNING))
        self.h.emit(_record("Redis-ACL-Schalter steht auf false", level=logging.WARNING))
        self.assertEqual(sum(self.h.zusammenfassung().values()), 0)

    def test_the_real_redis_signatures_are_seen(self):
        for text in (
            "redis.exceptions.ConnectionError: Error 111 connecting to redis:6379. Connection refused.",
            "redis.exceptions.ConnectionError: Connection closed by server.",
            "[Scheduler] RedisACL error: Timeout connecting to server",
        ):
            with self.subTest(text=text):
                h = InfraErrorWindow()
                h.emit(_record(text))
                self.assertEqual(h.zusammenfassung()["redis"], 1)

    def test_the_real_db_retry_line_is_seen(self):
        self.h.emit(_record("[resilient_session] connect attempt 2/3 failed (TimeoutError: ); "
                            "retrying in 1.01s", level=logging.WARNING))
        self.assertEqual(self.h.zusammenfassung()["db"], 1)

    def test_a_broken_record_does_not_raise(self):
        """Ein Protokoll-Handler, der das Protokoll zum Absturz bringt, waere absurd."""
        rec = _record("%s %s", level=logging.ERROR)     # Formatfehler: Argumente fehlen
        rec.args = ("nur-eins",)
        self.h.emit(rec)                                  # darf nicht werfen


class TheHintTests(unittest.TestCase):
    def setUp(self):
        self._alt = iew._fenster
        iew._fenster = InfraErrorWindow()

    def tearDown(self):
        iew._fenster = self._alt

    def test_with_dns_and_redis_errors_the_hint_names_them(self):
        iew._fenster.emit(_record("gaierror"))
        iew._fenster.emit(_record("gaierror"))
        iew._fenster.emit(_record("Timeout connecting to server"))
        hinweis = ursachen_hinweis()
        self.assertIn("2x DNS-Aussetzer", hinweis)
        self.assertIn("1x Redis-Verbindungsfehler", hinweis)
        self.assertIn("Infrastruktur-Aussetzer", hinweis)

    def test_without_errors_the_hint_says_so(self):
        hinweis = ursachen_hinweis()
        self.assertIn("KEINE", hinweis)
        self.assertIn("im Dienst selbst", hinweis)

    def test_without_a_handler_the_hint_admits_it_did_not_measure(self):
        """„Nicht gemessen" ist etwas anderes als „keine Fehler"."""
        iew._fenster = None
        self.assertIn("nicht gemessen", ursachen_hinweis())


class TheSetupTests(unittest.TestCase):
    def tearDown(self):
        root = logging.getLogger()
        for h in list(root.handlers):
            if isinstance(h, InfraErrorWindow):
                root.removeHandler(h)
        iew._fenster = None

    def test_setup_is_idempotent_and_wires_the_root_logger(self):
        a = setup_infra_error_window()
        b = setup_infra_error_window()
        self.assertIs(a, b)
        self.assertEqual(
            sum(isinstance(h, InfraErrorWindow) for h in logging.getLogger().handlers), 1,
        )
        logging.getLogger("asyncio").error(
            "Future exception was never retrieved",
            exc_info=(socket.gaierror, socket.gaierror(-3, "Temporary failure in name resolution"), None),
        )
        self.assertEqual(a.zusammenfassung()["dns"], 1)
        self.assertIn("1x DNS-Aussetzer", ursachen_hinweis())
