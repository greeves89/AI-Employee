"""Wie selbststaendig darf ein Nutzer seinen Agenten machen?

Jede Rolle traegt ``max_autonomy_level`` (None = unbegrenzt; Standard: Admin und
Manager unbegrenzt, Mitglied L3, Betrachter/ohne Rolle L1). Ab L4 handelt ein
Agent ohne Rueckfrage nach aussen — E-Mail/M365, externe APIs, git push, Kaeufe;
bis L3 bleibt alles mit Aussenwirkung freigabepflichtig. Deshalb liegt die Grenze
fuer Mitglieder dort.

EINE Pruefung fuer alle Wege, auf denen sich Autonomie aendern laesst: Stufe
(Oberflaeche und Sprache), feine Matrix, Zugriffs-Richtlinie, Sudo-Pakete,
agentenbezogene Freigaberegeln und das Anlegen eines Agenten
(``AgentManager.create_agent`` — dort laufen API, Vorlage, Verwaltung und
Branchenpakete zusammen). Ungueltige Grenzen gelten als L1 — geschlossen, nicht
offen.

Bestandsschutz: Wer schon mehr hat, als seine Rolle heute erlaubt, verliert
nichts. Gesperrt wird nur das ERHOEHEN — gemessen am bisherigen Stand
(``bisher``). Welche Agenten ueber der Grenze stehen, zeigt
``agenten_ueber_der_grenze`` dem Administrator.

Computer-Use: Shell auf dem Rechner des Menschen, Tastatur-/Mikrofon-Mitschnitt
und Browser in einer angemeldeten Sitzung (``SENSIBLE_COMPUTER_USE``) schaltet ein
begrenzter Nutzer nur frei, wenn die Matrix seines Agenten ``shell_exec`` ohne
Rueckfrage erlaubt — es ist mindestens so viel wie Shell im Container.

Voller Root-Zugriff (``full-access``) entsteht nie nebenbei: nur mit
ausdruecklicher Bestaetigung (``root_bestaetigt``) und nur fuer Rollen ohne
Grenze. Vorlagen duerfen ihn vorschlagen, aber nicht still vergeben.
"""
from __future__ import annotations

import logging

from fastapi import HTTPException

from app.core import autonomy_matrix as am

logger = logging.getLogger(__name__)

STUFEN = ("l1", "l2", "l3", "l4")
_RANG = {am.DENY: 0, am.ASK: 1, am.ALLOW: 2}

# Computer-Use-Gruppen (``api/computer_use.py::CAPABILITY_GROUPS``), die ueber
# Bildschirm, Maus und Tastatur hinausgehen: Shell auf dem Rechner, Mitschnitt von
# Eingaben und Mikrofon, Browser (eigenes Profil bzw. die ECHTE angemeldete
# Sitzung). Alle standardmaessig aus.
SENSIBLE_COMPUTER_USE = frozenset({"shell", "input_capture", "voice_capture", "browser", "ego_browser"})


async def autonomie_obergrenze(user, db) -> str | None:
    """Die hoechste erlaubte Stufe fuer diesen Nutzer, ``None`` = unbegrenzt."""
    from app.core.permissions import get_effective_permissions

    perms = await get_effective_permissions(user, db)
    if "max_autonomy_level" not in perms:
        # Unbegrenzt ist nur ein ausdrueckliches None (Admin, Manager, Rolle
        # "unbegrenzt"). Fehlt die Angabe ganz, schliesst das — nicht offen.
        return "l1"
    return normalisiere_grenze(perms.get("max_autonomy_level"))


def normalisiere_grenze(wert) -> str | None:
    """``None`` bleibt unbegrenzt; alles Unbekannte schliesst auf L1."""
    if wert is None:
        return None
    wert = str(wert).lower()
    return wert if wert in STUFEN else "l1"


async def grenze_des_besitzers(db, user_id: str | None) -> str | None:
    """Die Grenze, die fuer einen Agenten dieses Besitzers gilt.

    Ohne Besitzer (interne Wege, Einrichtungsmodus) gibt es keine Rolle, an der
    sich eine Grenze festmachen liesse. Ein Besitzer, den es nicht (mehr) gibt,
    bekommt die engste Stufe — geschlossen, nicht offen.
    """
    if not user_id:
        return None
    from app.models.user import User

    besitzer = await db.get(User, user_id)
    if besitzer is None:
        return "l1"
    return await autonomie_obergrenze(besitzer, db)


def _ueber(stufe: str, grenze: str | None) -> bool:
    return grenze is not None and STUFEN.index(stufe) > STUFEN.index(grenze)


def _absage(grenze: str, zusatz: str = "") -> HTTPException:
    return HTTPException(
        status_code=403,
        detail=f"Deine Rolle erlaubt höchstens Stufe {grenze.upper()}{zusatz}.",
    )


def _vergleichsstufe(stufe: str | None) -> str | None:
    """Eine eigene Matrix ("custom") ohne gespeicherte Werte wirkt wie L3."""
    stufe = (stufe or "").lower()
    if stufe in STUFEN:
        return stufe
    return "l3" if stufe == "custom" else None


