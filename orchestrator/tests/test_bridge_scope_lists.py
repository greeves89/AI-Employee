"""Freigabelisten: die Capability-Gruppe sagt OB, die Freigabeliste sagt WOMIT.

Hintergrund: Die Tray-App kennt seit jeher ein Feld ``allowed_paths``, zeigt es
im Berechtigungs-Dialog an und speichert es — es erreicht aber weder Bridge noch
Server. Eine Zusage ohne Durchsetzung. ``allowed_apps``/``allowed_domains``
werden deshalb ausschliesslich serverseitig geprueft, dort wo auch die
Capabilities geprueft werden.
"""

import unittest

from app.api.computer_use import _app_in_scope, _scope_violation, _url_in_scope


class AppScopeTests(unittest.TestCase):
    def test_none_means_unrestricted(self):
        """Bestandssitzungen ohne Liste duerfen weiterarbeiten wie bisher."""
        self.assertTrue(_app_in_scope("Excel", None))

    def test_empty_list_means_nothing_allowed(self):
        """Leere Liste ist eine Aussage, kein fehlender Wert."""
        self.assertFalse(_app_in_scope("Excel", []))

    def test_matching_ignores_case_and_platform_suffix(self):
        """Der Nutzer traegt 'Excel' ein; das Modell schickt je nach Plattform
        'Excel.app' oder 'EXCEL.EXE'."""
        for sent in ("Excel", "excel", "Excel.app", "EXCEL.EXE"):
            with self.subTest(sent=sent):
                self.assertTrue(_app_in_scope(sent, ["Excel"]))

    def test_other_app_is_rejected(self):
        self.assertFalse(_app_in_scope("Terminal", ["Excel", "Word"]))


class UrlScopeTests(unittest.TestCase):
    def test_none_means_unrestricted(self):
        self.assertTrue(_url_in_scope("https://example.com/x", None))

    def test_subdomain_is_covered(self):
        self.assertTrue(_url_in_scope("https://a.example.com/x", ["example.com"]))

    def test_exact_host_is_covered(self):
        self.assertTrue(_url_in_scope("https://example.com/x", ["example.com"]))

    def test_suffix_trick_is_rejected(self):
        """`example.com.angreifer.tld` endet NICHT auf der Freigabe — der Host
        wird verglichen, nicht die Zeichenkette."""
        self.assertFalse(
            _url_in_scope("https://example.com.angreifer.tld/x", ["example.com"])
        )

    def test_prefix_trick_is_rejected(self):
        """`boeseexample.com` darf nicht als `example.com` durchgehen."""
        self.assertFalse(_url_in_scope("https://boeseexample.com/x", ["example.com"]))

    def test_garbage_url_is_rejected_when_a_list_exists(self):
        self.assertFalse(_url_in_scope("nicht-mal-eine-url", ["example.com"]))


class ScopeViolationTests(unittest.TestCase):
    def test_open_app_outside_the_list_is_refused(self):
        session = {"allowed_apps": ["Excel"], "allowed_domains": None}
        msg = _scope_violation("open_app", {"app": "Terminal"}, session)
        self.assertIsNotNone(msg)
        self.assertIn("Terminal", msg)

    def test_open_app_inside_the_list_passes(self):
        session = {"allowed_apps": ["Excel"], "allowed_domains": None}
        self.assertIsNone(_scope_violation("open_app", {"app": "Excel"}, session))

    def test_browser_navigate_is_scoped_by_domain(self):
        session = {"allowed_apps": None, "allowed_domains": ["intern.example"]}
        self.assertIsNone(
            _scope_violation("browser_navigate", {"url": "https://intern.example/a"}, session)
        )
        self.assertIsNotNone(
            _scope_violation("browser_navigate", {"url": "https://youtube.com"}, session)
        )

    def test_open_url_is_scoped_too(self):
        """Der alte, blinde Weg darf die Freigabeliste nicht umgehen."""
        session = {"allowed_apps": None, "allowed_domains": ["intern.example"]}
        self.assertIsNotNone(
            _scope_violation("open_url", {"url": "https://youtube.com"}, session)
        )

    def test_unscoped_action_is_untouched(self):
        """Ein Screenshot hat kein Ziel, das eingeschraenkt werden koennte."""
        session = {"allowed_apps": [], "allowed_domains": []}
        self.assertIsNone(_scope_violation("screenshot", {}, session))

    def test_name_parameter_is_honoured(self):
        """Die Bridge akzeptiert `app` ODER `name` — die Pruefung muss beide
        sehen, sonst umgeht `name` die Freigabeliste."""
        session = {"allowed_apps": ["Excel"], "allowed_domains": None}
        self.assertIsNotNone(_scope_violation("open_app", {"name": "Terminal"}, session))


if __name__ == "__main__":
    unittest.main()
