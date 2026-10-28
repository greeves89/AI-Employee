"""Second Brains API — admin-managed, department-shared knowledge vaults.

A Second Brain is a DB-managed mount-catalog entry. Creating one provisions a
shared Markdown vault under ``/srv/secondbrain/<slug>/`` (mkdir + local ``git
init`` + an ``index.md`` scaffold) and makes its ``label`` available everywhere
the mount machinery already works: the mount-permissions modal (per-user ro/rw),
custom roles (``mount_labels``) and the per-agent mount selector. No .env edit,
no orchestrator restart.

Admins create/edit/delete; any authenticated user may list them (so they can
attach one to an agent). The host path is never returned to the UI.
"""
import logging
import os
import re
import secrets
import subprocess
from datetime import datetime

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy import text as sa_text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core import vault, vault_transfer
from app.core.encryption import encrypt_token
from app.db.session import get_db
from app.dependencies import require_auth, verify_agent_token
from app.models.agent import Agent
from app.models.audit_log import AuditEventType, AuditLog
from app.models.second_brain import SecondBrain
from app.models.user import UserRole
from app.services import vault_indexer, vault_search

log = logging.getLogger(__name__)
router = APIRouter(prefix="/brains", tags=["brains"])

_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9_-]*$")

# Vault formatting standards scaffolded on creation.
STANDARDS = ("freeform", "wikimedia", "it_support")

_INDEX = {
    "freeform": "# {name} — Second Brain\n\nGeteilte Wissensablage. Frei organisierbar.\n\n## Index\n- (Artikel hier verlinken)\n",
    "wikimedia": "# {name} — Second Brain\n\nGeteilte Wissensablage (Wikimedia-Stil). Siehe `CONVENTIONS.md`.\n\n## Index\n- (Artikel mit `[[Titel]]` verlinken)\n",
    "it_support": "# {name} — IT-Support Wissensbasis\n\nRunbooks & Loesungen, sortiert nach Bereich. Siehe `CONVENTIONS.md` und die Vorlage `Drucker/_template.md`.\n\n## Bereiche\n- [[Drucker]]\n- [[Netzwerk]]\n- [[Zugaenge]]\n- [[Software]]\n- [[Hardware]]\n",
}

_CONVENTIONS = {
    "freeform": "# Konventionen\n\nKeine erzwungene Struktur. Sprechende Dateinamen, Querverweise mit `[[Titel]]`, Schlagworte/Fehlercodes im Klartext (damit `grep` sie findet).\n",
    "wikimedia": "# Konventionen (Wikimedia-Stil)\n\n- Eine `.md` pro Thema, sprechender Dateiname.\n- Themen-Ordner; `index.md` verlinkt die Artikel.\n- Querverweise mit `[[Titel]]`.\n- Schlagworte/Fehlercodes im Klartext (fuer `grep`).\n",
    "it_support": "# Konventionen (IT-Support / Runbooks)\n\n- Ordner nach Bereich: `Drucker/`, `Netzwerk/`, `Zugaenge/`, `Software/`, `Hardware/`.\n- Ein Artikel pro Problem/Fehlercode; Dateiname = Fehlercode oder kurzes Symptom.\n- **Artikel-Struktur** (siehe `Drucker/_template.md`): `## Symptom` -> `## Ursache` -> `## Loesung` (Schritt-fuer-Schritt) -> `## Hinweise`.\n- Fehlercodes/Modelle/Hostnamen IM KLARTEXT (damit `grep` exakt trifft).\n- Querverweise mit `[[Titel]]`.\n",
}

_FOLDERS = {
    "freeform": [],
    "wikimedia": [],
    "it_support": ["Drucker", "Netzwerk", "Zugaenge", "Software", "Hardware"],
}

_IT_TEMPLATE = (
    "# <Fehlercode oder kurzes Symptom>\n\n"
    "## Symptom\n<Was meldet/sieht der Nutzer? Fehlercode im Klartext.>\n\n"
    "## Ursache\n<Woran liegt es?>\n\n"
    "## Loesung\n1. <Schritt>\n2. <Schritt>\n\n"
    "## Hinweise\n<Edge-Cases, verwandte Artikel [[...]]>\n"
)


def _seed_file(path: str, content: str, mode: int = 0o666) -> None:
    if not os.path.exists(path):
        try:
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(content)
            os.chmod(path, mode)
        except OSError as e:
            log.warning("Could not write %s: %s", vault.safe_log(path), vault.safe_log(e))


def _require_admin(user) -> None:
    if not (hasattr(user, "role") and user.role == UserRole.ADMIN):
        raise HTTPException(status_code=403, detail="Nur für Admins")


def _slugify(raw: str) -> str:
    s = raw.strip().lower().replace(" ", "_")
    s = re.sub(r"[^a-z0-9_-]", "", s)
    return s.strip("-_")


