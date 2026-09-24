"""Das Frontend fragt nur bei sichtbarem Tab im Takt nach — und Freigaben kommen live.

Anlass 24.09.2026: Jede offene Ansicht pollte dauerhaft, auch in Hintergrund-Tabs.
Allein die Freigabe-Abfrage im Chat lief alle 3 s (20/min je offenem Chat). Alles
zaehlt gegen dasselbe Rate-Limit pro Nutzer (120/min, APIRateLimitMiddleware);
zusammen mit einem hakenden Zweitgeraet bekam JEDES Geraet des Nutzers 429.

Die Tests halten fest:
- der Helfer pausiert im Hintergrund und fragt beim Zurueckkommen sofort nach,
- die immer eingeblendeten und die Agenten-Ansichten benutzen ihn,
- die Freigabe-Abfrage im Chat laeuft nicht mehr im 3-s-Takt, sondern reagiert
  auf das Live-Ereignis `approval_request` — und das Backend sendet es weiterhin.

Das Repo hat keinen Frontend-Test-Runner; wie test_my_ai_credentials_ui.py wird
der Quelltext geprueft.
"""

import pathlib
import re
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[2]
FRONTEND = ROOT / "frontend" / "src"
ORCH = pathlib.Path(__file__).resolve().parents[1]


def _read(rel: str) -> str:
    return (FRONTEND / rel).read_text(encoding="utf-8")


# Ansichten, die dauerhaft oder auf der Agentenseite eingeblendet sind.
USERS = [
    "components/agents/chat.tsx",
    "app/agents/[id]/page.tsx",
    "hooks/use-agents.ts",
    "hooks/use-tasks.ts",
    "components/layout/sidebar.tsx",
    "components/layout/notification-bell.tsx",
    "components/agents/chat-overview.tsx",
    "components/agents/todo-tab.tsx",
    "components/agents/docker-apps-tab.tsx",
    "components/agents/agent-network-view.tsx",
    "components/agents/agent-speech-tab.tsx",
    "components/agents/computer-use-tab.tsx",
    "app/chat/layout.tsx",
    "app/approvals/page.tsx",
    "components/dashboard/system-status-bar.tsx",
]


class HelperTests(unittest.TestCase):
    def test_helper_skips_hidden_tabs_and_catches_up(self):
        src = _read("lib/visible-interval.ts")
        self.assertIn('document.visibilityState !== "hidden"', src)
        self.assertIn("if (isTabVisible()) fn();", src)  # Takt nur sichtbar
        self.assertIn('addEventListener("visibilitychange"', src)  # sofort nachholen
        self.assertIn('removeEventListener("visibilitychange"', src)  # sauber aufraeumen


class UsageTests(unittest.TestCase):
    def test_views_use_the_helper(self):
        for rel in USERS:
            with self.subTest(view=rel):
                src = _read(rel)
                self.assertIn('from "@/lib/visible-interval"', src)
                self.assertIn("setVisibleInterval(", src)

    def test_presence_heartbeat_is_left_alone(self):
        # Bewusst unveraendert: der Heartbeat meldet Anwesenheit — ihn im
        # Hintergrund abzuschalten, aendert, wohin Benachrichtigungen gehen.
        src = _read("components/auth/auth-guard.tsx")
        self.assertNotIn("setVisibleInterval", src)


class ApprovalLiveTests(unittest.TestCase):
    def test_chat_no_longer_polls_approvals_every_3s(self):
        src = _read("components/agents/chat.tsx")
        self.assertNotRegex(src, r"setInterval\(check,\s*3000\)")
        self.assertIn("setVisibleInterval(check, hasPendingApproval ? 5000 : 30000)", src)

    def test_chat_reacts_to_live_approval_event(self):
        src = _read("components/agents/chat.tsx")
        self.assertIn('"approval_request"', src)  # im ChatEvent-Typ
        self.assertRegex(
            src,
            re.compile(r'chatEvent\.type === "approval_request"\)\s*\{\s*//[^\n]*\n\s*approvalRecheckRef\.current\(\);'),
        )

    def test_backend_still_sends_the_live_event(self):
        # Vertrag: ohne dieses Ereignis kaemen neue Freigaben erst mit dem
        # 30-s-Rueckfall-Takt an.
        src = (ORCH / "app/api/approvals.py").read_text(encoding="utf-8")
        self.assertIn('"type": "approval_request"', src)
        self.assertIn('f"agent:{agent_id}:chat:response"', src)


if __name__ == "__main__":
    unittest.main()
