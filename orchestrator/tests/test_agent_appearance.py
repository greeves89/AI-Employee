"""Symbol, Farbe, Schlagwort — die Prüfung dahinter (#523, #524).

Der springende Punkt: die alte Sperrliste war ein Schutz mit falscher Begründung.
Sie hielt zwar eingeschleuste Formatierung ab, verbot dabei aber auch jedes
harmlose Sinnbild. Diese Tests halten beides fest — dass die Enge weg ist UND dass
der Schutz geblieben ist.
"""

import unittest

from app.core.agent_appearance import (
    PALETTE,
    TAG_MAX,
    apply_appearance,
    tag_of,
    validate_color,
    validate_icon,
    validate_tag,
)


class IconTests(unittest.TestCase):
    def test_curated_names_still_work(self):
        for name in ("Bot", "Cpu", "MessageSquare", "FlaskConical"):
            self.assertEqual(validate_icon(name), name)

    def test_any_lucide_name_is_allowed_now(self):
        """Der eigentliche Zweck von #523."""
        for name in ("Truck", "HeartPulse", "ChartNoAxesColumn", "message-square", "AArrowDown"):
            self.assertEqual(validate_icon(name), name)

    def test_empty_means_no_custom_icon(self):
        self.assertEqual(validate_icon(""), "")
        self.assertEqual(validate_icon("   "), "")

    def test_anything_that_is_not_a_name_is_refused(self):
        """Der Name wird im Browser auf eine Komponente abgebildet — dort darf
        nichts landen, was sich als etwas anderes lesen laesst."""
        for bad in (
            "<script>alert(1)</script>",
            "Bot; drop",
            "Bot')",
            "../../etc/passwd",
            "Bot Cpu",
            "1Bot",          # muss mit einem Buchstaben beginnen
            "B" * 80,        # absurd lang
        ):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    validate_icon(bad)


class ColorTests(unittest.TestCase):
    def test_palette_names_survive(self):
        for name in PALETTE:
            self.assertEqual(validate_color(name), name)

    def test_hex_is_accepted_and_normalised(self):
        self.assertEqual(validate_color("#4F46E5"), "#4f46e5")
        self.assertEqual(validate_color("  #abcdef "), "#abcdef")

    def test_short_hex_is_refused(self):
        """#abc wuerde mit angehaengter Deckung zu #abc1a — fuenf Stellen, ungueltig,
        und der Kasten bliebe farblos."""
        with self.assertRaises(ValueError):
            validate_color("#abc")

    def test_css_cannot_be_smuggled_in(self):
        for bad in (
            "red; background: url(http://x)",
            "url(javascript:alert(1))",
            "rgb(1,2,3)",
            "var(--x)",
            "#12345",
            "#1234567",
        ):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    validate_color(bad)


class TagTests(unittest.TestCase):
    def test_free_vocabulary(self):
        self.assertEqual(validate_tag("Kunde Meier"), "Kunde Meier")
        self.assertEqual(validate_tag("Vertrieb"), "Vertrieb")
        self.assertEqual(validate_tag("Sandkasten / Test"), "Sandkasten / Test")

    def test_whitespace_is_collapsed(self):
        self.assertEqual(validate_tag("  Kunde   Meier \n"), "Kunde Meier")

    def test_too_long_is_refused(self):
        with self.assertRaises(ValueError):
            validate_tag("x" * (TAG_MAX + 1))

    def test_control_characters_and_markup_refused(self):
        for bad in ("Kunde\x00Meier", "<b>Vertrieb</b>", "a\x1bb"):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    validate_tag(bad)


class ApplyTests(unittest.TestCase):
    def test_none_leaves_untouched_empty_removes(self):
        config = apply_appearance({}, icon="Truck", color="#4f46e5", tag="Vertrieb")
        self.assertEqual(config["avatar"], {"icon": "Truck", "color": "#4f46e5"})
        self.assertEqual(tag_of(config), "Vertrieb")

        # Nur die Farbe aendern — Symbol und Schlagwort bleiben.
        config = apply_appearance(config, color="rose")
        self.assertEqual(config["avatar"]["icon"], "Truck")
        self.assertEqual(tag_of(config), "Vertrieb")

        # Leerer Text entfernt. Ohne diesen Unterschied wuerde man ein einmal
        # gesetztes Schlagwort nie wieder los.
        config = apply_appearance(config, tag="")
        self.assertNotIn("tag", config)

    def test_other_config_keys_are_preserved(self):
        """Aussehen ist kosmetisch — es darf nichts anderes aus der Konfiguration
        verlieren. Sonst kostet ein Farbwechsel die Verantwortungsbereiche."""
        config = apply_appearance(
            {"role": "Buchhaltung", "proactive": {"responsibilities": ["a"]}},
            icon="Bot",
        )
        self.assertEqual(config["role"], "Buchhaltung")
        self.assertEqual(config["proactive"], {"responsibilities": ["a"]})

    def test_invalid_value_raises_before_anything_is_written(self):
        original = {"avatar": {"icon": "Bot", "color": "violet"}}
        with self.assertRaises(ValueError):
            apply_appearance(original, color="red; background: url(x)")
        self.assertEqual(original["avatar"]["color"], "violet")


if __name__ == "__main__":
    unittest.main()
