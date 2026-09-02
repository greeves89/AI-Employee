"""Jedes Werkzeug, mit dem ein Agent Skills ENTDECKT, muss die `id` mit ausgeben.

Warum es diesen Test gibt (Befund 2026-08-24, Issue #667): `skill_search` und
`skill_get_my_skills` sind die einzigen beiden Wege, auf denen ein Agent je einen
Skill zu sehen bekommt. Beide bauten ihre Textausgabe aus `name`, `description` und
`content` — die `id` warfen sie weg, obwohl der Server sie in `_to_response()`
mitliefert.

`skill_id` ist aber Pflichtparameter bei `skill_rate`, `skill_install`,
`skill_record_usage`, `skill_update` und `skill_get`. Ohne die ID in der Ausgabe war
der Kreislauf Suchen -> Installieren -> Nutzen -> Bewerten an seiner ERSTEN Stelle
unterbrochen: ein Agent konnte einen Skill finden, aber weder installieren noch
bewerten. Zwei Proaktiv-Laeufe in Folge scheiterten daran, ohne dass irgendwo ein
Fehler auftauchte — ein weggelassenes Feld beim Rendern hinterlaesst keine Spur.

Der Test sucht die Formatierer UEBER IHRE FORM (`result.skills.map(...)`), nicht
ueber eine gepflegte Namensliste. Ein kuenftiges drittes Entdeckungswerkzeug faellt
damit automatisch in die Pruefung, statt denselben Fehler unbemerkt zu wiederholen.
"""

import json
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SKILL_SERVER = ROOT / "agent/mcp/skill-server.mjs"

# Werkzeuge, deren Schema eine numerische skill_id verlangt. Ohne einen Weg, an
# diese ID zu kommen, sind sie fuer einen Agenten nicht aufrufbar.
BENOETIGEN_SKILL_ID = {
    "skill_rate",
    "skill_install",
    "skill_record_usage",
    "skill_update",
    "skill_get",
}


def _skill_formatierer(quelltext: str) -> list[tuple[int, str]]:
    """Alle Stellen, die eine Skill-Liste zu Text rendern.

    Erkennungsmerkmal ist ``result.skills.map(`` — der Rumpf reicht bis zur
    schliessenden Klammer auf gleicher Ebene. Rueckgabe: (Zeilennummer, Rumpf).
    """
    treffer = []
    for m in re.finditer(r"result\.skills\.map\(", quelltext):
        start = m.end() - 1  # auf die oeffnende Klammer von map(
        tiefe = 0
        for i in range(start, len(quelltext)):
            if quelltext[i] == "(":
                tiefe += 1
            elif quelltext[i] == ")":
                tiefe -= 1
                if tiefe == 0:
                    zeile = quelltext.count("\n", 0, m.start()) + 1
                    treffer.append((zeile, quelltext[start : i + 1]))
                    break
    return treffer


class JedesEntdeckungswerkzeugGibtDieIdAusTests(unittest.TestCase):
    def setUp(self):
        self.quelltext = SKILL_SERVER.read_text(encoding="utf-8")

    def test_formatierer_werden_ueberhaupt_gefunden(self):
        """Schutz vor einem Test, der still nichts mehr prueft.

        Wird der Server umgebaut und heisst das Feld nicht mehr ``result.skills``,
        faende die Suche nichts — und der Test unten waere gruen, ohne je etwas
        angesehen zu haben. Genau die Falle, die dieser Test verhindern soll.
        """
        self.assertGreaterEqual(
            len(_skill_formatierer(self.quelltext)),
            2,
            "Weniger als zwei Skill-Formatierer gefunden. Entweder wurde ein "
            "Entdeckungswerkzeug entfernt, oder das Erkennungsmerkmal "
            "'result.skills.map(' stimmt nicht mehr — dann muss dieser Test "
            "angepasst werden, statt ihn gruen laufen zu lassen.",
        )

    def test_jeder_formatierer_rendert_die_id(self):
        ohne_id = [
            f"{SKILL_SERVER.name}:{zeile}"
            for zeile, rumpf in _skill_formatierer(self.quelltext)
            if "${s.id}" not in rumpf
        ]
        self.assertEqual(
            [],
            ohne_id,
            "Diese Skill-Ausgabe(n) lassen die id weg: "
            f"{ohne_id}. Ein Agent sieht den Skill dann zwar, kann ihn aber weder "
            f"installieren noch bewerten — {sorted(BENOETIGEN_SKILL_ID)} verlangen "
            "alle eine numerische skill_id.",
        )


class DerServerLiefertDieIdUeberhauptTests(unittest.TestCase):
    """Gegenstueck: die Ausgabe kann die id nur zeigen, wenn die API sie schickt.

    Ohne diese Haelfte wuerde der Test oben eine kaputte Kette nur verschieben --
    `${s.id}` stuende im Quelltext und rendert trotzdem `undefined`.
    """

    def test_to_response_enthaelt_die_id(self):
        from app.api.skill_marketplace import _to_response

        class _Skill:
            id = 42
            name = "beispiel"
            description = "Beispiel"
            content = "Inhalt"
            category = "PATTERN"
            status = "ACTIVE"
            created_by = None
            source_url = None
            source_repo = None
            paths = None
            roles = None
            usage_count = 0
            avg_rating = None
            avg_agent_duration_ms = None
            manual_duration_seconds = None
            is_public = True
            current_version = 1
            improvement_status = None
            improvement_proposal = None
            improvement_proposed_at = None
            improvement_review_reason = None
            created_at = None
            updated_at = None

        antwort = _to_response(_Skill())
        self.assertEqual(42, antwort.get("id"))
        # Muss durch JSON gehen -- der MCP-Server liest die Antwort als JSON.
        self.assertEqual(42, json.loads(json.dumps(antwort))["id"])


if __name__ == "__main__":
    unittest.main()
