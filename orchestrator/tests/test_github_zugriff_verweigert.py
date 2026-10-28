"""GitHub verweigert den Zugriff: Die Meldung muss sagen, was am Schlüssel fehlt.

Nachgestellt nach einer echten Antwort: Ein fein granulierter Token sah das Repo
(Metadata), durfte aber keine Issues lesen — GitHub antwortete 403 „Resource not
accessible by personal access token“ mit ``x-accepted-github-permissions:
issues=read``. Die Oberflaeche zeigte nur „GitHub 403“ — und weil der Endpunkt
dafuer 502 sendete, ersetzte Cloudflare selbst das durch seine Fehlerseite.
"""

import unittest
from unittest.mock import AsyncMock, patch

import httpx
from fastapi import HTTPException

from app.api import teams as api
from app.core import projekt_git
from app.core.githost.github import GitHubHostProvider
from app.models.team import Team
from tests.test_projekte import MITGLIED, Basis


def _antwort(status, headers=None, json=None):
    return lambda request: httpx.Response(status, headers=headers or {}, json=json or {})


class Meldung(unittest.IsolatedAsyncioTestCase):
    async def _fehler(self, handler) -> str:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as c:
            with self.assertRaises(RuntimeError) as e:
                await GitHubHostProvider().list_open_issues(c, "tok", "o/r", 10)
        return str(e.exception)

    async def test_fehlendes_recht_wird_benannt(self):
        text = await self._fehler(_antwort(403, {"x-accepted-github-permissions": "issues=read"},
                                           {"message": "Resource not accessible by personal access token"}))
        self.assertIn("Issues: Read", text)
        self.assertIn("Repository permissions", text)
        self.assertIn("Resource not accessible", text)

    async def test_sso_und_ungueltiger_schluessel(self):
        self.assertIn("SSO", await self._fehler(_antwort(403, {"x-github-sso": "required; url=https://example.com"})))
        self.assertIn("ungültig oder abgelaufen", await self._fehler(_antwort(401)))

    async def test_abfragegrenze_bleibt_eigene_meldung(self):
        text = await self._fehler(_antwort(403, {"x-ratelimit-remaining": "0"}))
        self.assertIn("Abfragegrenze", text)


class Endpunkt(Basis):
    async def test_fehlgeschlagener_abgleich_ist_kein_502(self):
        class _Verweigert:
            async def list_open_issues(self, *a):
                raise RuntimeError("Dem Schlüssel fehlt das Recht Issues: Read")

        async with self.Session() as db:
            team = await db.get(Team, "p1")
            team.repo = "o/r"
            await db.commit()
            with patch("app.core.projekt_git.zugang", new=AsyncMock(return_value=("tok", _Verweigert()))), \
                    self.assertRaises(HTTPException) as e:
                await api.repo_abgleichen("p1", user=MITGLIED, db=db, redis=None)
        self.assertEqual(e.exception.status_code, 424)          # Cloudflare reicht ihn durch
        self.assertIn("Issues: Read", e.exception.detail)
        self.assertNotIn("antwortet nicht", e.exception.detail)  # der Server hat geantwortet

    async def test_netzfehler_heisst_antwortet_nicht(self):
        class _Weg:
            async def list_open_issues(self, *a):
                raise httpx.ConnectError("keine Verbindung")

        async with self.Session() as db:
            team = await db.get(Team, "p1")
            team.repo = "o/r"
            with self.assertRaises(projekt_git.GitFehler) as e:
                await projekt_git.abgleichen(db, team, "tok", _Weg(), client=None)
        self.assertIn("antwortet nicht", str(e.exception))


if __name__ == "__main__":
    unittest.main()


class CiOhneCheckRecht(unittest.IsolatedAsyncioTestCase):
    """Fein granulierte Tokens duerfen keine Check-Runs lesen (403, ``checks=read``) —
    der CI-Stand kommt dann aus den Workflow-Laeufen und dem Commit-Status."""

    async def _stand(self, laeufe, status=None) -> str:
        def handler(request):
            pfad = request.url.path
            if pfad.endswith("/check-runs"):
                return httpx.Response(403, headers={"x-accepted-github-permissions": "checks=read"},
                                      json={"message": "Resource not accessible by personal access token"})
            if pfad.endswith("/actions/runs"):
                self.assertEqual(request.url.params["head_sha"], "abc")
                return httpx.Response(200, json={"workflow_runs": laeufe})
            if pfad.endswith("/status"):
                return httpx.Response(200, json=status or {"state": "pending", "statuses": []})
            return httpx.Response(404, json={})
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as c:
            return await GitHubHostProvider().checks_state(c, "tok", "o/r", "abc")

    async def test_stand_aus_workflow_laeufen(self):
        self.assertEqual(await self._stand([{"status": "in_progress"}]), "ci_laeuft")
        self.assertEqual(await self._stand([{"status": "completed", "conclusion": "success"}]), "ci_gruen")
        self.assertEqual(await self._stand([{"status": "completed", "conclusion": "success"},
                                            {"status": "completed", "conclusion": "failure"}]), "ci_rot")

    async def test_externe_ci_ueber_commit_status_und_ohne_alles_offen(self):
        self.assertEqual(await self._stand([], {"state": "failure", "statuses": [{"id": 1}]}), "ci_rot")
        self.assertEqual(await self._stand([], {"state": "success", "statuses": [{"id": 1}]}), "ci_gruen")
        self.assertEqual(await self._stand([]), "offen")
