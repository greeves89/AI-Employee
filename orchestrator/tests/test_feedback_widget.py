"""Feedback-Widget ("Feedback-Gedöns"): MD-Store, Attribution, Issue-Spiegelung.

Die MD-Datei in FEEDBACK_DIR ist die Source of Truth; der DB-Eintrag ist die
Sicht fuer die bestehende Admin-Liste. Die kritischen Eigenschaften:
- Attribution kommt aus der Session, ein Body-User wird ignoriert (Confused-Deputy).
- Screenshots sind hart gedeckelt und scheitern nie das Text-Feedback.
- Die Issue-Spiegelung ist best-effort — ein API-Fehler verliert kein Feedback.
- Kein Modellname im Code: die RE-Rueckfrage laeuft ueber den config-getriebenen
  LLM-Pfad, der Prompt liegt als Datei neben dem Code.
"""

import base64
from pathlib import Path
from types import SimpleNamespace

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.api import feedback as fb
from app.config import settings
from app.models.base import Base
from app.models.feedback import Feedback
from app.schemas.feedback import FeedbackWidgetIn

# 1x1-PNG als kleinster gueltiger Screenshot
_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)
_PNG_DATAURL = "data:image/png;base64," + base64.b64encode(_PNG).decode()


@pytest.fixture
def feedback_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "feedback_dir", str(tmp_path))
    return tmp_path


# ------------------------------------------------------------------ MD-Store


class TestMdStore:
    def test_frontmatter_traegt_alle_pflichtfelder(self, feedback_dir):
        meta = {
            "id": "20260813_120000_yannik", "user": "Yannik", "seite": "/agents",
            "element": "Neuer Agent", "selector": "div > button:nth-of-type(2)",
            "sentiment": "wunsch", "kategorie": "feature", "screenshot": "", "zeit": "2026-08-13T12:00:00",
        }
        md = fb.build_md(meta, [{"role": "user", "text": "Bitte einen Duplizieren-Button."}])
        fm = md.split("---")[1]
        for key in ("user", "seite", "element", "selector", "sentiment", "kategorie", "screenshot", "zeit"):
            assert f"{key}: " in fm
        assert '"Yannik"' in fm
        assert "Bitte einen Duplizieren-Button." in md

    def test_chatverlauf_und_screenshot_embed(self, feedback_dir):
        meta = {"id": "x", "user": "Yannik", "sentiment": "negativ", "screenshot": "x.png"}
        md = fb.build_md(meta, [
            {"role": "user", "text": "Der Button ist zu klein."},
            {"role": "bot", "text": "Auf welchem Geraet?"},
            {"role": "user", "text": "iPhone."},
        ])
        assert "**Yannik:** Der Button ist zu klein." in md
        assert "**Bot (Rückfrage):** Auf welchem Geraet?" in md
        assert "![Screenshot](x.png)" in md

    def test_write_md_landet_im_feedback_dir(self, feedback_dir):
        name = fb.write_md("20260813_120000_yannik", {"id": "20260813_120000_yannik", "user": "Y"}, [])
        assert (feedback_dir / name).exists()

    def test_fid_ist_traversal_sicher(self, feedback_dir):
        # Ein boeser fid darf das FEEDBACK_DIR nie verlassen.
        name = fb.write_md("../../etc/passwd", {"id": "x", "user": "Y"}, [])
        assert "/" not in name and ".." not in name
        assert (feedback_dir / name).exists()

    def test_issue_url_wird_ins_frontmatter_geschrieben(self, feedback_dir):
        md = fb.build_md({"id": "x", "user": "Y", "issue": "https://github.com/o/r/issues/1"}, [])
        assert 'issue: "https://github.com/o/r/issues/1"' in md
        assert "[GitHub-Issue](https://github.com/o/r/issues/1)" in md