def _provision_vault(host_path: str, name: str, standard: str = "freeform") -> None:
    """Create the vault dir, scaffold it per the chosen standard (folders +
    index.md + CONVENTIONS.md), and init a LOCAL git repo (no remote).

    Best-effort: a missing ``git`` binary or a non-writable base must not break
    brain creation — the mount still works, only file history is unavailable.
    """
    standard = standard if standard in STANDARDS else "freeform"
    base = os.path.realpath(settings.secondbrain_base)
    real = os.path.realpath(host_path)
    if real != base and not real.startswith(base + os.sep):
        raise HTTPException(status_code=400, detail="resolved path escapes the second-brain base")
    os.makedirs(real, exist_ok=True)
    # The orchestrator runs as root but agent containers run as a non-root user
    # (uid 1000). Make the vault writable by the agent so rw brains work, and the
    # seeded files editable. .git stays root-owned (the host auto-commit timer
    # runs as root). World-writable is acceptable for an internal shared vault.
    try:
        os.chmod(real, 0o777)
    except OSError as e:
        log.warning("Could not chmod vault %s: %s", vault.safe_log(real), vault.safe_log(e))
    # Scaffold folders for the standard
    for folder in _FOLDERS.get(standard, []):
        d = os.path.join(real, folder)
        os.makedirs(d, exist_ok=True)
        try:
            os.chmod(d, 0o777)
        except OSError:
            pass
    _seed_file(os.path.join(real, "index.md"), _INDEX[standard].format(name=name))
    _seed_file(os.path.join(real, "CONVENTIONS.md"), _CONVENTIONS[standard])
    if standard == "it_support":
        _seed_file(os.path.join(real, "Drucker", "_template.md"), _IT_TEMPLATE)
    if not os.path.isdir(os.path.join(real, ".git")):
        try:
            # Über denselben Ordner wie der Verlauf (``projekt_wissen``) — und dieser
            # Aufruf kann einen Ordner vorfinden, in dem schon Agenten-Inhalte liegen
            # (Brain gelöscht, Dateien behalten, Slug neu angelegt). Deshalb gehärtet.
            if vault.git_sicher(real, "init", "-q", timeout=20).returncode != 0:
                raise OSError("git init fehlgeschlagen")
            vault.git_sicher(real, "config", "user.email", "secondbrain@ai-employee.local", timeout=10)
            vault.git_sicher(real, "config", "user.name", "Second Brain", timeout=10)
            vault.git_sicher(real, "add", "-A", timeout=20)
            vault.git_sicher(real, "commit", "-q", "-m", "init vault", timeout=20)
        except (OSError, subprocess.SubprocessError) as e:
            log.warning("git init for vault %s failed (history disabled): %s", vault.safe_log(real), vault.safe_log(e))


async def _audit(db: AsyncSession, event: AuditEventType, brain: SecondBrain, user_id: str) -> None:
    try:
        db.add(AuditLog(
            agent_id="system",
            event_type=event.value,
            command=brain.label,
            outcome="success",
            user_id=str(user_id),
            # Der Name eines gespiegelten Bridge-Ordners ist privat (#1092) — Admins lesen das Protokoll.
            meta={"name": "(privat)" if getattr(brain, "bridge_ordner", None) else brain.name,
                  "slug": brain.slug, "mode": brain.default_mode},
        ))
        await db.commit()
    except Exception:  # auditing must never break the operation
        await db.rollback()


class BrainCreate(BaseModel):
    name: str
    slug: str | None = None
    default_mode: str = "rw"  # "ro" | "rw"
    standard: str = "freeform"  # freeform | wikimedia | it_support
    description: str | None = None


class BrainUpdate(BaseModel):
    name: str | None = None
    default_mode: str | None = None
    description: str | None = None
    is_active: bool | None = None


class BrainResponse(BaseModel):
    id: int
    label: str
    name: str
    slug: str
    container_path: str  # host_path is intentionally NOT exposed
    default_mode: str
    standard: str
    description: str | None
    is_active: bool
    mcp_enabled: bool = False
    mcp_path: str | None = None  # relative endpoint; UI prepends window.location.origin
    # Index-Sichtbarkeit (#727): ohne diese zwei Zahlen ist am Reindex-Knopf nicht
    # ablesbar, ob er noetig ist — und ein driftender Index bleibt still.
    indexed_files: int = 0
    indexed_at: datetime | None = None
    # Brain aus einem Bridge-Ordner (#1092): Quelle auf dem Rechner, letzter Abgleich, Fehler.
    bridge_ordner: str | None = None
    sync_stand: datetime | None = None
    sync_fehler: str | None = None
    created_at: datetime
    updated_at: datetime


def _mcp_path(b: SecondBrain) -> str:
    return f"/api/v1/mcp/brains/{b.slug}"


def _to_response(b: SecondBrain, stats: dict[str, tuple[int, datetime | None]] | None = None) -> BrainResponse:
    files, indexed_at = (stats or {}).get(b.label, (0, None))
    mcp_on = bool(getattr(b, "mcp_enabled", False))
    return BrainResponse(
        id=b.id, label=b.label, name=b.name, slug=b.slug,
        container_path=b.container_path, default_mode=b.default_mode,
        standard=getattr(b, "standard", "freeform") or "freeform",
        description=b.description, is_active=b.is_active,
        mcp_enabled=mcp_on, mcp_path=_mcp_path(b) if mcp_on else None,
        indexed_files=files, indexed_at=indexed_at,
        bridge_ordner=getattr(b, "bridge_ordner", None), sync_stand=getattr(b, "sync_stand", None),
        sync_fehler=getattr(b, "sync_fehler", None),
        created_at=b.created_at, updated_at=b.updated_at,
    )


