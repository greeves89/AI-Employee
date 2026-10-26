"""Die Bridge als Mitdenker (#1019): Regeln, Tagesdeckel, Beobachter-Anweisung.

Die Bridge sieht mit (Capability ``observe``) und meldet zwei Dinge:

* ``rule_hit`` — eine Regel des Nutzers trifft auf aktive Anwendung und
  Fenstertitel. Abgeglichen wird LOKAL in der Bridge (``bridge.regel_passt``,
  gleiche Logik wie ``regel_passt`` hier — ``test_bridge_mitdenker`` hält beide
  zusammen). Kein Modellaufruf, kein Token.
* ``observation`` — der Bildschirm hat sich deutlich verändert und wieder
  beruhigt. Ist Mitdenken an und der Tagesdeckel nicht erreicht, bekommt der
  Agent das Bild mit ``beobachter_anweisung`` und antwortet mit einem Angebot
  oder ``NICHTS``.

Beides endet im selben Overlay-Vorschlag; erst ein Ja gibt dem Agenten den
Auftrag. Der Agent bekommt das alles über seinen normalen Chat
(``agent_chat_bridge``) — damit gilt es in jeder Laufzeit gleich, und das Budget
des Agenten prüft wie bei jeder Chatnachricht mit.
"""

from __future__ import annotations

import json
import re
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import func, select

MAX_REGELN_JE_NUTZER = 50
MAX_AGENT_REGELN_JE_TAG = 10
#: Mitdenk-Läufe (Bild an den Agenten) je Nutzer und Tag, wenn nichts eingestellt ist.
STANDARD_DECKEL = 40
MAX_DECKEL = 500

_FELDLAENGEN = {"name": 120, "wenn_app": 120, "wenn_titel": 200, "angebot": 300, "auftrag": 4000}


class RegelFehler(ValueError):
    """Eine Regel ist ungültig oder darf nicht angelegt werden — Text für den Menschen."""


def _norm(wert: str | None) -> str:
    return " ".join(str(wert or "").split()).casefold()


def regel_passt(regel: dict, app: str | None, titel: str | None) -> bool:
    """Trifft die Regel auf diese Anwendung und diesen Fenstertitel zu?

    „Enthält“, ohne Gross-/Kleinschreibung und doppelte Leerzeichen: Der Mensch
    schreibt „Chrome“, die Anwendung heisst „Google Chrome“. Eine Regel ohne jede
    Bedingung trifft NIE — sonst böte sie auf jedem Bildschirm etwas an.
    Gleiche Logik in ``computer-use-bridge/bridge.py`` (``regel_passt``).
    """
    if not regel.get("aktiv", True):
        return False
    wenn_app, wenn_titel = _norm(regel.get("wenn_app")), _norm(regel.get("wenn_titel"))
    if not wenn_app and not wenn_titel:
        return False
    if wenn_app and wenn_app not in _norm(app):
        return False
    if wenn_titel and wenn_titel not in _norm(titel):
        return False
    return True


def regel_dict(r) -> dict:
    def iso(t):
        return (t if t.tzinfo else t.replace(tzinfo=timezone.utc)).isoformat() if t else None

    return {
        "id": r.id, "name": r.name, "agent_id": r.agent_id,
        "wenn_app": r.wenn_app or "", "wenn_titel": r.wenn_titel or "",
        "angebot": r.angebot, "auftrag": r.auftrag, "aktiv": bool(r.aktiv),
        "von": r.von, "treffer": int(r.treffer or 0), "zuletzt": iso(r.zuletzt_at),
    }


def regel_fuer_bridge(r) -> dict:
    """Was die Bridge zum Abgleichen braucht — ohne den Auftrag: der bleibt auf dem
    Server und geht erst nach einem Ja an den Agenten."""
    return {"id": r.id, "wenn_app": r.wenn_app or "", "wenn_titel": r.wenn_titel or "",
            "angebot": r.angebot, "aktiv": bool(r.aktiv)}


def _felder_pruefen(felder: dict) -> dict:
    sauber = {}
    for feld, laenge in _FELDLAENGEN.items():
        if feld in felder and felder[feld] is not None:
            sauber[feld] = str(felder[feld]).strip()[:laenge]
    return sauber


