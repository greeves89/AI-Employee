"""Kein Werkzeugname darf in einer Sprachsitzung zweimal vorkommen.

Ausfall vom 18.08.2026: Jede Sprachsitzung brach sofort ab mit
``ValidationException: Input is invalid``. Ursache war kein Limit und keine
kaputte Aufnahme — Bedrock lehnt DOPPELTE Werkzeugnamen ab, und der angebundene
Dienst brachte ein ``list_todos`` mit, das die Sprachfront als eingebautes
Werkzeug bereits selbst vergibt.

Die Deduplizierung in ``voice_toolspecs`` kannte nur die MCP-Werkzeuge
untereinander (``vergeben`` startete leer) — die eingebauten Namen sah sie nie.
Folge: nicht ein Werkzeug fiel aus, sondern die ganze Sitzung kam nicht
zustande.
"""

import unittest
from types import SimpleNamespace

from app.core.agent_mcp_servers import voice_toolspecs


def _server(name: str, *tools: str) -> SimpleNamespace:
    return SimpleNamespace(
        id=1, name=name, transport="http", url="https://example.invalid",
        tools=[{"name": t, "description": f"tut {t}"} for t in tools],
        headers=None, auth_token=None, auth_token_encrypted=None,
        extra_headers=None, command=None, args=None, env=None,
    )


class VoiceToolNameUniquenessTests(unittest.TestCase):
    def _namen(self, werkzeuge: list[dict]) -> list[str]:
        return [w["toolSpec"]["name"] for w in werkzeuge]

    def test_builtin_name_is_not_handed_out_twice(self):
        """Der konkrete Ausfall: ein Dienst bringt `list_todos` mit."""
        werkzeuge, plan, _ = voice_toolspecs(
            [_server("ProjectPlannerPro", "list_todos", "list_projects")],
            budget=50,
            reservierte_namen={"list_todos", "desktop", "ask_agent"},
        )
        namen = self._namen(werkzeuge)
        self.assertNotIn(
            "list_todos", namen,
            "Der eingebaute Name wurde ein zweites Mal vergeben — Bedrock lehnt "
            "die gesamte Sitzung ab",
        )
        # Erreichbar bleiben muss es trotzdem, nur unter eindeutigem Namen.
        self.assertTrue(
            any(n.endswith("_list_todos") for n in namen),
            f"Das Werkzeug fehlt jetzt ganz statt umbenannt zu sein: {namen}",
        )

    def test_no_duplicates_at_all_in_the_result(self):
        werkzeuge, _, _ = voice_toolspecs(
            [_server("A", "gleich", "eins"), _server("B", "gleich", "zwei")],
            budget=50,
            reservierte_namen={"eins"},
        )
        namen = self._namen(werkzeuge)
        self.assertEqual(len(namen), len(set(namen)), f"Doppelte Namen: {namen}")

    def test_without_reserved_names_behaviour_is_unchanged(self):
        """Bestandsaufrufer ohne die neue Angabe duerfen sich nicht aendern."""
        werkzeuge, _, _ = voice_toolspecs([_server("A", "eins", "zwei")], budget=50)
        self.assertEqual(sorted(self._namen(werkzeuge)), ["eins", "zwei"])

    def test_voice_session_passes_its_builtin_names(self):
        """Die Absicherung nuetzt nichts, wenn der Aufrufer sie nicht nutzt."""
        from pathlib import Path
        src = (Path(__file__).resolve().parents[1]
               / "app/services/realtime_voice_session.py").read_text(encoding="utf-8")
        aufruf = src.split("voice_toolspecs(", 1)[1][:200]
        self.assertIn("_belegt", aufruf,
                      "Die Sprachfront uebergibt ihre eigenen Werkzeugnamen nicht")


if __name__ == "__main__":
    unittest.main()
