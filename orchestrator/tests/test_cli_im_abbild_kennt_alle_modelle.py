"""Das Agenten-Abbild muss eine Claude-Code-CLI mitbringen, die alle angebotenen Modelle kennt.

Am 03.10.2026: Abbild mit 2.1.270, Agent auf ``claude-opus-5-5`` (braucht 2.1.280).
Nach jedem Neustart scheiterte jeder Aufruf mit „does not support this model“, bis
das Hintergrund-Update im Startskript durch war — auf dem Pi gut zwei Minuten. Die
automatische Kompaktierung scheiterte mit, danach hing die Sitzung.
"""

import pathlib
import re
import unittest

from app.core.model_catalog import CLAUDE_CLI_MINDESTENS

DOCKERFILE = pathlib.Path(__file__).resolve().parents[2] / "agent" / "Dockerfile"


def _fassung(text: str) -> tuple[int, ...]:
    return tuple(int(t) for t in text.split("."))


class CliImAbbildTests(unittest.TestCase):
    def test_abbild_mindestens_so_neu_wie_der_katalog_verlangt(self):
        treffer = re.search(r"npm install -g @anthropic-ai/claude-code@(\d+\.\d+\.\d+)",
                            DOCKERFILE.read_text(encoding="utf-8"))
        self.assertIsNotNone(treffer, "Fest installierte CLI-Fassung im Dockerfile nicht gefunden")
        self.assertGreaterEqual(
            _fassung(treffer.group(1)), _fassung(CLAUDE_CLI_MINDESTENS),
            f"Abbild bringt claude-code {treffer.group(1)} mit, der Modellkatalog "
            f"braucht mindestens {CLAUDE_CLI_MINDESTENS}",
        )


if __name__ == "__main__":
    unittest.main()