async def pruefe_stufe(user, db, stufe: str, bisher: str | None = None) -> None:
    """Stufe ueber der Grenze → 403. Bestandsschutz: Halten oder Senken geht immer."""
    stufe = (stufe or "").lower()
    if stufe not in STUFEN:
        return
    grenze = await autonomie_obergrenze(user, db)
    if not _ueber(stufe, grenze):
        return
    bisher = (bisher or "").lower()
    if bisher in STUFEN and STUFEN.index(stufe) <= STUFEN.index(bisher):
        return
    raise _absage(grenze)


def zu_freie_zellen(matrix: dict, grenze: str | None, bisher: dict | None = None) -> list[str]:
    """Zellen, die freizuegiger sind als die Voreinstellung der Grenzstufe — und,
    falls ``bisher`` angegeben ist, auch freizuegiger als bisher."""
    if grenze is None:
        return []
    erlaubt = am.matrix_for_level(grenze)
    return sorted(
        k for k, v in (matrix or {}).items()
        if _RANG.get(v, 2) > _RANG.get(erlaubt.get(k, am.ASK), 1)
        and (bisher is None or _RANG.get(v, 2) > _RANG.get(bisher.get(k, am.ASK), 1))
    )


async def pruefe_matrix(user, db, matrix: dict, bisher: dict | None = None) -> None:
    """Keine Zelle freizuegiger als die Voreinstellung der Grenzstufe."""
    grenze = await autonomie_obergrenze(user, db)
    zu_frei = zu_freie_zellen(matrix, grenze, bisher)
    if zu_frei:
        raise _absage(grenze, f" — zu freizügig: {', '.join(zu_frei)}")


async def pruefe_sudo_pakete(user, db, pakete, bisher=None) -> None:
    """Sudo-Pakete (bis „Voller Root-Zugriff") setzt nur, wessen Rolle keine Grenze hat.

    Bestandsschutz: Pakete, die der Agent schon hat, darf auch ein begrenzter
    Nutzer behalten oder abwaehlen — nur neue kommen nicht dazu.
    """
    neu = set(pakete or ()) - set(bisher or ())
    if not neu:
        return
    if await autonomie_obergrenze(user, db) is not None:
        raise HTTPException(status_code=403, detail="Sudo-Pakete setzt ein Administrator.")


def zu_freie_computer_use(gruppen, matrix: dict, grenze: str | None, bisher=None) -> list[str]:
    """Sensible Computer-Use-Gruppen, die ein begrenzter Nutzer nicht (neu) setzen
    darf: nur ohne Grenze oder wenn die Matrix ``shell_exec`` ohne Rueckfrage
    erlaubt. Bestandsschutz: was in ``bisher`` schon steht, bleibt erlaubt."""
    if grenze is None or (matrix or {}).get("shell_exec") == am.ALLOW:
        return []
    return sorted((set(gruppen or ()) & SENSIBLE_COMPUTER_USE) - set(bisher or ()))


async def pruefe_computer_use(user, db, gruppen, matrix: dict, bisher=None) -> None:
    """Computer-Use-Standard eines Agenten: sensible Gruppen nur innerhalb der
    Grenze (siehe ``zu_freie_computer_use``) — sonst 403."""
    if not set(gruppen or ()) & SENSIBLE_COMPUTER_USE:
        return
    grenze = await autonomie_obergrenze(user, db)
    zu_frei = zu_freie_computer_use(gruppen, matrix, grenze, bisher)
    if zu_frei:
        raise HTTPException(
            status_code=403,
            detail=("Diese Computer-Use-Fähigkeiten brauchen einen Agenten, der Befehle "
                    f"ohne Rückfrage ausführen darf: {', '.join(zu_frei)}."),
        )


def pruefe_root(pakete, bisher=None, root_bestaetigt: bool = False) -> None:
    """Neuer voller Root-Zugriff nur mit ausdruecklicher Bestaetigung."""
    if am.PKG_FULL_ACCESS in (pakete or ()) and am.PKG_FULL_ACCESS not in (bisher or ()) \
            and not root_bestaetigt:
        raise HTTPException(
            status_code=400,
            detail="Voller Root-Zugriff braucht eine ausdrückliche Bestätigung.",
        )


def erlaubte_kategorien(grenze: str | None) -> set[str] | None:
    """Freigaberegel-Kategorien, die bis zur Grenze ohne Rueckfrage laufen
    (``None`` = alle). Eine agentenbezogene Regel IST eine Freischaltung: der
    Werkzeug-Executor nimmt ihre Kategorie in die Erlaubt-Liste auf."""
    if grenze is None:
        return None
    return am.allowed_categories_from_matrix(am.matrix_for_level(grenze))


async def pruefe_freigaberegel(user, db, kategorie: str | None) -> None:
    """Eine agentenbezogene Freigaberegel darf nichts freischalten, was ueber der
    Grenze liegt. Unbekannte Kategorien zaehlen als ueber der Grenze."""
    grenze = await autonomie_obergrenze(user, db)
    erlaubt = erlaubte_kategorien(grenze)
    if erlaubt is None or (kategorie or "custom") in erlaubt:
        return
    raise _absage(grenze, f" — die Kategorie „{kategorie or 'custom'}“ schaltet mehr frei")


