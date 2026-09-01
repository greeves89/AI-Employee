"""Feedback API - users submit feedback, admins manage and create GitHub issues.

Zwei Eingangswege:
- Klassisch (POST /): Titel + Beschreibung + Kategorie, kommt heute aus dem
  Feedback-Widget nur noch indirekt — der Endpoint bleibt fuer API-Clients.
- Widget ("Feedback-Gedöns", POST /reply + /save): Feedback an ein konkretes
  UI-Element gepinnt, mit genau EINER Requirements-Rueckfrage vom LLM.
  Source of Truth ist eine Markdown-Datei (+ optional PNG-Screenshot) in
  FEEDBACK_DIR; zusaetzlich entsteht ein Feedback-DB-Eintrag, damit die
  bestehende Admin-Liste weiterfunktioniert.

Attribution: Der User kommt IMMER aus der validierten Session (require_auth),
nie aus dem Request-Body — ein mitgeschickter Username wird ignoriert.
"""

import base64
import csv
import io
import json
import logging
import re
import time
import zipfile
from functools import lru_cache
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse, StreamingResponse
import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db.session import get_db
from app.dependencies import get_redis_service, require_admin, require_auth
from app.models.feedback import Feedback, FeedbackCategory, FeedbackStatus
from app.schemas.feedback import (
    FeedbackCreate,
    FeedbackListResponse,
    FeedbackResponse,
    FeedbackUpdate,
    FeedbackWidgetIn,
)
from app.services.oauth_service import OAuthService
from app.services.redis_service import RedisService

router = APIRouter(prefix="/feedback", tags=["feedback"])
logger = logging.getLogger(__name__)

# Lesbare Labels fuer das Widget-Sentiment (Lucide-Icons im UI, keine Emojis).
SENTIMENT_LABELS = {"positiv": "gefällt mir", "negativ": "stört mich", "wunsch": "Wunsch"}

MAX_SCREENSHOT_BYTES = 8 * 1024 * 1024  # hartes Limit fuer das decodierte PNG

_RE_PROMPT_PATH = Path(__file__).resolve().parents[1] / "prompts" / "feedback_re_prompt.md"


def _to_response(f: Feedback) -> dict:
    return {
        "id": f.id,
        "user_id": f.user_id,
        "user_name": f.user_name,
        "title": f.title,
        "description": f.description,
        "category": f.category.value if isinstance(f.category, FeedbackCategory) else f.category,
        "status": f.status.value if isinstance(f.status, FeedbackStatus) else f.status,
        "admin_notes": f.admin_notes,
        "github_issue_url": f.github_issue_url,
        "page": f.page,
        "element_label": f.element_label,
        "selector": f.selector,
        "sentiment": f.sentiment,
        "md_file": f.md_file,
        "screenshot_file": f.screenshot_file,
        "created_at": f.created_at,
        "updated_at": f.updated_at,
    }


def _count_by_status(items: list[Feedback]) -> dict:
    counts = {"pending": 0, "reviewed": 0, "in_progress": 0, "closed": 0}
    for f in items:
        s = f.status.value if isinstance(f.status, FeedbackStatus) else f.status
        if s in counts:
            counts[s] += 1
    return counts


def _get_oauth_service(
    db: AsyncSession = Depends(get_db),
    redis: RedisService = Depends(get_redis_service),
) -> OAuthService:
    return OAuthService(db, redis)


async def _send_feedback_webhook(feedback: Feedback) -> None:
    """Forward newly created feedback to the configured external webhook.

    The webhook is best-effort by design: feedback must stay saved in the app
    even if n8n is unavailable, misconfigured, or slow.
    """
    if not settings.feedback_webhook_url:
        return

    category = (
        feedback.category.value
        if isinstance(feedback.category, FeedbackCategory)
        else str(feedback.category or FeedbackCategory.GENERAL.value)
    )
    payload = {
        "category": category,
        "title": feedback.title,
        "description": feedback.description or "",
    }
    headers = {"Content-Type": "application/json"}
    if settings.feedback_webhook_api_key:
        headers["apiKey"] = settings.feedback_webhook_api_key

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(
                settings.feedback_webhook_url,
                headers=headers,
                json=payload,
            )
            response.raise_for_status()
    except Exception as e:
        logger.warning("Feedback webhook delivery failed: %s", e)