async def _index_stats(db: AsyncSession) -> dict[str, tuple[int, datetime | None]]:
    """Per-Brain: wie viele Dateien im Index stehen und wann zuletzt indiziert.

    Best effort und absichtlich fehlertolerant: ``vault_chunks`` ist eine reine
    SQL-Tabelle (kein ORM-Modell), es gibt sie in einer SQLite-Testdatenbank also
    nicht. Eine Anzeige darf das Auflisten der Brains nicht zum Scheitern bringen
    — fehlt die Tabelle, sind die Zahlen eben 0/leer.
    """
    try:
        rows = (
            await db.execute(
                sa_text(
                    "SELECT brain_label, COUNT(DISTINCT path) AS dateien, "
                    "MAX(updated_at) AS zuletzt FROM vault_chunks GROUP BY brain_label"
                )
            )
        ).all()
    except Exception as e:
        log.debug("Index-Zahlen nicht verfuegbar: %s", vault.safe_log(e))
        return {}
    return {r[0]: (int(r[1] or 0), r[2]) for r in rows}


async def _ist_projektwissen(db: AsyncSession, slug: str) -> bool:
    from app.models.team import Team

    return (await db.scalar(select(Team.id).where(Team.wissen_slug == slug).limit(1))) is not None


async def _brain_oder_404(db: AsyncSession, brain_id: int, user=None) -> SecondBrain:
    """Das Brain — 404, wenn es fehlt, das Wissen eines Projekts ist oder ein
    gespiegelter Bridge-Ordner eines anderen Nutzers.

    Projektwissen (samt ausgelesenem Text hochgeladener Dateien) sieht nur, wer das
    Projekt sieht — über die Projektseite. Die allgemeine Brain-Verwaltung gab es
    bis 1.385.0 jedem Administrator preis (Sicherheits-Review 28.10.2026). Ein
    Bridge-Ordner (#1092) spiegelt den Rechner seines Besitzers und gehört nur ihm.
    """
    from app.core import bridge_wissen

    brain = await db.get(SecondBrain, brain_id)
    if not brain or await _ist_projektwissen(db, brain.slug):
        raise HTTPException(status_code=404, detail="Brain not found")
    if bridge_wissen.ist_bridge_brain(brain) and not bridge_wissen.gehoert(brain, user):
        raise HTTPException(status_code=404, detail="Brain not found")
    return brain


def _nicht_gespiegelt(brain: SecondBrain) -> None:
    """Ein gespiegelter Bridge-Ordner ist nur lesend — seine Quelle ist der Rechner."""
    from app.core import bridge_wissen

    if bridge_wissen.ist_bridge_brain(brain):
        raise HTTPException(status_code=409, detail="Dieses Brain spiegelt einen Bridge-Ordner und ist nur lesend — "
                                                    "Änderungen bitte im Ordner auf dem Rechner.")


# ── Brain aus einem Bridge-Ordner (#1092) — für jeden angemeldeten Nutzer ──

class BridgeBrainCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    ordner: str = Field(min_length=1, max_length=1000)


async def _bridge_ordner(user) -> list[str]:
    """Die in der verbundenen Bridge freigegebenen Ordner des Nutzers."""
    from app.api.computer_use import bridge_befehl

    antwort = await bridge_befehl(str(user.id), "wissen_ordner", {}, timeout=15)
    if not isinstance(antwort, dict) or not antwort.get("ok"):
        if str((antwort or {}).get("error") or "").startswith("Unknown action"):
            raise HTTPException(status_code=409, detail="Die Bridge ist zu alt für gespiegelte Ordner — bitte aktualisieren.")
        raise HTTPException(status_code=409, detail="Die Bridge hat ihre Ordner nicht genannt.")
    return [str(o) for o in antwort.get("ordner") or [] if isinstance(o, str)][:100]


async def _eigenes_bridge_brain(db: AsyncSession, brain_id: int, user) -> SecondBrain:
    from app.core import bridge_wissen

    brain = await db.get(SecondBrain, brain_id)
    if not brain or not bridge_wissen.ist_bridge_brain(brain) or not bridge_wissen.gehoert(brain, user):
        raise HTTPException(status_code=404, detail="Brain not found")
    return brain


@router.get("/bridge/ordner")
async def bridge_ordner_liste(user=Depends(require_auth)):
    """Ordner, die die Bridge des Nutzers freigegeben hat (Berechtigungen → Ordner-Zugriff)."""
    return {"ordner": await _bridge_ordner(user)}


@router.post("/bridge", response_model=BrainResponse, status_code=201)
async def bridge_brain_anlegen(body: BridgeBrainCreate, user=Depends(require_auth), db: AsyncSession = Depends(get_db)):
    """Second Brain mit einem freigegebenen Bridge-Ordner als Quelle anlegen und abgleichen."""
    from app.core import bridge_wissen

    ordner = body.ordner.strip()
    # Nur, was die Bridge JETZT freigibt — der Server übernimmt keinen frei getippten Pfad.
    if ordner not in await _bridge_ordner(user):
        raise HTTPException(status_code=422, detail="Dieser Ordner ist in der Bridge nicht freigegeben.")
    vorhanden = await bridge_wissen.bridge_brains(db, str(user.id))
    if any(b.bridge_ordner == ordner for b in vorhanden):
        raise HTTPException(status_code=409, detail="Dieser Ordner wird schon gespiegelt.")
    if len(vorhanden) >= bridge_wissen.MAX_JE_NUTZER:
        raise HTTPException(status_code=409, detail=f"Höchstens {bridge_wissen.MAX_JE_NUTZER} gespiegelte Ordner je Nutzer.")
    brain = await bridge_wissen.anlegen(db, user, body.name, ordner)
    await _audit(db, AuditEventType.BRAIN_CREATED, brain, user.id)
    await db.refresh(brain)  # nach einem gescheiterten Protokolleintrag (Rollback) neu laden
    bridge_wissen.anstossen(str(user.id))
    return _to_response(brain, await _index_stats(db))