async def gekappte_stufe(user, db, stufe: str | None) -> str:
    """Die gewuenschte Stufe, hoechstens die Grenze (Vorgabe fuer neue Agenten)."""
    stufe = (stufe or "l3").lower()
    grenze = await autonomie_obergrenze(user, db)
    return grenze if stufe in STUFEN and _ueber(stufe, grenze) else stufe


async def fuer_neuen_agenten(
    db,
    user_id: str | None,
    stufe: str | None,
    pakete: list[str] | None,
    root_bestaetigt: bool = False,
) -> tuple[str, list[str] | None]:
    """Stufe und Sudo-Pakete, mit denen ein neuer Agent tatsaechlich entsteht.

    - ohne Stufenangabe: L3, hoechstens die Grenze des Besitzers
    - ausdruecklich ueber der Grenze: 403
    - begrenzter Besitzer: keine handverlesenen Pakete — der Container folgt der
      (erlaubten) Stufe (``None`` = automatisch)
    - ``full-access`` nur mit ``root_bestaetigt``; sonst faellt er weg, die
      uebrigen Pakete der Vorlage bleiben
    """
    grenze = await grenze_des_besitzers(db, user_id)
    if stufe is None:
        stufe = grenze if _ueber("l3", grenze) else "l3"
    else:
        stufe = stufe.lower()
        vergleich = _vergleichsstufe(stufe)
        if vergleich is not None and _ueber(vergleich, grenze):
            raise _absage(grenze)

    if pakete is None:
        return stufe, None
    if grenze is not None:
        if pakete:
            logger.info("Neuer Agent: Sudo-Pakete %s entfallen — die Rolle des Besitzers "
                        "hat eine Autonomie-Grenze, der Container folgt der Stufe.", pakete)
        return stufe, None
    pakete = list(pakete)
    if am.PKG_FULL_ACCESS in pakete and not root_bestaetigt:
        logger.info("Neuer Agent: voller Root-Zugriff entfaellt — nicht ausdruecklich bestaetigt.")
        pakete = [p for p in pakete if p != am.PKG_FULL_ACCESS]
    return stufe, pakete


def ueberschreitung(stufe: str | None, access_policy: dict | None, grenze: str | None) -> list[str]:
    """Woran ein bestehender Agent ueber der Grenze seines Besitzers liegt (leer = nichts)."""
    if grenze is None:
        return []
    policy = access_policy or {}
    stufe = (stufe or "l3").lower()
    gruende: list[str] = []
    if stufe in STUFEN and _ueber(stufe, grenze):
        gruende.append(f"Stufe {stufe.upper()}")
    else:
        matrix = am.normalize_matrix(policy.get("autonomy_matrix"), stufe)
        zu_frei = zu_freie_zellen(matrix, grenze)
        if zu_frei:
            gruende.append(f"Matrix freizügiger: {', '.join(zu_frei)}")
    zuviel = sorted(
        set(am.effective_permissions(policy, stufe))
        - set(am.derive_permissions(am.matrix_for_level(grenze)))
    )
    if zuviel:
        gruende.append(f"Sudo: {', '.join(zuviel)}")
    computer_use = zu_freie_computer_use(
        am.computer_use_default_capabilities(policy),
        am.normalize_matrix(policy.get("autonomy_matrix"), stufe), grenze)
    if computer_use:
        gruende.append(f"Computer-Use: {', '.join(computer_use)}")
    return gruende


async def agenten_ueber_der_grenze(db) -> list[dict]:
    """Alle Agenten, die mehr duerfen, als die Rolle ihres Besitzers heute erlaubt.

    Sie bleiben, wie sie sind (Bestandsschutz) — die Liste ist fuer den
    Administrator, der entscheidet, ob er nachzieht.
    """
    from sqlalchemy import select

    from app.models.agent import Agent
    from app.models.user import User

    zeilen = (await db.execute(
        select(Agent.id, Agent.name, Agent.user_id, Agent.autonomy_level, Agent.access_policy)
        .where(Agent.user_id.is_not(None))
        .order_by(Agent.name)
    )).all()
    grenzen: dict[str, tuple[str | None, object]] = {}
    ergebnis = []
    for agent_id, name, user_id, stufe, policy in zeilen:
        if user_id not in grenzen:
            besitzer = await db.get(User, user_id)
            grenze = await autonomie_obergrenze(besitzer, db) if besitzer else "l1"
            grenzen[user_id] = (grenze, besitzer)
        grenze, besitzer = grenzen[user_id]
        gruende = ueberschreitung(stufe, policy, grenze)
        if gruende:
            ergebnis.append({
                "agent_id": agent_id,
                "agent_name": name,
                "user_id": user_id,
                "user_name": getattr(besitzer, "name", None),
                "user_email": getattr(besitzer, "email", None),
                "grenze": grenze,
                "autonomy_level": stufe,
                "gruende": gruende,
            })
    return ergebnis