# --- Widget: MD-Store-Helfer ---------------------------------------------------


def _feedback_dir() -> Path:
    d = Path(settings.feedback_dir)
    d.mkdir(parents=True, exist_ok=True)
    return d


def _slug(s: str | None, n: int = 24) -> str:
    return re.sub(r"\W+", "", (s or "anon"))[:n] or "anon"


def _new_fid(user_name: str) -> str:
    base = f"{time.strftime('%Y%m%d_%H%M%S')}_{_slug(user_name)}"
    fid, i = base, 1
    while (_feedback_dir() / f"{fid}.md").exists():
        i += 1
        fid = f"{base}_{i}"
    return fid


def save_screenshot(fid: str, screenshot: str | None) -> str | None:
    """Schreibt einen base64-dataURL-Screenshot als PNG neben das MD.

    Gibt den Dateinamen zurueck — oder None, wenn nichts geschrieben wurde
    (kein/kaputter Screenshot, zu gross). Fehler werden geloggt, das Feedback
    geht trotzdem text-only durch.
    """
    if not screenshot:
        return None
    try:
        # erwartet "data:image/png;base64,..." — Prefix abtrennen, sonst direkt dekodieren
        b64 = screenshot.split(",", 1)[1] if "," in screenshot else screenshot
        raw = base64.b64decode(b64, validate=True)
    except Exception as e:
        logger.warning("Feedback %s: Screenshot nicht dekodierbar: %s", fid, e)
        return None
    if len(raw) > MAX_SCREENSHOT_BYTES:
        logger.warning(
            "Feedback %s: Screenshot verworfen (%d Bytes > %d)", fid, len(raw), MAX_SCREENSHOT_BYTES
        )
        return None
    name = f"{_slug(fid, 64)}.png"
    try:
        (_feedback_dir() / name).write_bytes(raw)
    except OSError as e:
        logger.warning("Feedback %s: Screenshot nicht schreibbar: %s", fid, e)
        return None
    return name


def build_md(meta: dict, messages: list[dict]) -> str:
    """Markdown-Datei: YAML-Frontmatter (user, seite, element, selector,
    sentiment, kategorie, screenshot, zeit) + Chat-Verlauf."""
    fm = "\n".join(f"{k}: {json.dumps(v, ensure_ascii=False)}" for k, v in meta.items())
    body = [f"---\n{fm}\n---\n", f"# Feedback – {SENTIMENT_LABELS.get(meta.get('sentiment', ''), 'Hinweis')}", ""]
    for m in messages:
        who = "Bot (Rückfrage)" if m.get("role") == "bot" else (meta.get("user") or "Nutzer")
        body.append(f"**{who}:** {m.get('text', '')}\n")
    if meta.get("screenshot"):
        body.append(f"![Screenshot]({meta['screenshot']})\n")
    if meta.get("issue"):
        body.append(f"[GitHub-Issue]({meta['issue']})\n")
    return "\n".join(body)


def write_md(fid: str, meta: dict, messages: list[dict]) -> str:
    name = f"{_slug(fid, 64)}.md"
    (_feedback_dir() / name).write_text(build_md(meta, messages), encoding="utf-8")
    return name


@lru_cache(maxsize=1)
def _re_system_prompt() -> str:
    """RE-Prompt liegt als Datei neben dem Code — kein Prompt-String im Code."""
    return _RE_PROMPT_PATH.read_text(encoding="utf-8").strip()


