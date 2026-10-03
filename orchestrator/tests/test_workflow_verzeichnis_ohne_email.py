"""Die Nutzerliste der Workflow-Freigabe folgt derselben Regel wie /apps/directory:
Nicht-Admins sehen keine fremden E-Mail-Adressen, Admins schon (eine Stelle:
app/core/nutzer_verzeichnis.py)."""

import asyncio
import unittest
from types import SimpleNamespace

from app.core.nutzer_verzeichnis import verzeichnis
from app.models.user import UserRole


class _Db:
    def __init__(self, rows):
        self._rows = rows

    async def execute(self, _stmt):
        return SimpleNamespace(all=lambda: self._rows)


ROWS = [("u1", "Anna", "anna@example.com"), ("u2", "Ben", "ben@example.com"), ("u3", "Cem", "cem@example.com")]


class VerzeichnisTests(unittest.TestCase):
    def test_nicht_admin_ohne_email_und_ohne_sich_selbst(self):
        user = SimpleNamespace(id="u1", role=UserRole.MEMBER if hasattr(UserRole, "MEMBER") else "member")
        out = asyncio.run(verzeichnis(_Db(ROWS), user))
        self.assertEqual([u["id"] for u in out], ["u2", "u3"])
        self.assertTrue(all("email" not in u for u in out))

    def test_admin_sieht_email(self):
        user = SimpleNamespace(id="u1", role=UserRole.ADMIN)
        out = asyncio.run(verzeichnis(_Db(ROWS), user))
        self.assertEqual(out[0]["email"], "ben@example.com")

    def test_beide_endpunkte_nutzen_die_eine_stelle(self):
        from pathlib import Path
        base = Path(__file__).resolve().parents[1] / "app/api"
        for datei in ("workflows.py", "apps_overview.py"):
            self.assertIn("nutzer_verzeichnis import verzeichnis", (base / datei).read_text())


if __name__ == "__main__":
    unittest.main()