@router.post("/bridge/{brain_id}/sync")
async def bridge_brain_sync(brain_id: int, user=Depends(require_auth), db: AsyncSession = Depends(get_db)):
    """Jetzt abgleichen (sonst alle 10 Minuten, solange die Bridge verbunden ist)."""
    from app.core import bridge_wissen

    from app.api.computer_use import verbundene_bridge

    await _eigenes_bridge_brain(db, brain_id, user)
    if verbundene_bridge(str(user.id)) is None or not bridge_wissen.anstossen(str(user.id)):
        raise HTTPException(status_code=409, detail="Die Bridge ist nicht verbunden — abgeglichen wird, sobald sie es ist.")
    return {"ok": True}


@router.delete("/bridge/{brain_id}")
async def bridge_brain_loeschen(brain_id: int, user=Depends(require_auth), db: AsyncSession = Depends(get_db)):
    """Gespiegeltes Brain löschen — die Kopie auf dem Server, nie den Ordner auf dem Rechner."""
    from app.core import bridge_wissen

    brain = await _eigenes_bridge_brain(db, brain_id, user)
    await _audit(db, AuditEventType.BRAIN_DELETED, brain, user.id)
    await bridge_wissen.entfernen(db, brain)
    return {"ok": True, "id": brain_id}


@router.get("/", response_model=list[BrainResponse])
async def list_brains(
    active_only: bool = False,
    user=Depends(require_auth),
    db: AsyncSession = Depends(get_db),
):
    """List Second Brains the caller may use (admin: all).

    Bis 29.09.2026 sah jeder angemeldete Nutzer alle Brains samt Beschreibung,
    auch ohne Freigabe — anhaengen durfte er sie ohnehin nicht. Jetzt dieselbe
    Regel wie beim Anhaengen (``freigegebene_mounts``)."""
    from app.core.mounts import fremde_privatablagen, freigegebene_mounts

    stmt = select(SecondBrain).order_by(SecondBrain.name)
    if active_only:
        stmt = stmt.where(SecondBrain.is_active.is_(True))
    from app.models.team import Team

    # Projektwissen gehört zur Projektseite, nicht in die allgemeine Liste.
    projekt_slugs = set((await db.execute(select(Team.wissen_slug).where(Team.wissen_slug.is_not(None)))).scalars())
    rows = [b for b in (await db.execute(stmt)).scalars().all() if b.slug not in projekt_slugs]
    # Gespiegelte Bridge-Ordner anderer bleiben auch Administratoren verborgen (#1092).
    fremd = await fremde_privatablagen(db, [b.label for b in rows], user)
    rows = [b for b in rows if b.label not in fremd]
    erlaubt = await freigegebene_mounts(user, db)
    if erlaubt is not None:
        rows = [b for b in rows if b.label in erlaubt]
    stats = await _index_stats(db)
    return [_to_response(b, stats) for b in rows]


@router.post("/", response_model=BrainResponse, status_code=201)
async def create_brain(
    body: BrainCreate,
    user=Depends(require_auth),
    db: AsyncSession = Depends(get_db),
):
    """Create a Second Brain (admin only): provision the vault + register the mount."""
    _require_admin(user)
    if body.default_mode not in ("ro", "rw"):
        raise HTTPException(status_code=422, detail="default_mode must be 'ro' or 'rw'")
    slug = _slugify(body.slug or body.name)
    if not _SLUG_RE.match(slug):
        raise HTTPException(status_code=422, detail="invalid slug (use a-z, 0-9, _ or -)")

    label = f"brain-{slug}"
    host_path = f"{settings.secondbrain_base.rstrip('/')}/{slug}"
    container_path = f"{settings.secondbrain_container_base.rstrip('/')}/{slug}"

    standard = body.standard if body.standard in STANDARDS else "freeform"
    brain = SecondBrain(
        label=label, name=body.name.strip(), slug=slug,
        host_path=host_path, container_path=container_path,
        default_mode=body.default_mode, standard=standard,
        description=body.description, created_by=str(user.id),
    )
    db.add(brain)
    try:
        await db.commit()
    except Exception:
        await db.rollback()
        raise HTTPException(status_code=409, detail="A brain with this name/slug already exists")
    await db.refresh(brain)

    _provision_vault(host_path, brain.name, standard)
    await _audit(db, AuditEventType.BRAIN_CREATED, brain, user.id)
    # Mit Zahlen antworten, nicht ohne: die Oberflaeche nimmt diese Antwort als
    # neuen Stand der Karte. Ohne Statistik stuende dort nach jedem Anlegen oder
    # Umbenennen „0 Dateien im Index", obwohl der Index voll ist (#727).
    return _to_response(brain, await _index_stats(db))


@router.patch("/{brain_id}", response_model=BrainResponse)
async def update_brain(
    brain_id: int,
    body: BrainUpdate,
    user=Depends(require_auth),
    db: AsyncSession = Depends(get_db),
):
    """Update a Second Brain (admin only). Slug/path are immutable once created."""
    _require_admin(user)
    brain = await _brain_oder_404(db, brain_id, user)
    if body.name is not None:
        brain.name = body.name.strip()
    if body.default_mode is not None:
        if body.default_mode not in ("ro", "rw"):
            raise HTTPException(status_code=422, detail="default_mode must be 'ro' or 'rw'")
        if body.default_mode == "rw":
            _nicht_gespiegelt(brain)
        brain.default_mode = body.default_mode
    if body.description is not None:
        brain.description = body.description
    if body.is_active is not None:
        brain.is_active = body.is_active
    await db.commit()
    await db.refresh(brain)
    await _audit(db, AuditEventType.BRAIN_UPDATED, brain, user.id)
    return _to_response(brain, await _index_stats(db))