async def _one_re_question(db: AsyncSession, redis, messages: list[dict], context: dict) -> str:
    """Genau EINE schaerfende Requirements-Rueckfrage ueber den vorhandenen
    config-getriebenen LLM-Pfad (ReflectionService: Anthropic-Key, Bedrock-
    oder Azure-OpenAI-/OpenAI-Account).

    Raises RuntimeError, wenn kein LLM-Zugang konfiguriert ist oder der Aufruf
    scheitert — der Aufrufer uebersetzt das in eine saubere HTTP-Antwort.
    """
    from app.services.reflection_service import ReflectionService

    svc = ReflectionService(redis)
    cfg = await svc._load_config(db)
    if not cfg.get("backend"):
        raise RuntimeError(
            "kein LLM-Zugang konfiguriert (kein Anthropic-Key, Bedrock- oder OpenAI-Account)"
        )

    # FEEDBACK_MODEL uebersteuert das Reflection-Modell nur fuer diese Rueckfrage.
    # Auf Bedrock braucht es eine volle Bedrock-Id — sonst bleibt cfg["model"],
    # das _load_config bereits auf ein Bedrock-taugliches Modell gemappt hat.
    # Auf Azure OpenAI ist es der Deployment-Name (z.B. ein guenstiges Mini-Deployment).
    fb_model = settings.feedback_model.strip()
    if fb_model and (
        cfg["backend"] != "bedrock" or "anthropic." in fb_model or "amazon." in fb_model
    ):
        cfg["model"] = fb_model

    ctx = (
        f"Element: {context.get('element_label') or '—'} · Seite: {context.get('page') or '—'} · "
        f"Bewertung: {SENTIMENT_LABELS.get(context.get('sentiment') or '', '—')} · "
        f"Kategorie: {context.get('kategorie') or '—'}"
    )
    turns = "\n".join(
        f"{'Rückfrage' if m.get('role') == 'bot' else 'Nutzer'}: {m.get('text', '')}"
        for m in messages
    )
    prompt = f"{_re_system_prompt()}\n\nKONTEXT: {ctx}\n\nGESPRÄCH:\n{turns}\n\nDeine Antwort:"

    data = await svc._call_llm(cfg, prompt)
    text = ((data or {}).get("content") or [{}])[0].get("text") or ""
    text = text.strip()
    if not text:
        raise RuntimeError("LLM lieferte keine Antwort")
    return text


# --- GitHub-Issue (gemeinsam fuer Admin-Endpoint und Auto-Spiegelung) ----------


def _issue_payload(feedback: Feedback) -> dict:
    """Issue-Titel/-Body aus einem Feedback-Eintrag — fuer Modal- wie Widget-Feedback."""
    category = (
        feedback.category.value if isinstance(feedback.category, FeedbackCategory) else feedback.category
    )
    lines = [f"**Category:** {category}"]
    if feedback.sentiment:
        lines.append(f"**Sentiment:** {SENTIMENT_LABELS.get(feedback.sentiment, feedback.sentiment)}")
    lines.append(f"**Submitted by:** {feedback.user_name or feedback.user_id}")
    if feedback.page:
        lines.append(f"**Seite:** `{feedback.page}`")
    if feedback.element_label:
        lines.append(f"**Element:** {feedback.element_label}")
    if feedback.selector:
        lines.append(f"**Selector:** `{feedback.selector}`")
    if feedback.md_file:
        lines.append(f"**Feedback-Datei:** `{feedback.md_file}`")
    body_md = "\n".join(lines) + "\n\n"
    body_md += feedback.description or "(No description provided)"
    body_md += "\n\n---\n*Created from AI Employee Platform Feedback*"
    return {
        "title": f"[Feedback] {feedback.title}",
        "body": body_md,
        "labels": [f"feedback:{category}"],
    }


