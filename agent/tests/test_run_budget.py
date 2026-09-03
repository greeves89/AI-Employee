"""Issue #628 Phase 2: ein gemeinsamer Deckel statt drei getrennter.

Aufgaben, Chat und Agent-Nachrichten laufen alle im selben Prozess
(``main.py`` startet sie per ``asyncio.gather``). Jeder Pool fuer sich gegen
das volle Container-Budget gerechnet ergab zusammen ein Vielfaches davon —
diese Tests belegen, dass ``RunBudget`` den gemeinsamen Zaehler durchsetzt
UND dass Chat trotzdem nie unbegrenzt hinter Aufgaben/Nachrichten warten
muss.
"""

import asyncio

import pytest

from app.run_budget import RunBudget, get_run_budget, reset_run_budget


@pytest.fixture(autouse=True)
def _reset_singleton():
    reset_run_budget()
    yield
    reset_run_budget()


class TestTheTotalIsNeverExceeded:
    @pytest.mark.asyncio
    async def test_a_third_task_slot_waits_for_a_free_place(self):
        """Budget=2 (kein Chat-Rest): ein dritter Platz muss auf einen der
        zwei ersten warten — das ist der ganze Sinn des gemeinsamen Topfs."""
        budget = RunBudget(total=2, chat_reserved=0)
        order: list[str] = []

        async def hold(name: str, release_after: asyncio.Event):
            async with budget.slot_for_task():
                order.append(f"{name}:in")
                await release_after.wait()
                order.append(f"{name}:out")

        gate_a, gate_b = asyncio.Event(), asyncio.Event()
        t_a = asyncio.ensure_future(hold("a", gate_a))
        t_b = asyncio.ensure_future(hold("b", gate_b))
        await asyncio.sleep(0.01)
        assert "a:in" in order and "b:in" in order

        gate_c = asyncio.Event()
        gate_c.set()  # c releases immediately once it gets a slot
        t_c = asyncio.ensure_future(hold("c", gate_c))
        await asyncio.sleep(0.01)
        # c must not have entered yet — both slots are still held by a and b
        assert "c:in" not in order

        gate_a.set()
        await t_a
        await asyncio.sleep(0.01)
        assert "c:in" in order  # freed slot went to c

        gate_b.set()
        await asyncio.gather(t_b, t_c)

    @pytest.mark.asyncio
    async def test_tasks_and_messages_share_one_pool_across_pool_boundaries(self):
        """Zwei Aufgaben UND eine Nachricht gegen ein Budget von 2 — die
        Nachricht darf erst rein, wenn eine Aufgabe fertig ist. Das ist genau
        das Szenario aus dem Konzept: drei Pools, EIN Zaehler."""
        budget = RunBudget(total=2, chat_reserved=0)
        active = 0
        peak = 0

        async def run():
            nonlocal active, peak
            async with budget.slot_for_task():
                active += 1
                peak = max(peak, active)
                await asyncio.sleep(0.02)
                active -= 1

        await asyncio.gather(run(), run(), run())
        assert peak == 2


class TestChatNeverStarvesForever:
    @pytest.mark.asyncio
    async def test_chat_gets_through_via_the_reserved_slot_while_tasks_hold_the_shared_pool(self):
        """Budget=2, 1 fuer Chat reserviert -> der gemeinsame Topf hat nur
        noch 1 Platz. Zwei lange Aufgaben besetzen ihn dauerhaft (>> die
        Testdauer) — Chat muss trotzdem sofort durchkommen, ueber den
        reservierten Platz, nicht ueber den blockierten gemeinsamen Topf."""
        budget = RunBudget(total=2, chat_reserved=1)
        assert budget.chat_reserved == 1

        blocker = asyncio.Event()  # bleibt die ganze Testdauer ungesetzt

        async def hog():
            async with budget.slot_for_task():
                await blocker.wait()

        hog_task = asyncio.ensure_future(hog())
        await asyncio.sleep(0.01)  # der einzige gemeinsame Platz ist jetzt belegt

        entered = asyncio.Event()

        async def chat_turn():
            async with budget.slot_for_chat():
                entered.set()

        chat = asyncio.ensure_future(chat_turn())
        await asyncio.wait_for(entered.wait(), timeout=1.0)  # darf NICHT haengen

        await chat
        blocker.set()
        await hog_task

    @pytest.mark.asyncio
    async def test_chat_prefers_the_shared_pool_when_it_has_room(self):
        """Ist der gemeinsame Topf frei, nimmt Chat daraus — der reservierte
        Platz bleibt fuer den Ernstfall unangetastet."""
        budget = RunBudget(total=3, chat_reserved=1)  # gemeinsamer Topf: 2

        async with budget.slot_for_chat():
            # Der reservierte Platz muss noch komplett frei sein.
            assert not budget._chat_only.locked()
            assert budget._shared._value == 1  # 2 - 1 genommen

    @pytest.mark.asyncio
    async def test_no_reservation_possible_with_a_budget_of_one(self):
        """Bei genau einem Platz insgesamt gibt es nichts zu reservieren —
        Chat teilt sich dann denselben einzigen Platz mit allen anderen."""
        budget = RunBudget(total=1, chat_reserved=1)
        assert budget.chat_reserved == 0
        assert budget.total == 1

        async with budget.slot_for_chat():
            async def try_task():
                async with budget.slot_for_task():
                    return "in"

            task = asyncio.ensure_future(try_task())
            await asyncio.sleep(0.01)
            assert not task.done()  # der einzige Platz ist von Chat belegt

        assert await task == "in"


class TestTheSingleton:
    def test_get_run_budget_builds_from_the_pids_budget(self, monkeypatch):
        import app.run_budget as rb

        monkeypatch.setattr(rb, "max_concurrent_runs", lambda **kw: 7)
        budget = get_run_budget()
        assert budget.total == 7

    def test_get_run_budget_is_reused_not_rebuilt(self, monkeypatch):
        import app.run_budget as rb

        monkeypatch.setattr(rb, "max_concurrent_runs", lambda **kw: 5)
        first = get_run_budget()
        monkeypatch.setattr(rb, "max_concurrent_runs", lambda **kw: 99)
        second = get_run_budget()
        assert first is second
        assert second.total == 5  # nicht neu gebaut, also der alte Wert
