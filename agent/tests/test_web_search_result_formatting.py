"""agent/app/tools/api_client.py::_format_web_search_result (#806-Nachtrag).

Der brave_news-Provider liefert ``age``/``publisher`` mit, damit ein Agent
einen zwei Jahre alten Artikel von einer Meldung von heute unterscheiden
kann — das war der erklaerte Zweck des PRs, aber die Felder wurden beim
Formatieren des Textblocks fuer den Agenten schlicht verworfen. Diese Tests
pruefen die Textform direkt, ohne HTTP/Orchestrator zu mocken.
"""

import unittest

from app.tools.api_client import _format_web_search_result


class FormatWebSearchResultTests(unittest.TestCase):
    def test_age_and_publisher_are_included_when_present(self):
        block = _format_web_search_result({
            "title": "Gold hits record",
            "url": "https://example.test/gold",
            "snippet": "Spot gold rose.",
            "age": "3 hours ago",
            "publisher": "reuters.com",
        })
        self.assertEqual(
            block,
            "**Gold hits record** (3 hours ago) — reuters.com\n"
            "https://example.test/gold\nSpot gold rose.",
        )

    def test_missing_age_and_publisher_keep_the_original_format(self):
        """DuckDuckGo/Brave-Websuche/SerpApi liefern kein age/publisher — das
        Format fuer sie darf sich durch diese Erweiterung nicht aendern."""
        block = _format_web_search_result({
            "title": "Pokemon Karten kaufen",
            "url": "https://example.test/p",
            "snippet": "Sammelkarten",
        })
        self.assertEqual(block, "**Pokemon Karten kaufen**\nhttps://example.test/p\nSammelkarten")

    def test_age_without_publisher(self):
        block = _format_web_search_result({
            "title": "t", "url": "u", "snippet": "s", "age": "1 day ago", "publisher": "",
        })
        self.assertEqual(block, "**t** (1 day ago)\nu\ns")

    def test_publisher_without_age(self):
        block = _format_web_search_result({
            "title": "t", "url": "u", "snippet": "s", "age": "", "publisher": "example.com",
        })
        self.assertEqual(block, "**t** — example.com\nu\ns")


if __name__ == "__main__":
    unittest.main()