async def _post_github_issue(service: OAuthService, feedback: Feedback) -> dict:
    """Legt das Issue via GitHub-API an. Raises ValueError (Integration fehlt)
    oder RuntimeError (API-Fehler) — Fehlerbehandlung liegt beim Aufrufer."""
    token = await service.get_valid_token("github")
    repo = settings.github_repo or "greeves89/AI-Employee"

    async with httpx.AsyncClient() as client:
        response = await client.post(
            f"https://api.github.com/repos/{repo}/issues",
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
            json=_issue_payload(feedback),
            timeout=15.0,
        )

    if response.status_code not in (200, 201):
        raise RuntimeError(f"GitHub API error: {response.status_code} - {response.text[:200]}")
    return response.json()


async def mirror_issue_best_effort(service: OAuthService, feedback: Feedback) -> str | None:
    """Auto-Spiegelung beim Speichern: wirft NIE — die MD-Datei ist schon
    geschrieben, ein Issue-Fehler darf das Speichern nicht scheitern lassen."""
    if not settings.feedback_issue_enabled:
        return None
    try:
        data = await _post_github_issue(service, feedback)
        return data.get("html_url")
    except Exception as e:
        logger.warning("Feedback %s: GitHub-Issue-Anlage fehlgeschlagen: %s", feedback.md_file, e)
        return None


# --- User endpoints ---


@router.post("/", response_model=FeedbackResponse)
async def create_feedback(
    body: FeedbackCreate,
    user=Depends(require_auth),
    db: AsyncSession = Depends(get_db),
):
    """Submit feedback (any authenticated user)."""
    feedback = Feedback(
        user_id=user.id,
        user_name=getattr(user, "display_name", None) or user.email,
        title=body.title,
        description=body.description,
        category=FeedbackCategory(body.category) if body.category else FeedbackCategory.GENERAL,
    )
    db.add(feedback)
    await db.commit()
    await db.refresh(feedback)
    await _send_feedback_webhook(feedback)
    return _to_response(feedback)


@router.post("/reply")
async def feedback_widget_reply(
    body: FeedbackWidgetIn,
    user=Depends(require_auth),
    db: AsyncSession = Depends(get_db),
    redis: RedisService = Depends(get_redis_service),
):
    """Genau EINE Requirements-Rueckfrage zum Widget-Feedback (jeder eingeloggte User)."""
    try:
        reply = await _one_re_question(
            db, redis, [m.model_dump() for m in body.messages], body.context.model_dump()
        )
    except Exception as e:
        logger.warning("Feedback-Rueckfrage fehlgeschlagen: %s", e)
        raise HTTPException(status_code=503, detail="RE-Rückfrage derzeit nicht verfügbar")
    return {"reply": reply}


@router.post("/save")
async def feedback_widget_save(
    body: FeedbackWidgetIn,
    user=Depends(require_auth),
    db: AsyncSession = Depends(get_db),
    service: OAuthService = Depends(_get_oauth_service),
):
    """Widget-Feedback speichern: MD (+PNG) in FEEDBACK_DIR ist die Source of
    Truth, dazu ein Feedback-DB-Eintrag fuer die Admin-Liste. Issue-Spiegelung
    best-effort, wenn FEEDBACK_ISSUE_ENABLED gesetzt ist."""
    messages = [m.model_dump() for m in body.messages]
    user_texts = [m["text"] for m in messages if m.get("role") == "user" and m.get("text", "").strip()]
    if not user_texts:
        raise HTTPException(status_code=422, detail="Feedback ohne Text")

    ctx = body.context
    # Attribution ausschliesslich aus der Session — nie aus dem Body.
    user_name = getattr(user, "name", None) or user.email

    fid = _new_fid(user_name)
    shot_name = save_screenshot(fid, body.screenshot)
    meta = {
        "id": fid,
        "user": user_name,
        "seite": ctx.page or "",
        "element": ctx.element_label or "",
        "selector": ctx.selector or "",
        "sentiment": ctx.sentiment or "",
        "kategorie": ctx.kategorie or FeedbackCategory.GENERAL.value,
        "screenshot": shot_name or "",
        "zeit": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }
    # MD zuerst — sie ist die Source of Truth; alles danach ist additiv.
    md_name = write_md(fid, meta, messages)

    try:
        category = FeedbackCategory(ctx.kategorie) if ctx.kategorie else FeedbackCategory.GENERAL
    except ValueError:
        category = FeedbackCategory.GENERAL
    feedback = Feedback(
        user_id=user.id,
        user_name=user_name,
        title=user_texts[0][:200],
        description="\n\n".join(
            f"{'Rückfrage' if m.get('role') == 'bot' else 'Nutzer'}: {m.get('text', '')}" for m in messages
        ),
        category=category,
        page=ctx.page,
        element_label=ctx.element_label,
        selector=ctx.selector,
        sentiment=ctx.sentiment,
        md_file=md_name,
        screenshot_file=shot_name,
    )
    db.add(feedback)
    await db.commit()
    await db.refresh(feedback)
    await _send_feedback_webhook(feedback)

    result = {"ok": True, "id": fid, "screenshot": shot_name}
    issue_url = await mirror_issue_best_effort(service, feedback)
    if issue_url:
        feedback.github_issue_url = issue_url
        await db.commit()
        meta["issue"] = issue_url
        write_md(fid, meta, messages)  # Frontmatter um die Issue-URL ergaenzen
        result["issue_url"] = issue_url
    return result