async def regeln_laden(db, user_id: str, nur_aktive: bool = False) -> list:
    from app.models.bridge_regel import BridgeRegel

    q = select(BridgeRegel).where(BridgeRegel.user_id == str(user_id))
    if nur_aktive:
        q = q.where(BridgeRegel.aktiv.is_(True))
    return list((await db.execute(q.order_by(BridgeRegel.id))).scalars().all())


async def regel_holen(db, user_id: str, regel_id: int):
    """Die Regel — nur, wenn sie diesem Nutzer gehört. Sonst None (nie fremde zeigen)."""
    from app.models.bridge_regel import BridgeRegel

    r = await db.get(BridgeRegel, regel_id)
    if r is None or r.user_id != str(user_id):
        return None
    return r


async def _agent_gehoert(db, user_id: str, agent_id: str) -> bool:
    from app.models.agent import Agent

    agent = await db.get(Agent, agent_id)
    return agent is not None and str(agent.user_id) == str(user_id)


async def regel_anlegen(db, user_id: str, felder: dict, von: str = "nutzer"):
    from app.models.bridge_regel import BridgeRegel

    sauber = _felder_pruefen(felder)
    if not sauber.get("wenn_app") and not sauber.get("wenn_titel"):
        raise RegelFehler("Die Regel braucht eine Bedingung: Anwendung oder Fenstertitel.")
    if not sauber.get("angebot"):
        raise RegelFehler("Was soll im Overlay angeboten werden? Das Angebot fehlt.")
    if not sauber.get("auftrag"):
        raise RegelFehler("Was soll der Agent nach einem Ja tun? Der Auftrag fehlt.")
    agent_id = felder.get("agent_id") or None
    if agent_id and not await _agent_gehoert(db, user_id, str(agent_id)):
        raise RegelFehler("Diesen Agenten gibt es nicht oder er gehört dir nicht.")

    anzahl = await db.scalar(select(func.count(BridgeRegel.id)).where(
        BridgeRegel.user_id == str(user_id))) or 0
    if anzahl >= MAX_REGELN_JE_NUTZER:
        raise RegelFehler(f"Es gibt schon {anzahl} Regeln — erst welche löschen.")
    if von != "nutzer":
        # Ein Agent, den eine Webseite oder Mail zu Regeln verleitet, darf nicht
        # unbegrenzt welche anlegen.
        heute = await db.scalar(select(func.count(BridgeRegel.id)).where(
            BridgeRegel.von == von,
            BridgeRegel.created_at >= datetime.now(timezone.utc) - timedelta(days=1))) or 0
        if heute >= MAX_AGENT_REGELN_JE_TAG:
            raise RegelFehler("Für heute sind genug Regeln angelegt.")

    r = BridgeRegel(
        user_id=str(user_id), agent_id=str(agent_id) if agent_id else None,
        name=sauber.get("name") or sauber["angebot"][:120],
        wenn_app=sauber.get("wenn_app", ""), wenn_titel=sauber.get("wenn_titel", ""),
        angebot=sauber["angebot"], auftrag=sauber["auftrag"], aktiv=True, von=von[:80],
        treffer=0,
    )
    db.add(r)
    return r


async def regel_aendern(db, user_id: str, regel_id: int, felder: dict):
    r = await regel_holen(db, user_id, regel_id)
    if r is None:
        return None
    sauber = _felder_pruefen(felder)
    if "agent_id" in felder:
        agent_id = felder.get("agent_id") or None
        if agent_id and not await _agent_gehoert(db, user_id, str(agent_id)):
            raise RegelFehler("Diesen Agenten gibt es nicht oder er gehört dir nicht.")
        r.agent_id = str(agent_id) if agent_id else None
    for feld, wert in sauber.items():
        setattr(r, feld, wert)
    if "aktiv" in felder and felder["aktiv"] is not None:
        r.aktiv = bool(felder["aktiv"])
    if not (r.wenn_app or "").strip() and not (r.wenn_titel or "").strip():
        raise RegelFehler("Die Regel braucht eine Bedingung: Anwendung oder Fenstertitel.")
    if not (r.angebot or "").strip() or not (r.auftrag or "").strip():
        raise RegelFehler("Angebot und Auftrag dürfen nicht leer sein.")
    return r


async def regel_loeschen(db, user_id: str, regel_id: int) -> bool:
    r = await regel_holen(db, user_id, regel_id)
    if r is None:
        return False
    await db.delete(r)
    return True