class TestScreenshot:
    def test_gueltiger_dataurl_wird_png(self, feedback_dir):
        name = fb.save_screenshot("fid1", _PNG_DATAURL)
        assert name == "fid1.png"
        assert (feedback_dir / name).read_bytes() == _PNG

    def test_zu_gross_wird_verworfen(self, feedback_dir, monkeypatch):
        monkeypatch.setattr(fb, "MAX_SCREENSHOT_BYTES", 8)
        assert fb.save_screenshot("fid2", _PNG_DATAURL) is None
        assert not (feedback_dir / "fid2.png").exists()

    def test_kaputtes_base64_scheitert_leise(self, feedback_dir):
        assert fb.save_screenshot("fid3", "data:image/png;base64,%%%nicht-base64%%%") is None

    def test_ohne_screenshot_kein_png(self, feedback_dir):
        assert fb.save_screenshot("fid4", None) is None


# ------------------------------------------------------------- Attribution


class TestAttribution:
    def test_schema_kennt_kein_user_feld(self):
        # Ein manipulierter Body-User darf nie beim Backend ankommen: weder das
        # Eingabeschema noch der Kontext akzeptieren ein user-Feld.
        body = FeedbackWidgetIn.model_validate({
            "messages": [{"role": "user", "text": "hi"}],
            "context": {"page": "/x", "user": "admin", "element_label": "Knopf"},
            "user": "admin",
        })
        assert not hasattr(body, "user")
        assert "user" not in body.context.model_dump()

    def test_save_endpoint_liest_user_nur_aus_der_session(self):
        # Regressionsschutz auf Code-Ebene: der Save-Pfad zieht den Namen aus
        # dem Session-User (require_auth), nirgends aus dem Request-Body.
        import inspect
        src = inspect.getsource(fb.feedback_widget_save)
        assert 'getattr(user, "name"' in src
        assert "body.user" not in src


# ----------------------------------------------------- Save-Flow (End-to-End)


@pytest_asyncio.fixture
async def db():
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as conn:
        await conn.run_sync(
            lambda c: Base.metadata.create_all(c, tables=[Base.metadata.tables["feedback"]])
        )
    Session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with Session() as session:
        yield session
    await engine.dispose()


def _session_user():
    return SimpleNamespace(id="u-1", name="Yannik", email="yannik@example.com")


@pytest.mark.asyncio
async def test_save_schreibt_md_und_db_mit_session_user(db, feedback_dir):
    body = FeedbackWidgetIn.model_validate({
        "messages": [
            {"role": "user", "text": "Die Kachel springt beim Laden."},
            {"role": "bot", "text": "Auf welcher Seite genau?"},
            {"role": "user", "text": "Im Chat."},
        ],
        "context": {
            "page": "/chat", "element_label": "Live-Kachel", "selector": "main > div",
            "sentiment": "negativ", "kategorie": "bug",
            # Angriffsversuch: gefaelschter User im Kontext — muss ignoriert werden.
            "user": "boese@example.com",
        },
        "screenshot": _PNG_DATAURL,
    })
    result = await fb.feedback_widget_save(body=body, user=_session_user(), db=db, service=None)

    assert result["ok"] is True
    md_path = feedback_dir / f"{result['id']}.md"
    assert md_path.exists()
    md = md_path.read_text(encoding="utf-8")
    assert 'user: "Yannik"' in md
    assert "boese@example.com" not in md
    assert (feedback_dir / f"{result['id']}.png").exists()

    row = (await db.execute(select(Feedback))).scalar_one()
    assert row.user_id == "u-1"
    assert row.user_name == "Yannik"
    assert row.title == "Die Kachel springt beim Laden."
    assert row.page == "/chat"
    assert row.element_label == "Live-Kachel"
    assert row.sentiment == "negativ"
    assert row.md_file == f"{result['id']}.md"
    assert row.screenshot_file == f"{result['id']}.png"


@pytest.mark.asyncio
async def test_save_ohne_text_wird_abgelehnt(db, feedback_dir):
    from fastapi import HTTPException
    body = FeedbackWidgetIn.model_validate({"messages": [], "context": {}})
    with pytest.raises(HTTPException) as exc:
        await fb.feedback_widget_save(body=body, user=_session_user(), db=db, service=None)
    assert exc.value.status_code == 422