# --- Admin endpoints ---


@router.get("/", response_model=FeedbackListResponse)
async def list_feedback(
    status: str | None = Query(None),
    user=Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """List all feedback (admin only)."""
    query = select(Feedback)
    if status:
        query = query.where(Feedback.status == FeedbackStatus(status))
    query = query.order_by(Feedback.created_at.desc())
    result = await db.execute(query)
    items = list(result.scalars().all())
    counts = _count_by_status(items)
    return {
        "feedback": [_to_response(f) for f in items],
        "total": len(items),
        **counts,
    }


_CSV_FORMULA_TRIGGERS = ("=", "+", "-", "@", "\t", "\r")


def _csv_safe(value: str | None) -> str:
    """Entschaerft CSV-/Formel-Injection: jedes Feld kommt aus Nutzer-Feedback
    (Titel, Notizen, ...) — ein Wert wie ``=cmd|'/c calc'!A1`` wuerde in Excel/
    Sheets beim Oeffnen als Formel ausgefuehrt. Ein fuehrendes Anfuehrungszeichen
    zwingt jede gaengige Tabellenkalkulation, die Zelle als reinen Text zu lesen."""
    text = value or ""
    if text and text[0] in _CSV_FORMULA_TRIGGERS:
        return "'" + text
    return text


@router.get("/export")
async def export_feedback(
    status: str | None = Query(None),
    user=Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """ZIP-Export: eine Uebersicht (CSV) + je Widget-Feedback die Markdown-Datei
    und den Screenshot, falls vorhanden (admin only). Dieselbe Filterung wie
    ``list_feedback``, damit "nur offene exportieren" moeglich ist."""
    query = select(Feedback)
    if status:
        query = query.where(Feedback.status == FeedbackStatus(status))
    query = query.order_by(Feedback.created_at.desc())
    items = list((await db.execute(query)).scalars().all())

    csv_puffer = io.StringIO()
    writer = csv.writer(csv_puffer)
    writer.writerow([
        "id", "user_name", "title", "category", "status", "sentiment",
        "page", "element_label", "github_issue_url", "admin_notes", "created_at",
    ])
    for f in items:
        category = f.category.value if isinstance(f.category, FeedbackCategory) else f.category
        fstatus = f.status.value if isinstance(f.status, FeedbackStatus) else f.status
        writer.writerow([
            f.id, _csv_safe(f.user_name), _csv_safe(f.title), category or "", fstatus or "",
            _csv_safe(f.sentiment), _csv_safe(f.page), _csv_safe(f.element_label),
            _csv_safe(f.github_issue_url), _csv_safe(f.admin_notes),
            f.created_at.isoformat() if f.created_at else "",
        ])

    zip_puffer = io.BytesIO()
    with zipfile.ZipFile(zip_puffer, "w", zipfile.ZIP_DEFLATED) as archiv:
        archiv.writestr("feedback.csv", csv_puffer.getvalue())
        fdir = _feedback_dir()
        for f in items:
            if f.md_file and (fdir / f.md_file).exists():
                archiv.write(fdir / f.md_file, arcname=f"widget/{f.md_file}")
            if f.screenshot_file and (fdir / f.screenshot_file).exists():
                archiv.write(fdir / f.screenshot_file, arcname=f"widget/{f.screenshot_file}")
    zip_puffer.seek(0)

    return StreamingResponse(
        zip_puffer,
        media_type="application/zip",
        headers={"Content-Disposition": 'attachment; filename="feedback-export.zip"'},
    )


@router.get("/item/{fid}")
async def get_feedback_item(
    fid: str,
    user=Depends(require_admin),
):
    """Volltext (Markdown) eines Widget-Feedbacks (admin only)."""
    p = _feedback_dir() / f"{_slug(fid, 64)}.md"
    if not p.exists():
        raise HTTPException(status_code=404, detail="Feedback file not found")
    return {"id": fid, "md": p.read_text(encoding="utf-8")}


@router.get("/image/{fid}")
async def get_feedback_image(
    fid: str,
    user=Depends(require_admin),
):
    """Screenshot (PNG) eines Widget-Feedbacks (admin only)."""
    p = _feedback_dir() / f"{_slug(fid, 64)}.png"
    if not p.exists():
        raise HTTPException(status_code=404, detail="Screenshot not found")
    return FileResponse(p, media_type="image/png")


@router.patch("/{feedback_id}", response_model=FeedbackResponse)
async def update_feedback(
    feedback_id: int,
    body: FeedbackUpdate,
    user=Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """Update feedback status or admin notes (admin only)."""
    result = await db.execute(select(Feedback).where(Feedback.id == feedback_id))
    feedback = result.scalar_one_or_none()
    if not feedback:
        raise HTTPException(status_code=404, detail="Feedback not found")

    if body.status is not None:
        feedback.status = FeedbackStatus(body.status)
    if body.admin_notes is not None:
        feedback.admin_notes = body.admin_notes

    await db.commit()
    await db.refresh(feedback)
    return _to_response(feedback)


@router.delete("/{feedback_id}")
async def delete_feedback(
    feedback_id: int,
    user=Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """Delete feedback (admin only)."""
    result = await db.execute(select(Feedback).where(Feedback.id == feedback_id))
    feedback = result.scalar_one_or_none()
    if not feedback:
        raise HTTPException(status_code=404, detail="Feedback not found")
    await db.delete(feedback)
    await db.commit()
    return {"deleted": feedback_id}


@router.post("/{feedback_id}/github-issue")
async def create_github_issue(
    feedback_id: int,
    user=Depends(require_admin),
    db: AsyncSession = Depends(get_db),
    service: OAuthService = Depends(_get_oauth_service),
):
    """Create a GitHub issue from feedback (admin only, requires GitHub integration)."""
    result = await db.execute(select(Feedback).where(Feedback.id == feedback_id))
    feedback = result.scalar_one_or_none()
    if not feedback:
        raise HTTPException(status_code=404, detail="Feedback not found")

    if feedback.github_issue_url:
        raise HTTPException(status_code=400, detail="GitHub issue already created")

    try:
        data = await _post_github_issue(service, feedback)
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail="GitHub integration not connected. Connect GitHub in Integrations first.",
        )
    except RuntimeError as e:
        raise HTTPException(status_code=502, detail=str(e))

    feedback.github_issue_url = data["html_url"]
    feedback.status = FeedbackStatus.IN_PROGRESS
    await db.commit()
    await db.refresh(feedback)

    return {
        "issue_url": data["html_url"],
        "issue_number": data["number"],
        "feedback": _to_response(feedback),
    }
