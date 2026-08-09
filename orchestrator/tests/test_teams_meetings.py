"""Agent in Teams-Terminen — Mitschreiber oder Beisitzer.

Der Entwurf erfindet bewusst nichts Neues: ein Teams-Termin HAT einen Chat, und den
Chat-Eingang gibt es bereits. „Beisitzend" heisst deshalb, fuer die Dauer des Termins
an genau diesem Chat zu haengen — ueber dieselbe Liste, die der Teams-Eingang ohnehin
abfragt. „Mitschreiber" heisst, das Transkript ueber den gemeinsamen Wissens-Schreibweg
abzulegen, also mit Embedding und Verknuepfung.
"""

import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

from app.services import teams_meetings as tm

REPO = Path(__file__).resolve().parents[2]
ORCH = REPO / "orchestrator"

NOW = datetime(2026, 8, 8, 10, 0, tzinfo=timezone.utc)


def _event(start_offset_min=0, duration_min=60, **over):
    start = NOW + timedelta(minutes=start_offset_min)
    end = start + timedelta(minutes=duration_min)
    data = {
        "id": "evt1",
        "subject": "Wochenabstimmung",
        "start": {"dateTime": start.isoformat().replace("+00:00", "Z")},
        "end": {"dateTime": end.isoformat().replace("+00:00", "Z")},
        "isOnlineMeeting": True,
        "organizer": {"emailAddress": {"address": "chef@firma.de"}},
        "responseStatus": {"response": "none"},
    }
    data.update(over)
    return data


def _agent(**meeting_cfg):
    return SimpleNamespace(id="a1", name="Protokollant", user_id="u1",
                           config={"channels": {"meetings": meeting_cfg}})


class ConfigTests(unittest.TestCase):
    def test_disabled_by_default(self):
        self.assertFalse(tm.is_enabled(SimpleNamespace(id="a", name="x", config={})))

    def test_needs_a_valid_role(self):
        self.assertFalse(tm.is_enabled(_agent(enabled=True)))
        self.assertFalse(tm.is_enabled(_agent(enabled=True, role="zuschauer")))
        self.assertTrue(tm.is_enabled(_agent(enabled=True, role=tm.ROLE_SCRIBE)))
        self.assertTrue(tm.is_enabled(_agent(enabled=True, role=tm.ROLE_PARTICIPANT)))


class WindowTests(unittest.TestCase):
    def test_active_shortly_before_the_start(self):
        """Frueher waere unnoetig, spaeter verpasst er den Anfang."""
        self.assertTrue(tm.is_active_now(_event(start_offset_min=3), NOW))

    def test_not_active_long_before(self):
        self.assertFalse(tm.is_active_now(_event(start_offset_min=60), NOW))

    def test_active_during(self):
        self.assertTrue(tm.is_active_now(_event(start_offset_min=-10), NOW))

    def test_stays_a_while_after_the_end(self):
        """Nachbesprechungen im Chat laufen weiter."""
        self.assertTrue(tm.is_active_now(_event(start_offset_min=-70, duration_min=60), NOW))

    def test_not_active_long_after(self):
        self.assertFalse(tm.is_active_now(_event(start_offset_min=-240), NOW))

    def test_finished_only_after_the_grace_period(self):
        """Das Transkript steht bei Teams erst mit Verzug bereit."""
        just_ended = _event(start_offset_min=-61, duration_min=60)
        self.assertFalse(tm.is_finished(just_ended, NOW))
        long_ended = _event(start_offset_min=-120, duration_min=60)
        self.assertTrue(tm.is_finished(long_ended, NOW))

    def test_broken_times_are_not_active(self):
        self.assertFalse(tm.is_active_now({"start": {}, "end": {}}, NOW))
        self.assertFalse(tm.is_active_now({}, NOW))