@router.delete("/{brain_id}")
async def delete_brain(
    brain_id: int,
    user=Depends(require_auth),
    db: AsyncSession = Depends(get_db),
):
    """Delete a Second Brain (admin only). The vault folder on disk is KEPT
    (data is never destroyed); only the mount registration is removed."""
    _require_admin(user)
    brain = await _brain_oder_404(db, brain_id, user)
    await _audit(db, AuditEventType.BRAIN_DELETED, brain, user.id)
    from app.core import bridge_wissen

    if bridge_wissen.ist_bridge_brain(brain):
        # Nur eine Kopie des Rechners — sie geht ganz (siehe bridge_wissen.entfernen).
        await bridge_wissen.entfernen(db, brain)
        return {"ok": True, "id": brain_id}
    await db.delete(brain)
    await db.commit()
    return {"ok": True, "id": brain_id}


# ── MCP exposure (admin-only): generate/rotate/disable the Bearer token ──

class BrainMcpTokenResponse(BaseModel):
    mcp_enabled: bool
    mcp_path: str
    token: str  # plaintext — shown to the admin ONCE, never stored or returned again


@router.post("/{brain_id}/mcp/token", response_model=BrainMcpTokenResponse)
async def brain_mcp_generate_token(
    brain_id: int,
    user=Depends(require_auth),
    db: AsyncSession = Depends(get_db),
):
    """Enable MCP for this brain and generate (or rotate) its Bearer token.

    The plaintext token is returned ONLY here — it is stored Fernet-encrypted and
    can never be read back. Calling again rotates it (old token stops working).
    """
    _require_admin(user)
    brain = await _brain_oder_404(db, brain_id, user)
    token = secrets.token_urlsafe(32)
    brain.mcp_token_encrypted = encrypt_token(token)
    brain.mcp_enabled = True
    await db.commit()
    await db.refresh(brain)
    await _audit(db, AuditEventType.BRAIN_UPDATED, brain, user.id)
    return BrainMcpTokenResponse(mcp_enabled=True, mcp_path=_mcp_path(brain), token=token)


@router.delete("/{brain_id}/mcp")
async def brain_mcp_disable(
    brain_id: int,
    user=Depends(require_auth),
    db: AsyncSession = Depends(get_db),
):
    """Disable MCP access for this brain and wipe its token."""
    _require_admin(user)
    brain = await _brain_oder_404(db, brain_id, user)
    brain.mcp_enabled = False
    brain.mcp_token_encrypted = None
    await db.commit()
    await _audit(db, AuditEventType.BRAIN_UPDATED, brain, user.id)
    return {"ok": True, "mcp_enabled": False}


# ── Vault file browser (admin-only): view + edit the Markdown content ──

def _vault_path(brain: SecondBrain, rel_path: str) -> str:
    """Resolve a vault-relative path to an absolute host path (jailed). Delegates
    to the shared vault helper so browser + MCP use identical jailing."""
    try:
        return vault.resolve_path(brain.host_path, rel_path)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


class BrainFileWrite(BaseModel):
    path: str
    content: str


@router.get("/{brain_id}/tree")
async def brain_tree(brain_id: int, user=Depends(require_auth), db: AsyncSession = Depends(get_db)):
    """Flat, sorted list of folders + files in the vault (excluding .git)."""
    _require_admin(user)
    brain = await _brain_oder_404(db, brain_id, user)
    base = os.path.realpath(brain.host_path)
    entries: list[dict] = []
    if os.path.isdir(base):
        for root, dirs, files in os.walk(base):
            dirs[:] = sorted(d for d in dirs if d != ".git")
            for d in dirs:
                rel = os.path.relpath(os.path.join(root, d), base)
                entries.append({"path": rel, "name": d, "type": "dir"})
            for f in sorted(files):
                rel = os.path.relpath(os.path.join(root, f), base)
                entries.append({"path": rel, "name": f, "type": "file"})
    entries.sort(key=lambda e: e["path"].lower())
    return {"entries": entries, "standard": getattr(brain, "standard", "freeform")}


@router.get("/{brain_id}/graph")
async def brain_graph(brain_id: int, user=Depends(require_auth), db: AsyncSession = Depends(get_db)):
    """Obsidian-style link graph of the vault.

    Nodes = Markdown notes, edges = [[wikilinks]] / relative .md links between
    them. Powers the 3D knowledge-graph view in the vault browser.
    """
    _require_admin(user)
    brain = await _brain_oder_404(db, brain_id, user)
    graph = vault.build_graph(brain.host_path)
    graph["brain"] = {"id": brain.id, "name": brain.name, "slug": brain.slug}
    return graph


@router.get("/{brain_id}/file")
async def brain_read_file(brain_id: int, path: str, user=Depends(require_auth), db: AsyncSession = Depends(get_db)):
    _require_admin(user)
    brain = await _brain_oder_404(db, brain_id, user)
    target = _vault_path(brain, path)
    if not os.path.isfile(target):
        raise HTTPException(status_code=404, detail="File not found")
    if os.path.getsize(target) > 2_000_000:
        raise HTTPException(status_code=413, detail="File too large to view")
    with open(target, encoding="utf-8", errors="replace") as fh:
        content = fh.read()
    return {"path": path, "content": content}


