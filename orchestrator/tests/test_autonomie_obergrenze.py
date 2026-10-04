"""Rollen begrenzen, wie selbststaendig ein Nutzer seinen Agenten machen darf.

29.09.2026: Mitglieder konnten ihren Agenten bis L4 stellen (handelt nach aussen
ohne Rueckfrage: E-Mail, externe APIs, git push, Kaeufe) und Sudo-Pakete bis
„Voller Root-Zugriff" setzen. Jetzt traegt jede Rolle eine Obergrenze
(``max_autonomy_level``); Standard: Admin/Manager unbegrenzt, Mitglied L3,
Betrachter und ohne Rolle L1. Eine eigene Rolle ohne Angabe erbt die Grenze ihrer
Grundrolle — sonst haette ein Mitglied mit Sonderrolle weniger Schranken.

    Pruefung                 Admin   Mitglied (L3)   Rolle ohne Angabe   Rolle "unbegrenzt"
    Stufe L3                  ja         ja              ja (erbt L3)          ja
    Stufe L4                  ja        nein             nein                  ja
    Matrix freier als L3      ja        nein             nein                  ja
    Sudo-Pakete               ja        nein             nein                  ja
    ungueltige Grenze "l9"    -     wie L1 (geschlossen, nicht offen)
"""
import unittest
from types import SimpleNamespace

from fastapi import HTTPException

from app.core import autonomy_matrix as am
from app.core import autonomie_grenze as ag
from app.models.user import UserRole

ADMIN = SimpleNamespace(id="a", role=UserRole.ADMIN, custom_role_id=None)
MITGLIED = SimpleNamespace(id="m", role=UserRole.MEMBER, custom_role_id=None)
BETRACHTER = SimpleNamespace(id="v", role=UserRole.VIEWER, custom_role_id=None)
SONDERROLLE = SimpleNamespace(id="s", role=UserRole.MEMBER, custom_role_id=7)


class _Db:
    def __init__(self, rollen_rechte=None):
        self.rollen_rechte = rollen_rechte

    async def get(self, model, key):
        return SimpleNamespace(permissions=self.rollen_rechte) if self.rollen_rechte is not None else None


def lauf(coro):
    import asyncio
    return asyncio.run(coro)


class ObergrenzeTests(unittest.TestCase):
    def grenze(self, user, rollen_rechte=None):
        return lauf(ag.autonomie_obergrenze(user, _Db(rollen_rechte)))

    def test_standardgrenzen(self):
        self.assertIsNone(self.grenze(ADMIN))
        self.assertEqual(self.grenze(MITGLIED), "l3")
        self.assertEqual(self.grenze(BETRACHTER), "l1")

    def test_sonderrolle_ohne_angabe_erbt_die_grundrolle(self):
        self.assertEqual(self.grenze(SONDERROLLE, {"max_agents": 3}), "l3")

    def test_sonderrolle_ausdruecklich_unbegrenzt(self):
        self.assertIsNone(self.grenze(SONDERROLLE, {"max_autonomy_level": None}))

    def test_sonderrolle_mit_eigener_grenze(self):
        self.assertEqual(self.grenze(SONDERROLLE, {"max_autonomy_level": "l2"}), "l2")

    def test_ungueltige_grenze_schliesst(self):
        self.assertEqual(self.grenze(SONDERROLLE, {"max_autonomy_level": "l9"}), "l1")


class StufeTests(unittest.TestCase):
    def pruefe(self, user, stufe, rollen_rechte=None):
        return lauf(ag.pruefe_stufe(user, _Db(rollen_rechte), stufe))

    def test_mitglied_bis_l3(self):
        for stufe in ("l1", "l2", "l3"):
            self.pruefe(MITGLIED, stufe)
        with self.assertRaises(HTTPException) as f:
            self.pruefe(MITGLIED, "l4")
        self.assertEqual(f.exception.status_code, 403)

    def test_admin_und_unbegrenzt_bis_l4(self):
        self.pruefe(ADMIN, "l4")
        self.pruefe(SONDERROLLE, "l4", {"max_autonomy_level": None})


class MatrixTests(unittest.TestCase):
    def pruefe(self, user, matrix):
        return lauf(ag.pruefe_matrix(user, _Db(), matrix))

    def test_voreinstellung_der_grenze_ist_erlaubt(self):
        self.pruefe(MITGLIED, am.matrix_for_level("l3"))
        self.pruefe(MITGLIED, am.matrix_for_level("l1"))

    def test_strenger_als_die_grenze_ist_erlaubt(self):
        matrix = dict(am.matrix_for_level("l3"))
        matrix["purchases"] = am.DENY
        self.pruefe(MITGLIED, matrix)

    def test_eine_freiere_zelle_reicht_fuer_ablehnung(self):
        matrix = dict(am.matrix_for_level("l3"))
        matrix["email_m365"] = am.ALLOW  # L3 fragt hier nach
        with self.assertRaises(HTTPException):
            self.pruefe(MITGLIED, matrix)

    def test_admin_darf_alles(self):
        self.pruefe(ADMIN, am.matrix_for_level("l4"))


class SudoTests(unittest.TestCase):
    def test_nur_ohne_grenze(self):
        lauf(ag.pruefe_sudo_pakete(ADMIN, _Db(), ["full_sudo"]))
        lauf(ag.pruefe_sudo_pakete(MITGLIED, _Db(), []))  # nichts setzen ist immer ok
        with self.assertRaises(HTTPException):
            lauf(ag.pruefe_sudo_pakete(MITGLIED, _Db(), ["full_sudo"]))


class AnlegenTests(unittest.TestCase):
    def test_neuer_agent_wird_auf_die_grenze_gekappt(self):
        self.assertEqual(lauf(ag.gekappte_stufe(BETRACHTER, _Db(), "l3")), "l1")
        self.assertEqual(lauf(ag.gekappte_stufe(MITGLIED, _Db(), "l3")), "l3")
        self.assertEqual(lauf(ag.gekappte_stufe(MITGLIED, _Db(), "l4")), "l3")
        self.assertEqual(lauf(ag.gekappte_stufe(ADMIN, _Db(), "l4")), "l4")


if __name__ == "__main__":
    unittest.main()