class AutoAcceptTests(unittest.TestCase):
    def test_off_by_default(self):
        """Ein Agent, der jede Einladung annimmt, taucht in fremden Terminen auf
        und liest dort mit — das muss eine bewusste Entscheidung sein."""
        agent = _agent(enabled=True, role=tm.ROLE_SCRIBE)
        self.assertFalse(tm.should_auto_accept(agent, _event()))

    def test_accepts_when_switched_on(self):
        agent = _agent(enabled=True, role=tm.ROLE_SCRIBE, auto_accept=True)
        self.assertTrue(tm.should_auto_accept(agent, _event()))

    def test_already_answered_is_left_alone(self):
        agent = _agent(enabled=True, role=tm.ROLE_SCRIBE, auto_accept=True)
        event = _event(responseStatus={"response": "accepted"})
        self.assertFalse(tm.should_auto_accept(agent, event))

    def test_organizer_allowlist(self):
        agent = _agent(enabled=True, role=tm.ROLE_SCRIBE, auto_accept=True,
                       accept_from=["firma.de"])
        self.assertTrue(tm.should_auto_accept(agent, _event()))
        fremd = _event(organizer={"emailAddress": {"address": "wer@fremd.example"}})
        self.assertFalse(tm.should_auto_accept(agent, fremd))


class TranscriptTests(unittest.TestCase):
    def test_vtt_becomes_readable_text(self):
        raw = (
            "WEBVTT\n\n"
            "1\n"
            "00:00:01.000 --> 00:00:04.000\n"
            "<v Anna Beispiel>Guten Morgen zusammen.</v>\n\n"
            "2\n"
            "00:00:04.000 --> 00:00:07.000\n"
            "<v Bert Muster>Moin, fangen wir an.</v>\n"
        )
        out = tm._vtt_to_text(raw)
        self.assertIn("Anna Beispiel: Guten Morgen zusammen.", out)
        self.assertIn("Bert Muster: Moin, fangen wir an.", out)
        self.assertNotIn("-->", out)
        self.assertNotIn("WEBVTT", out)

    def test_consecutive_lines_of_one_speaker_are_merged(self):
        raw = (
            "WEBVTT\n\n"
            "00:00:01.000 --> 00:00:02.000\n<v Anna>Erster Teil</v>\n\n"
            "00:00:02.000 --> 00:00:03.000\n<v Anna>zweiter Teil</v>\n"
        )
        out = tm._vtt_to_text(raw)
        self.assertEqual(out, "Anna: Erster Teil zweiter Teil")

    def test_empty_input(self):
        self.assertEqual(tm._vtt_to_text(""), "")

    def test_title_is_stable_for_the_same_meeting(self):
        """Der Titel ist der Schluessel gegen doppeltes Mitschreiben."""
        event = _event()
        self.assertEqual(tm.transcript_title(event), tm.transcript_title(event))
        self.assertIn("Wochenabstimmung", tm.transcript_title(event))


class NoSecondMechanismTests(unittest.TestCase):
    SRC = ORCH / "app/services/teams_meetings.py"

    def test_participant_uses_the_existing_chat_input(self):
        """Kein eigener Meeting-Kanal: der Beisitzer ist ein Chat mehr."""
        src = self.SRC.read_text()
        self.assertIn("chat_ids", src)
        self.assertIn("_sync_watched_chats", src)

    def test_meeting_chats_are_removed_again(self):
        """Sonst haengt der Agent fuer immer an jeder Besprechung, in der er je war."""
        src = self.SRC.read_text()
        self.assertIn("chat_ids_meetings", src)
        self.assertIn("chat_ids_manual", src,
                      "Von Hand eingetragene Chats duerfen dabei nicht verloren gehen.")

    def test_transcript_uses_the_shared_knowledge_path(self):
        src = self.SRC.read_text()
        self.assertIn("knowledge_write", src)
        self.assertNotIn("SET embedding = CAST", src)

    def test_no_own_scheduler(self):
        src = (ORCH / "app/services/scheduler_service.py").read_text()
        self.assertIn("_teams_meetings", src)

    def test_no_media_bot_is_pretended(self):
        """Eine Stimme in der Audiospur braucht einen Media-Bot im Azure Bot Service.
        Das steht im Modul als Grenze benannt, statt es vorzugaukeln."""
        src = self.SRC.read_text()
        self.assertIn("Media-Bot", src)


if __name__ == "__main__":
    unittest.main()