# ── Tagesdeckel ──────────────────────────────────────────────────────────────

def _deckel_schluessel(user_id: str, tag: date | None = None) -> str:
    return f"bridge:mitdenken:{user_id}:{(tag or datetime.now(timezone.utc).date()).isoformat()}"


async def lauf_erlaubt(redis_client, user_id: str, deckel: int) -> bool:
    """Zählt einen Mitdenk-Lauf und sagt, ob er noch in den Tagesdeckel passt.

    Ohne Redis kein Lauf: Ein Deckel, der still nicht zählt, ist keiner.
    """
    if redis_client is None or deckel <= 0:
        return False
    schluessel = _deckel_schluessel(str(user_id))
    try:
        stand = await redis_client.incr(schluessel)
        if stand == 1:
            await redis_client.expire(schluessel, 2 * 24 * 3600)
    except Exception:  # noqa: BLE001 — im Zweifel kein Lauf
        return False
    return int(stand) <= int(deckel)


async def laeufe_heute(redis_client, user_id: str) -> int:
    if redis_client is None:
        return 0
    try:
        return int(await redis_client.get(_deckel_schluessel(str(user_id))) or 0)
    except Exception:  # noqa: BLE001
        return 0


def deckel_lesen(wert) -> int:
    try:
        return max(0, min(MAX_DECKEL, int(wert)))
    except (TypeError, ValueError):
        return STANDARD_DECKEL


# ── Beobachter-Anweisung und Antwort ─────────────────────────────────────────

def beobachter_anweisung(app: str, titel: str) -> str:
    """Was der Agent zum Bildschirmfoto bekommt. Kurz, damit der Lauf billig bleibt."""
    return (
        "[Bridge-Beobachter] Anbei der aktuelle Bildschirm deines Menschen "
        f"(Anwendung: {app or 'unbekannt'}, Fenster: {titel or 'unbekannt'}).\n"
        "Gibt es GENAU JETZT etwas Konkretes, das du ihm auf diesem Bildschirm abnehmen "
        "kannst und das zu dem passt, was du über ihn und seine Arbeit weisst? "
        "Nur anbieten, was wirklich hilft — im Zweifel nichts.\n"
        "Führe noch NICHTS aus. Antworte ausschliesslich mit einer Zeile JSON:\n"
        '{"angebot": "<Frage an ihn, höchstens 140 Zeichen, Du-Form>", '
        '"auftrag": "<was du nach einem Ja tust>"}\n'
        "oder mit dem Wort NICHTS."
    )


def angebot_lesen(antwort: str | None) -> dict | None:
    """Das Angebot aus der Antwort des Agenten — oder None (nichts anzubieten,
    Fehler, Unlesbares). Ein Fehlertext des Chatwegs wird nie zum Angebot."""
    text = (antwort or "").strip()
    if not text or text.startswith("[Fehler") or text.upper().startswith("NICHTS"):
        return None
    treffer = re.search(r"\{.*\}", text, re.S)
    if not treffer:
        return None
    try:
        daten = json.loads(treffer.group(0))
    except (TypeError, ValueError):
        return None
    if not isinstance(daten, dict):
        return None
    angebot = " ".join(str(daten.get("angebot") or "").split())[:300]
    auftrag = str(daten.get("auftrag") or "").strip()[:4000]
    if not angebot or not auftrag:
        return None
    return {"angebot": angebot, "auftrag": auftrag}


def auftrag_text(auftrag: str, angebot: str, app: str, titel: str, antwort: str | None) -> str:
    """Der Auftrag an den Agenten nach einem Ja im Overlay."""
    zusatz = f"\nEr hat dazu geschrieben: {antwort}" if antwort else ""
    return (
        f"[Bridge] Dein Mensch hat im Overlay „{angebot}“ mit Ja beantwortet.{zusatz}\n"
        f"Bildschirm: Anwendung {app or 'unbekannt'}, Fenster {titel or 'unbekannt'} (Bild anbei).\n"
        f"Auftrag: {auftrag}\n"
        "Arbeite über die Computer-Use-Werkzeuge auf seinem Rechner. Antworte am Ende mit "
        "einem kurzen Satz, was du getan hast — er steht im Overlay."
    )