@router.put("/{brain_id}/file")
async def brain_write_file(brain_id: int, body: BrainFileWrite, user=Depends(require_auth), db: AsyncSession = Depends(get_db)):
    _require_admin(user)
    brain = await _brain_oder_404(db, brain_id, user)
    _nicht_gespiegelt(brain)
    target = _vault_path(brain, body.path)
    if target == os.path.realpath(brain.host_path) or os.path.isdir(target):
        raise HTTPException(status_code=400, detail="path must be a file")
    os.makedirs(os.path.dirname(target), exist_ok=True)
    with open(target, "w", encoding="utf-8") as fh:
        fh.write(body.content)
    try:
        os.chmod(target, 0o666)
    except OSError:
        pass
    try:
        await vault_indexer.index_file(db, brain.label, brain.host_path, body.path)
    except Exception as e:
        log.warning("reindex after write failed brain=%s path=%s: %s", brain.slug, vault.safe_log(body.path), vault.safe_log(e))
    return {"ok": True, "path": body.path}


@router.delete("/{brain_id}/file")
async def brain_delete_file(brain_id: int, path: str, user=Depends(require_auth), db: AsyncSession = Depends(get_db)):
    _require_admin(user)
    brain = await _brain_oder_404(db, brain_id, user)
    _nicht_gespiegelt(brain)
    target = _vault_path(brain, path)
    if os.path.isfile(target):
        os.remove(target)
    elif os.path.isdir(target):
        if os.listdir(target):
            raise HTTPException(status_code=400, detail="folder not empty")
        os.rmdir(target)
    else:
        raise HTTPException(status_code=404, detail="File not found")
    try:
        await vault_indexer.remove_file(db, brain.label, path)
    except Exception as e:
        log.warning("deindex after delete failed brain=%s path=%s: %s", brain.slug, vault.safe_log(path), vault.safe_log(e))
    return {"ok": True, "path": path}


@router.get("/{brain_id}/export")
async def brain_export(brain_id: int, user=Depends(require_auth), db: AsyncSession = Depends(get_db)):
    """Den ganzen Vault als ZIP herunterladen — Ordnerstruktur inklusive.

    Ein Obsidian-Vault ist nichts als Markdown in Ordnern; das Ergebnis laesst
    sich direkt in Obsidian oeffnen.
    """
    import io
    from fastapi.responses import Response

    _require_admin(user)
    brain = await _brain_oder_404(db, brain_id, user)

    puffer = io.BytesIO()
    anzahl = vault_transfer.exportiere_zip(brain.host_path, puffer)
    log.info("[Vault] Export %s: %d Dateien", brain.slug, anzahl)
    return Response(
        content=puffer.getvalue(),
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{brain.slug}.zip"'},
    )


@router.post("/{brain_id}/import")
async def brain_import(
    brain_id: int,
    file: UploadFile = File(...),
    replace: bool = False,
    user=Depends(require_auth),
    db: AsyncSession = Depends(get_db),
):
    """Einen Vault als ZIP einspielen.

    ``replace=false`` (Vorgabe) fuegt zusammen, ``replace=true`` macht den Vault
    zum Abbild des Archivs.

    **Danach wird neu indiziert** — sonst lauegen die Notizen zwar auf der
    Platte, waeren aber semantisch unauffindbar, weil die Einbettungen fehlen.
    Der Indexlauf ist inkrementell und entfernt auch die Eintraege geloeschter
    Dateien, passt also zu beiden Betriebsarten.
    """
    import io
    import zipfile

    _require_admin(user)
    brain = await _brain_oder_404(db, brain_id, user)
    _nicht_gespiegelt(brain)

    roh = await file.read()
    if len(roh) > vault_transfer.MAX_ARCHIV_BYTES:
        raise HTTPException(status_code=413, detail="Archiv zu gross")

    try:
        with zipfile.ZipFile(io.BytesIO(roh)) as archiv:
            bericht = vault_transfer.importiere_zip(brain.host_path, archiv, ersetzen=replace)
    except zipfile.BadZipFile:
        raise HTTPException(status_code=400, detail="Keine gueltige ZIP-Datei")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    # Die Einbettungen nachziehen. Ohne diesen Schritt findet die semantische
    # Suche die frisch importierten Notizen nicht — sie waeren fuer Agenten
    # praktisch unsichtbar.
    try:
        stats = await vault_indexer.reindex_vault(db, brain.label, brain.host_path)
    except Exception as e:  # noqa: BLE001
        log.warning("[Vault] Neuindizierung nach Import fehlgeschlagen brain=%s: %s",
                    brain.slug, vault.safe_log(e))
        stats = {"error": "Neuindizierung fehlgeschlagen — bitte manuell anstossen"}

    log.info("[Vault] Import %s: %d Dateien, %d geloescht", brain.slug,
             bericht.geschrieben, bericht.geloescht)
    return {"ok": True, "brain": brain.slug, **bericht.als_dict(), "index": stats}


