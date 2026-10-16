"""Der Gesprächsfaden reißt nicht ab (app/chat_uebertrag).

Bis v1.365: eine neue Sitzung (zu langer Verlauf, Modellwechsel, 7 Tage Ruhe)
begann bei null; Codex-Chats setzten nicht einmal die vorige Nachricht fort.
Hier das Verhalten mit Attrappen für Redis, Orchestrator und CLI.
"""

import json
import time

import pytest

from app import chat_uebertrag as cu


class FakeRedis:
    def __init__(self):
        self.store: dict[str, str] = {}

    async def get(self, key):
        return self.store.get(key)

    async def set(self, key, value):
        self.store[key] = value

    async def setex(self, key, _ttl, value):
        self.store[key] = value

    async def delete(self, key):
        self.store.pop(key, None)

    async def publish(self, *_a, **_k):
        pass


def _zug(rolle, inhalt, vor_sekunden=0):
    from datetime import datetime, timedelta, timezone
    zeit = datetime.now(timezone.utc) - timedelta(seconds=vor_sekunden)
    return {"role": rolle, "content": inhalt, "timestamp": zeit.isoformat()}


# ── Übertrag ─────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_uebertrag_mit_uebergabe_und_juengsten_nachrichten():
    r = FakeRedis()
    await cu.uebergabe_speichern(r, "a1", "webapp:s1", "## Ziel\nAngebot fertig machen")
    zuege = [_zug("user", "Bitte immer brutto rechnen", 300), _zug("assistant", "Mache ich", 290),
             _zug("user", "Und jetzt?", 1)]
    block = await cu.uebertrag_block(r, "a1", "webapp:s1", "Und jetzt?", zuege=zuege)
    assert "Angebot fertig machen" in block
    assert "Bitte immer brutto rechnen" in block
    assert "chat_search" in block
    # Die eingehende Nachricht folgt unten — nicht doppelt im Übertrag.
    assert block.count("Und jetzt?") == 0


@pytest.mark.asyncio
async def test_kein_uebertrag_ohne_vorgeschichte():
    assert await cu.uebertrag_block(FakeRedis(), "a1", "webapp:neu", "Hallo", zuege=[]) == ""


@pytest.mark.asyncio
async def test_neuanfang_laesst_alten_verlauf_und_uebergabe_draussen():
    r = FakeRedis()
    await cu.uebergabe_speichern(r, "a1", "telegram:7", "alte Übergabe")
    alt = [_zug("user", "alte Absprache", 600)]
    await cu.neuanfang_markieren(r, "a1", "telegram:7")
    neu = [_zug("user", "frische Nachricht", 0)]
    time.sleep(0.01)
    block = await cu.uebertrag_block(r, "a1", "telegram:7", "nächste", zuege=alt + neu)
    assert "alte Absprache" not in block
    assert "alte Übergabe" not in block


def test_juengste_im_zeichenbudget_in_zeitlicher_reihenfolge():
    zuege = [_zug("user", f"Nachricht {i} " + "x" * 3000) for i in range(20)]
    auswahl = cu._juengste_woertlich(zuege, "neu")
    assert sum(len(z["content"]) for z in auswahl) <= cu.UEBERTRAG_ZEICHEN
    assert auswahl[-1]["content"].startswith("Nachricht 19")
    assert all(len(z["content"]) <= cu.NACHRICHT_ZEICHEN + 4 for z in auswahl)


def test_kanal_auf_datenbank_sitzung():
    assert cu.chat_sitzung_der_quelle("webapp:abc") == "abc"
    assert cu.chat_sitzung_der_quelle("ios:abc") == "abc"
    assert cu.chat_sitzung_der_quelle("telegram:42") == "telegram:42"


# ── Consumer: Sitzung ohne Ablauf, Wechsel löscht, Neuanfang markiert ────────

@pytest.mark.asyncio
async def test_sitzung_laeuft_nicht_ab_und_wechsel_loescht():
    from app.chat_consumer import ChatConsumer

    consumer = ChatConsumer("a1")
    consumer.redis = FakeRedis()
    h = await consumer._get_or_create_handler("webapp:s1", "claude-sonnet-5")
    h.session_id = "sess-1"
    await consumer._persist_session("webapp:s1", h, "claude-sonnet-5")
    assert json.loads(consumer.redis.store["agent:a1:claude_session:webapp:s1"])["session_id"] == "sess-1"
    h.session_id = None  # Übergabe oder zu langer Verlauf
    await consumer._persist_session("webapp:s1", h, "claude-sonnet-5")
    assert "agent:a1:claude_session:webapp:s1" not in consumer.redis.store


