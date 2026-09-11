"""Fuenf identische, leere Platzhalter statt der eigenen Nachrichten (SKBS).

Ein Kunde schrieb waehrend eines laufenden Custom-LLM-Chat-Zugs (GPT ueber den
Custom-LLM-Harness) mehrere kurze Folgenachrichten. Jede wurde korrekt in die
Modell-Historie gefaltet (``self._history``) — aber im sichtbaren Antworttext
landete pro Nachricht nur der nackte Text ``"[Neue Nachricht aufgenommen]"``,
ohne den eigentlichen Inhalt. Fuenf Folgenachrichten ergaben fuenf identische,
inhaltsleere Zeilen hintereinander im Chat-Verlauf — fuer den Kunden nicht
nachvollziehbar, was er selbst geschrieben hatte.

Claude Code/Codex (``agent/app/steering.py``) haben dieses Problem nicht: dort
wird die gefaltete Nachricht direkt als naechster Zug-Input verwendet, nie als
Platzhalter in eine laufende Antwort gemischt. Der Custom-LLM-Pfad ist der
einzige mit der Inline-Falt-Logik in ``llm_chat_handler.py`` (zwei Stellen —
vor dem Zugende ohne Werkzeugaufrufe, und nach Werkzeugergebnissen mitten im
Zug) und braucht deshalb seinen eigenen Fix an beiden Stellen.
"""

import pathlib
import unittest

_SRC = (pathlib.Path(__file__).resolve().parents[1] / "app" / "llm_chat_handler.py").read_text()


class TheFoldedMessageTextIsShownTests(unittest.TestCase):
    def test_the_bare_tag_alone_is_gone(self):
        """Der alte Bug in einem Satz: die Zeile hatte KEINEN Platz fuer den
        eigentlichen Text — bei mehreren Folgenachrichten stapelten sich leere,
        identische Platzhalter."""
        self.assertNotIn('full_text += f"\\n\\n[Neue Nachricht aufgenommen]\\n"', _SRC)
        self.assertNotIn('full_text += "\\n\\n[Neue Nachricht aufgenommen]\\n"', _SRC)

    def test_both_fold_points_interpolate_the_actual_text(self):
        """Zwei Stellen im Custom-LLM-Handler falten Nachrichten ein — vor dem
        Zugende ohne Werkzeugaufrufe, und nach Werkzeugergebnissen mitten im
        Zug. Beide muessen den echten Text zeigen, nicht nur einen davon."""
        treffer = _SRC.count('full_text += f"\\n\\n---\\n*Zwischenzeitlich erhalten:* {t}\\n\\n"')
        self.assertEqual(treffer, 2, "Erwartet an GENAU zwei Stellen (Ende ohne Tools, Mitte nach Tools)")

    def test_the_folded_text_still_reaches_the_model_too(self):
        """Die Anzeige-Reparatur darf die Modell-Seite nicht kaputt machen —
        die gefaltete Nachricht muss weiterhin in die Historie gehen, damit
        das Modell sie tatsaechlich verarbeitet."""
        for zeile in ("self._history.append(ChatMessage(role=\"user\", content=t))",):
            self.assertGreaterEqual(_SRC.count(zeile), 2)


if __name__ == "__main__":
    unittest.main()