@router.post("/{brain_id}/reindex")
async def brain_reindex(brain_id: int, user=Depends(require_auth), db: AsyncSession = Depends(get_db)):
    """Re-chunk + re-embed a whole vault into ``vault_chunks`` for hybrid search.

    Idempotent and incremental: unchanged files are skipped (file-hash check),
    files that vanished are pruned. Run after bulk-editing the vault on disk or
    to backfill an existing vault the first time.
    """
    _require_admin(user)
    brain = await _brain_oder_404(db, brain_id, user)
    stats = await vault_indexer.reindex_vault(db, brain.label, brain.host_path)
    return {"ok": True, "brain": brain.slug, **stats}


# ─────────────────────────── Agenten-Zugang (#727) ───────────────────────────
# Bis hierher suchte ein Agent seinen Second Brain mit einem Substring-Zaehler
# ueber die gemounteten Dateien, obwohl die Hybrid-Suche (Vektor + Volltext,
# RRF) bereits lief — nur fuer den externen MCP-Endpunkt und die Sprach-Sitzung.
# Diese beiden Endpunkte geben dem Agenten denselben Weg.
#
# Sicherheitsinvariante: Der Umfang kommt AUSSCHLIESSLICH aus dem Token. Ein
# Label aus der Anfrage wird nur noch gegen die ohnehin zugewiesene Menge
# geprueft, nie zur Erweiterung benutzt.


class AgentBrainWrite(BaseModel):
    path: str
    content: str


async def _agent_brains(
    db: AsyncSession, agent_id: str, label: str | None = None
) -> list[tuple[SecondBrain, str]]:
    """Die Second Brains, die DIESER Agent anfassen darf — plus effektiver Modus.

    Reihenfolge wie beim Containerstart (``agent_manager``): zugewiesene Labels
    aus ``Agent.config["mounts"]`` gegen den effektiven Katalog schneiden
    (``resolve_agent_mounts`` setzt dabei die strengere von Katalog- und
    Agenten-Kappung durch), dann auf aktive ``SecondBrain``-Eintraege filtern.

    ``label`` darf den Umfang nur VERKLEINERN. Ein Label ausserhalb der
    Zuweisung ergibt 404 und nicht eine leere Trefferliste: ein Tippfehler soll
    nicht wie „nichts gefunden" aussehen (Spec 3.1).
    """
    from app.core.mounts import get_effective_catalog, resolve_agent_mounts

    agent = await db.get(Agent, agent_id)
    if agent is None:
        raise HTTPException(status_code=404, detail="Agent nicht gefunden")
    config = agent.config or {}
    catalog = await get_effective_catalog(db)
    entries = resolve_agent_mounts(
        list(config.get("mounts") or []), catalog, dict(config.get("mount_modes") or {})
    )
    modi = {e.label: e.mode for e in entries}
    rows = (
        await db.execute(select(SecondBrain).where(SecondBrain.is_active.is_(True)).order_by(SecondBrain.slug))
    ).scalars().all()
    paare = [(b, modi[b.label]) for b in rows if b.label in modi]

    if label:
        gewaehlt = [(b, m) for b, m in paare if label in (b.label, b.slug)]
        if not gewaehlt:
            raise HTTPException(
                status_code=404,
                detail=(
                    f"Kein zugewiesener Second Brain '{label}'. Verfuegbar: "
                    + (", ".join(b.slug for b, _ in paare) or "(keiner)")
                ),
            )
        return gewaehlt
    return paare