@pytest.mark.asyncio
async def test_reset_markiert_neuanfang():
    from app.chat_consumer import ChatConsumer

    consumer = ChatConsumer("a1")
    consumer.redis = FakeRedis()
    await cu.uebergabe_speichern(consumer.redis, "a1", "telegram:7", "alt")
    await consumer._get_or_create_handler("telegram:7", "claude-sonnet-5")
    await consumer._reset_handler("telegram:7")
    assert await cu.uebergabe_laden(consumer.redis, "a1", "telegram:7") == ""
    assert "agent:a1:chat_neuanfang:telegram:7" in consumer.redis.store


def test_nach_verdichtung_gehen_regeln_wieder_mit(monkeypatch):
    from app import runner_hooks
    from app.chat_consumer import ChatConsumer

    monkeypatch.setattr(runner_hooks, "get_approval_rules_prefix", lambda: "[REGELN]\n")
    monkeypatch.setattr(runner_hooks, "fachanleitungen", lambda *a, **k: "")
    monkeypatch.setattr(ChatConsumer, "_skills_prefix", lambda self, t: "[SKILLS]\n")
    consumer = ChatConsumer("a1")

    class H:
        session_id = "laeuft"
        _last_rules_prefix = "[REGELN]\n"
        _kompaktiert = False
        _fachanleitungen = set()

    h = H()
    ohne = consumer._prepare_text("hallo", None, "webapp", h)
    assert "[REGELN]" not in ohne and "[SKILLS]" not in ohne
    h._kompaktiert = True
    mit = consumer._prepare_text("hallo", None, "webapp", h)
    assert "[REGELN]" in mit and "[SKILLS]" in mit
    assert h._kompaktiert is False


# ── Claude: Übergabe am Ende eines Zugs ─────────────────────────────────────

class _Publisher:
    def __init__(self):
        self.ereignisse = []
        self.last_activity_at = 0.0

    async def publish_chat(self, message_id, art, daten):
        self.ereignisse.append((art, daten))


@pytest.mark.asyncio
async def test_uebergabe_sichert_stand_und_wechselt_die_sitzung(monkeypatch):
    from app import chat_handler as ch

    pub = _Publisher()
    h = ch.ChatHandler(pub)
    h.session_id = "sess-voll"
    h._kontext_tokens = 200_000
    abgelegt = []

    async def _ablegen(t):
        abgelegt.append(t)

    h.uebergabe_ablegen = _ablegen

    async def _cli(self_, message_id, text, model):
        assert "Gesprächsübergabe" in text
        await self_.log_publisher.publish_chat(message_id, "text", {"text": "## Ziel\nX"})
        return {"status": "completed", "text": "## Ziel\nX", "cost_usd": 0.5, "input_tokens": 10}

    monkeypatch.setattr(ch.ChatHandler, "_execute_cli", _cli)
    assert h._uebergabe_faellig("claude-sonnet-5")
    ergebnis = {"status": "completed", "cost_usd": 1.0, "input_tokens": 5}
    await h._uebergeben("m1", "claude-sonnet-5", ergebnis)

    assert abgelegt == ["## Ziel\nX"]
    assert h.session_id is None
    assert ergebnis["cost_usd"] == 1.5 and ergebnis["input_tokens"] == 15
    # Der Nutzer sieht nur den Hinweis, nicht den Text der Übergabe.
    assert [a for a, _ in pub.ereignisse] == ["system"]
    assert pub.last_activity_at > 0


@pytest.mark.asyncio
async def test_gescheiterte_uebergabe_laesst_die_sitzung_stehen(monkeypatch):
    from app import chat_handler as ch

    h = ch.ChatHandler(_Publisher())
    h.session_id = "sess-voll"
    h._kontext_tokens = 200_000

    async def _ablegen(t):
        raise AssertionError("darf nicht gespeichert werden")

    h.uebergabe_ablegen = _ablegen

    async def _cli(self_, message_id, text, model):
        return {"status": "error", "error": "kaputt"}

    monkeypatch.setattr(ch.ChatHandler, "_execute_cli", _cli)
    await h._uebergeben("m1", "claude-sonnet-5", {"status": "completed"})
    assert h.session_id == "sess-voll"


def test_schwelle_hoechstens_60_prozent_und_abschaltbar(monkeypatch):
    from app import config

    monkeypatch.setattr(config.settings, "chat_uebergabe_tokens", 150_000)
    assert config.chat_uebergabe_schwelle("claude-sonnet-5") == 150_000  # Fenster 1 Mio.
    assert config.chat_uebergabe_schwelle("claude-opus-4-5") == 120_000  # 60 % von 200k
    monkeypatch.setattr(config.settings, "chat_uebergabe_tokens", 0)
    assert config.chat_uebergabe_schwelle("claude-sonnet-5") > 10**12