@pytest.mark.asyncio
async def test_kaputter_screenshot_verliert_kein_feedback(db, feedback_dir):
    body = FeedbackWidgetIn.model_validate({
        "messages": [{"role": "user", "text": "Text bleibt erhalten."}],
        "context": {"page": "/x"},
        "screenshot": "data:image/png;base64,%%%kaputt%%%",
    })
    result = await fb.feedback_widget_save(body=body, user=_session_user(), db=db, service=None)
    assert result["ok"] is True
    assert result["screenshot"] is None
    assert (feedback_dir / f"{result['id']}.md").exists()


# ------------------------------------------------- Issue-Spiegelung (Block 4)


class _ExplodingOAuth:
    async def get_valid_token(self, provider):
        raise RuntimeError("GitHub API down")


@pytest.mark.asyncio
async def test_issue_spiegelung_default_aus(monkeypatch):
    monkeypatch.setattr(settings, "feedback_issue_enabled", False)
    # Service wuerde explodieren — darf bei abgeschaltetem Flag nie angefasst werden.
    url = await fb.mirror_issue_best_effort(_ExplodingOAuth(), Feedback(user_id="u", title="t"))
    assert url is None


@pytest.mark.asyncio
async def test_issue_fehler_scheitert_save_nicht(db, feedback_dir, monkeypatch):
    monkeypatch.setattr(settings, "feedback_issue_enabled", True)
    body = FeedbackWidgetIn.model_validate({
        "messages": [{"role": "user", "text": "Wichtig!"}],
        "context": {"page": "/x"},
    })
    result = await fb.feedback_widget_save(
        body=body, user=_session_user(), db=db, service=_ExplodingOAuth()
    )
    assert result["ok"] is True
    assert "issue_url" not in result
    assert (feedback_dir / f"{result['id']}.md").exists()


@pytest.mark.asyncio
async def test_issue_url_landet_in_response_db_und_md(db, feedback_dir, monkeypatch):
    monkeypatch.setattr(settings, "feedback_issue_enabled", True)

    async def fake_post(service, feedback):
        return {"html_url": "https://github.com/o/r/issues/7", "number": 7}

    monkeypatch.setattr(fb, "_post_github_issue", fake_post)
    body = FeedbackWidgetIn.model_validate({
        "messages": [{"role": "user", "text": "Bitte spiegeln."}],
        "context": {"page": "/x"},
    })
    result = await fb.feedback_widget_save(body=body, user=_session_user(), db=db, service=object())
    assert result["issue_url"] == "https://github.com/o/r/issues/7"
    row = (await db.execute(select(Feedback))).scalar_one()
    assert row.github_issue_url == "https://github.com/o/r/issues/7"
    md = (feedback_dir / f"{result['id']}.md").read_text(encoding="utf-8")
    assert "https://github.com/o/r/issues/7" in md


def test_issue_payload_traegt_widget_kontext():
    f = Feedback(
        user_id="u-1", user_name="Yannik", title="Kachel springt",
        description="Nutzer: Kachel springt", category="bug",
        page="/chat", element_label="Live-Kachel", selector="main > div",
        sentiment="negativ", md_file="20260813_x.md",
    )
    payload = fb._issue_payload(f)
    assert payload["title"] == "[Feedback] Kachel springt"
    assert "`/chat`" in payload["body"]
    assert "Live-Kachel" in payload["body"]
    assert "stört mich" in payload["body"]
    assert "20260813_x.md" in payload["body"]


# --------------------------------------------------------- LLM-Config-Disziplin


def test_re_prompt_liegt_als_datei_neben_dem_code():
    p = Path(fb.__file__).resolve().parents[1] / "prompts" / "feedback_re_prompt.md"
    assert p.exists()
    text = p.read_text(encoding="utf-8")
    assert "Requirements Engineer" in text
    assert fb._re_system_prompt().startswith(text.strip()[:20])


def test_kein_modellname_im_feedback_code():
    src = Path(fb.__file__).read_text(encoding="utf-8")
    for verboten in ("claude-", "anthropic.claude", "nova-", "gpt-"):
        assert verboten not in src, f"Modellname {verboten!r} hartcodiert"