@router.get("/agent/search")
async def agent_brain_search(
    q: str = Query(..., min_length=1),
    limit: int = Query(10, ge=1, le=50),
    label: str | None = None,
    auth: dict = Depends(verify_agent_token),
    db: AsyncSession = Depends(get_db),
):
    """Hybrid-Suche ueber die dem Agenten zugewiesenen Second Brains.

    ``mode`` ist die Gesamtaussage ueber alle durchsuchten Vaults: „hybrid" nur,
    wenn JEDER von ihnen aus dem Index geantwortet hat. Faellt einer auf grep
    zurueck oder scheitert er, steht hier „grep" — sonst verdeckt ein gesunder
    Vault den driftenden. ``brains`` nennt den Modus je Vault.

    **503, wenn gar nichts durchsucht werden konnte.** Das ist der Unterschied
    zwischen „ich habe gesucht und nichts gefunden" und „ich habe nicht gesucht".
    Nur die zweite Aussage darf den Agenten auf seinen lokalen Dateiweg schicken
    (``api_client`` liest 5xx als „nicht erreichbar") — und genau dann ist der
    lokale Weg richtig, denn der Vault liegt im Container und bleibt lesbar.
    Antwortete der Endpunkt hier mit 200 und leerer Trefferliste, waere eine
    kaputte Hybrid-Suche beim Agenten als ehrliches „nichts gefunden"
    angekommen, und der Rueckfall haette nie gegriffen.
    """
    brains = await _agent_brains(db, auth["agent_id"], label)
    if not brains:
        # Kein zugewiesener Brain in der Datenbank — aber vielleicht einer aus
        # dem statischen Katalog (``AGENT_MOUNT_CATALOG``) oder einer, der
        # waehrend der Laufzeit des Containers deaktiviert wurde. Beide sind im
        # Container weiter gemountet und lesbar, also lokal weitersuchen lassen.
        raise HTTPException(
            status_code=503,
            detail="Kein zugewiesener Second Brain in der Verwaltung — lokal weitersuchen.",
        )
    # ALLE Modellwerte VOR der Schleife in einfache Tupel aufloesen. Grund: ein
    # ``rollback()`` entwertet jedes Objekt der Sitzung, und der naechste
    # Attributzugriff ist dann ein Nachladevorgang — der in einer asynchronen
    # Sitzung nicht beilaeufig gehen kann und mit ``MissingGreenlet`` abbricht.
    # Genau im Fehlerzweig, in dem wir aufraeumen und protokollieren wollen,
    # waere die Vault-Isolation damit hin, bevor sie greift.
    ziele = [(b.slug, b.label, b.host_path) for b, _m in brains]

    je_brain: list[dict] = []
    pro_brain: list[list[dict]] = []
    for slug, label_, host_path in ziele:
        try:
            modus, treffer = await vault_search.hybrid_search_with_mode(
                db, label_, host_path, q, limit
            )
        except Exception as e:
            je_brain.append({"brain": slug, "mode": "error"})
            # Die Sitzung ZURUECKROLLEN, bevor der naechste Vault dran ist.
            # Ohne das kippt der naechste zwangslaeufig mit (auf Postgres
            # deterministisch, ``PendingRollbackError``) — ein einzelner kaputter
            # Vault nimmt dann alle uebrigen mit und das Ergebnis sieht wie eine
            # ehrliche Leerantwort aus.
            try:
                await db.rollback()
            except Exception:
                pass
            log.warning("Agenten-Suche in %s fehlgeschlagen: %s", slug, vault.safe_log(e))
            continue
        je_brain.append({"brain": slug, "mode": modus, "hits": len(treffer)})
        pro_brain.append([{"brain": slug, **h} for h in treffer])

    if je_brain and all(b["mode"] == "error" for b in je_brain):
        raise HTTPException(
            status_code=503,
            detail=(
                "Suche in keinem der zugewiesenen Second Brains moeglich "
                f"({len(je_brain)} versucht) — lokal weitersuchen."
            ),
        )

    # Zusammenfuehren NACH RANG, nicht nach Punktzahl: die Punktzahl aus dem
    # Index ist ein RRF-Wert (~0,01), die aus grep ein Trefferzaehler (~20).
    # Nach Punktzahl sortiert draengt sich ein grep-Vault immer vor einen
    # gesunden Index-Vault. Innerhalb eines Vaults ist die Reihenfolge schon
    # richtig, also abwechselnd entnehmen.
    hits: list[dict] = []
    for i in range(max((len(x) for x in pro_brain), default=0)):
        for liste in pro_brain:
            if i < len(liste):
                hits.append(liste[i])
    # Ein gescheiterter Vault darf die Gesamtaussage NICHT verbessern: „hybrid"
    # heisst „alle aus dem Index", und ein Vault, der gar nicht geantwortet hat,
    # war nicht aus dem Index.
    gesamt = ("hybrid" if je_brain and all(b["mode"] == "hybrid" for b in je_brain)
              else "grep")
    return {"mode": gesamt, "brains": je_brain, "hits": hits[:limit]}


@router.post("/agent/write")
async def agent_brain_write(
    body: AgentBrainWrite,
    auth: dict = Depends(verify_agent_token),
    db: AsyncSession = Depends(get_db),
):
    """Schreibt eine Datei in einen zugewiesenen Second Brain UND indexiert sie.

    Derselbe Zweischritt wie Admin-UI, externer MCP und Sprach-Sitzung:
    ``vault.write_file()`` (atomar, mit Ausbruchschutz) und direkt danach
    ``vault_indexer.index_file()``. Der Agent war bis #727 der einzige
    Schreibweg ohne Index.

    ``path`` traegt den Slug des Ziel-Brains als erstes Segment — genau die
    Form, die das Agentenwerkzeug schon immer erwartet
    (``it_operations/Drucker/x17137.md``).
    """
    roh = (body.path or "").strip().lstrip("/")
    if not roh:
        raise HTTPException(status_code=422, detail="'path' fehlt")
    slug, _, rest = roh.partition("/")
    if not rest:
        raise HTTPException(
            status_code=422,
            detail="'path' braucht den Brain-Slug als erstes Segment, z. B. 'it_operations/Drucker/x17137.md'",
        )
    brains = await _agent_brains(db, auth["agent_id"], slug)
    brain, modus = brains[0]
    # Gleiche Vorsichtsmassnahme wie im Suchendpunkt: nach einem gescheiterten
    # Indexierungsversuch waere ``brain.slug`` ein Nachladevorgang.
    brain_slug, brain_label, brain_host = brain.slug, brain.label, brain.host_path
    if modus == "ro":
        raise HTTPException(
            status_code=403,
            detail=f"Second Brain '{brain_slug}' ist nur lesend eingebunden — Schreiben nicht erlaubt.",
        )
    try:
        ergebnis = vault.write_file(brain_host, rest, body.content)
    except (ValueError, FileNotFoundError) as e:
        raise HTTPException(status_code=400, detail=str(e))
    except OSError as e:
        raise HTTPException(status_code=500, detail=f"Schreiben fehlgeschlagen: {e}")
    chunks = 0
    try:
        chunks = await vault_indexer.index_file(db, brain_label, brain_host, rest)
    except Exception as e:
        # Geschrieben ist geschrieben — der 5-Minuten-Durchlauf holt den Index nach.
        log.warning(
            "Index nach Agenten-Schreibzugriff fehlgeschlagen brain=%s path=%s: %s",
            brain_slug, vault.safe_log(rest), vault.safe_log(e),
        )
    return {
        "ok": True,
        "brain": brain_slug,
        "path": rest,
        "created": bool(ergebnis.get("created")),
        "bytes": ergebnis.get("bytes"),
        "indexed_chunks": chunks,
    }