@pytest.mark.asyncio
async def test_notfall_reset_nimmt_den_uebertrag_mit(monkeypatch):
    from app import chat_handler as ch

    h = ch.ChatHandler(_Publisher())
    h.session_id = "sess-alt"
    gesehen = []

    async def _laden():
        return "[ÜBERTRAG]\n"

    h.uebertrag_laden = _laden

    async def _cli(self_, message_id, text, model):
        gesehen.append(text)
        if len(gesehen) == 1:
            return {"status": "error", "error": "Prompt is too long: 1200000 tokens > 1000000"}
        return {"status": "completed", "text": "ok"}

    monkeypatch.setattr(ch.ChatHandler, "_execute_cli", _cli)
    monkeypatch.setattr(ch, "report_result_status", lambda r: _nichts())
    await h._run_turn_with_retries("m1", "folgezug", "claude-sonnet-5", fresh_text="ERSTE NACHRICHT")
    assert gesehen[1] == "[ÜBERTRAG]\nERSTE NACHRICHT"


async def _nichts():
    return None


# ── Codex: ein Faden je Kanal ────────────────────────────────────────────────

class _FakeRunner:
    aufrufe: list = []
    antworten: list = []

    def __init__(self, _pub):
        self.thread_id = None
        self._nutzung_stand = {}

    async def _run_codex(self, message_id, text, model, stream, resume=False, resume_id=None):
        _FakeRunner.aufrufe.append({"text": text, "resume_id": resume_id})
        antwort = _FakeRunner.antworten.pop(0)
        self.thread_id = antwort.get("_faden")
        return {k: v for k, v in antwort.items() if k != "_faden"}

    async def interrupt(self):
        pass


@pytest.mark.asyncio
async def test_codex_setzt_den_faden_ueber_nachrichten_fort(monkeypatch):
    from app import codex_runner as cr

    _FakeRunner.aufrufe, _FakeRunner.antworten = [], [
        {"status": "completed", "text": "eins", "_faden": "t-1"},
        {"status": "completed", "text": "zwei", "_faden": "t-1"},
    ]
    monkeypatch.setattr(cr, "CodexAgentRunner", _FakeRunner)
    monkeypatch.setattr(cr, "report_result_status", lambda r: _nichts())
    h = cr.CodexChatHandler(_Publisher())
    await h.handle_message("m1", "erste")
    assert h.session_id == "t-1"
    await h.handle_message("m2", "zweite")
    assert [a["resume_id"] for a in _FakeRunner.aufrufe] == [None, "t-1"]


@pytest.mark.asyncio
async def test_codex_faden_weg_neuer_faden_mit_uebertrag(monkeypatch):
    from app import codex_runner as cr

    _FakeRunner.aufrufe, _FakeRunner.antworten = [], [
        {"status": "error", "error": "no rollout found for thread"},
        {"status": "completed", "text": "weiter", "_faden": "t-2"},
    ]
    monkeypatch.setattr(cr, "CodexAgentRunner", _FakeRunner)
    monkeypatch.setattr(cr, "report_result_status", lambda r: _nichts())
    monkeypatch.setattr(cr, "_codex_auth_problem", lambda: "")
    pub = _Publisher()
    h = cr.CodexChatHandler(pub)
    h.session_id = "t-alt"
    h.fresh_session_text = "ERSTE NACHRICHT"

    async def _laden():
        return "[ÜBERTRAG]\n"

    h.uebertrag_laden = _laden
    await h.handle_message("m1", "folgezug")
    assert _FakeRunner.aufrufe[1]["text"] == "[ÜBERTRAG]\nERSTE NACHRICHT"
    assert _FakeRunner.aufrufe[1]["resume_id"] is None
    assert h.session_id == "t-2"
    assert any(a == "system" for a, _ in pub.ereignisse)


@pytest.mark.asyncio
async def test_codex_abgelaufene_anmeldung_kein_neuer_faden(monkeypatch):
    from app import codex_runner as cr

    _FakeRunner.aufrufe, _FakeRunner.antworten = [], [
        {"status": "error", "error": "Codex-Anmeldedaten abgelaufen — bitte neu anmelden"},
    ]
    monkeypatch.setattr(cr, "CodexAgentRunner", _FakeRunner)
    monkeypatch.setattr(cr, "report_result_status", lambda r: _nichts())
    monkeypatch.setattr(cr, "_codex_auth_problem", lambda: "abgelaufen")
    h = cr.CodexChatHandler(_Publisher())
    h.session_id = "t-1"
    ergebnis = await h.handle_message("m1", "hallo")
    assert len(_FakeRunner.aufrufe) == 1
    assert ergebnis["status"] == "error"
    assert h.session_id == "t-1"
