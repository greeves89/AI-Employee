"""Der Preload war nach Zeilen gedeckelt, nie nach Zeichen.

Gemessen am 07.09.2026 an einem laufenden Agenten: 98.754 Zeichen (~24.700 Token) in
JEDEN Lauf, 70 Eintraege mit im Mittel 1.238 Zeichen; fuenf Laeufe sind an „Prompt is too
long" gestorben. Eintraege loeschen half nicht — die Auswahl fuellt sich auf feste 70
Zeilen auf, es rueckt nur der naechste nach. Diese Tests halten den Deckel je Eintrag fest
UND die Ausnahme davon: Zugangsdaten bleiben ungekuerzt.
"""
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.core.memory_preload import (
    MAX_CONTENT_CHARS,
    _clip,
    collect_preload,
)


def _mem(id_, content, category="learning", importance=5, key="k"):
    m = MagicMock()
    m.id, m.content, m.category, m.importance, m.key = id_, content, category, importance, key
    return m


def _db_with(high_imp=None, creds=None, learnings=None):
    db = MagicMock()
    queue = [high_imp or [], [], creds or [], learnings or []]

    async def execute(stmt, *a, **kw):
        result = MagicMock()
        items = queue.pop(0) if queue else []
        result.scalars.return_value.all.return_value = items
        return result

    db.execute = AsyncMock(side_effect=execute)
    return db


def test_clip_leaves_short_content_untouched():
    assert _clip("kurz") == "kurz"


def test_clip_respects_the_cap_including_its_marker():
    assert len(_clip("x" * 5000)) <= MAX_CONTENT_CHARS


def test_clip_marks_that_it_truncated():
    # Ohne den Hinweis haelt das Modell den Rest fuer die ganze Erinnerung.
    assert "memory_search" in _clip("x" * 5000)


def test_clip_survives_none():
    assert _clip(None) == ""


@pytest.mark.asyncio
async def test_critical_entries_are_clipped():
    db = _db_with(high_imp=[_mem(1, "y" * 4000)])
    out = await collect_preload(db, "agent-1")
    assert len(out["critical"][0]["content"]) <= MAX_CONTENT_CHARS


@pytest.mark.asyncio
async def test_recent_learnings_are_clipped():
    db = _db_with(learnings=[_mem(2, "y" * 4000)])
    out = await collect_preload(db, "agent-1")
    assert len(out["recent_learnings"][0]["content"]) <= MAX_CONTENT_CHARS


@pytest.mark.asyncio
async def test_credentials_are_never_clipped():
    # Ein abgeschnittener Schluessel ist nicht weniger Kontext, sondern ein falscher
    # Schluessel — der Agent wuerde sich damit anmelden und raetseln, warum es 401 gibt.
    secret = "k" * 4000
    db = _db_with(creds=[_mem(3, secret, category="credentials")])
    out = await collect_preload(db, "agent-1")
    assert out["credentials"][0]["content"] == secret


@pytest.mark.asyncio
async def test_preload_stays_far_below_the_old_size():
    # 70 Eintraege wie im Feld gemessen, jeder ueberlang: frueher ~98k Zeichen.
    db = _db_with(high_imp=[_mem(i, "y" * 1238) for i in range(70)])
    out = await collect_preload(db, "agent-1")
    total = sum(len(m["content"]) for m in out["critical"])
    assert total <= 70 * MAX_CONTENT_CHARS
