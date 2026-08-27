"""Feedback als ZIP exportieren (Kundenfeedback: Export-Wunsch).

Eine CSV-Uebersicht ueber alle Eintraege + je Widget-Feedback die Markdown-
Datei und den Screenshot, falls vorhanden. Klassische (Nicht-Widget-)
Eintraege haben kein md_file/screenshot_file — die muessen trotzdem sauber
in der CSV landen, ohne dass das Zip an fehlenden Dateien scheitert.
"""

import csv
import io
import zipfile
from types import SimpleNamespace

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.api import feedback as fb
from app.config import settings
from app.models.base import Base
from app.models.feedback import Feedback


@pytest.fixture
def feedback_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "feedback_dir", str(tmp_path))
    return tmp_path


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


def _admin():
    return SimpleNamespace(id="admin-1", role="admin", email="admin@example.test")


async def _zip_from(resp) -> zipfile.ZipFile:
    chunks = [c async for c in resp.body_iterator]
    data = b"".join(c if isinstance(c, bytes) else c.encode() for c in chunks)
    return zipfile.ZipFile(io.BytesIO(data))


@pytest.mark.asyncio
async def test_empty_feedback_still_yields_a_csv_only_zip(db, feedback_dir):
    resp = await fb.export_feedback(status=None, user=_admin(), db=db)
    assert resp.media_type == "application/zip"
    zf = await _zip_from(resp)
    assert zf.namelist() == ["feedback.csv"]
    rows = list(csv.reader(io.StringIO(zf.read("feedback.csv").decode("utf-8"))))
    assert rows[0][:3] == ["id", "user_name", "title"]

@pytest.mark.asyncio
async def test_classic_feedback_without_widget_files_lands_in_the_csv_only(db, feedback_dir):
    db.add(Feedback(user_id="u-1", user_name="Yannik", title="App stuerzt ab", category="bug"))
    await db.commit()
    resp = await fb.export_feedback(status=None, user=_admin(), db=db)
    zf = await _zip_from(resp)
    assert zf.namelist() == ["feedback.csv"]
    rows = list(csv.reader(io.StringIO(zf.read("feedback.csv").decode("utf-8"))))
    assert rows[1][1:4] == ["Yannik", "App stuerzt ab", "bug"]


@pytest.mark.asyncio
async def test_widget_feedback_bundles_its_markdown_and_screenshot(db, feedback_dir):
    (feedback_dir / "20260827_test.md").write_text("# Feedback", encoding="utf-8")
    (feedback_dir / "20260827_test.png").write_bytes(b"\x89PNG")
    db.add(Feedback(
        user_id="u-1", user_name="Yannik", title="Kachel springt", category="bug",
        md_file="20260827_test.md", screenshot_file="20260827_test.png",
    ))
    await db.commit()
    resp = await fb.export_feedback(status=None, user=_admin(), db=db)
    zf = await _zip_from(resp)
    assert set(zf.namelist()) == {
        "feedback.csv", "widget/20260827_test.md", "widget/20260827_test.png",
    }
    assert zf.read("widget/20260827_test.md") == b"# Feedback"
    assert zf.read("widget/20260827_test.png") == b"\x89PNG"


@pytest.mark.asyncio
async def test_a_missing_widget_file_on_disk_is_skipped_not_a_crash(db, feedback_dir):
    """md_file zeigt auf eine Datei, die (aus welchem Grund auch immer) nicht
    mehr existiert — der Export darf trotzdem fertig werden."""
    db.add(Feedback(
        user_id="u-1", user_name="Yannik", title="X", category="bug",
        md_file="verschwunden.md",
    ))
    await db.commit()
    resp = await fb.export_feedback(status=None, user=_admin(), db=db)
    zf = await _zip_from(resp)
    assert zf.namelist() == ["feedback.csv"]


@pytest.mark.asyncio
async def test_a_formula_looking_title_is_neutralised_not_executed(db, feedback_dir):
    """CSV-/Formel-Injection: Titel/Notizen kommen aus Nutzer-Feedback. Ein Wert
    wie '=cmd|...'!A1 wuerde in Excel/Sheets beim Oeffnen als Formel laufen."""
    db.add(Feedback(
        user_id="u-1", user_name="=HYPERLINK(\"http://evil.test\")", title="=1+1",
        category="bug", admin_notes="+SUM(A1:A9)",
    ))
    await db.commit()
    resp = await fb.export_feedback(status=None, user=_admin(), db=db)
    zf = await _zip_from(resp)
    rows = list(csv.reader(io.StringIO(zf.read("feedback.csv").decode("utf-8"))))
    _id, user_name, title, *_rest, admin_notes, _created = rows[1]
    assert user_name.startswith("'=")
    assert title.startswith("'=")
    assert admin_notes.startswith("'+")


@pytest.mark.asyncio
async def test_status_filter_only_exports_matching_items(db, feedback_dir):
    db.add(Feedback(user_id="u-1", user_name="A", title="offen", category="bug", status="pending"))
    db.add(Feedback(user_id="u-1", user_name="B", title="erledigt", category="bug", status="closed"))
    await db.commit()
    resp = await fb.export_feedback(status="pending", user=_admin(), db=db)
    zf = await _zip_from(resp)
    rows = list(csv.reader(io.StringIO(zf.read("feedback.csv").decode("utf-8"))))
    titles = [r[2] for r in rows[1:]]
    assert titles == ["offen"]
