"""Kanal-Einstellungen eines Agenten — eine Beschreibung, eine Prüfung, ein Speicherort.

Teams, Slack, Discord und WhatsApp lesen ihre Einstellungen aus ``agent.config["channels"]``
(services/*_gateway.py). Bis 1.447 gab es keinen Weg, sie zu setzen, außer direkt in der
Datenbank — das Handbuch beschrieb eine Oberfläche, die es nicht gab.

Hier steht, was ein Mensch je Kanal einstellen darf, in neutralen Namen (``ziele``,
``nur_bei_erwaehnung``, ``stichworte``, ``token`` …), und wie das auf die Schlüssel abgebildet
wird, die die Gateways lesen. Zugangsdaten (Bot-Token, Zugriffstoken) werden verschlüsselt
gespeichert und nie zurückgegeben — nur ``token_gesetzt``.

Teams: Die Chats aus Besprechungen (services/teams_meetings) bleiben unangetastet; was hier
eingestellt wird, sind die von Hand gewählten (``chat_ids_manual``).
"""

from __future__ import annotations

import re

from app.core.encryption import encrypt_token

KANAELE = ("teams", "slack", "discord", "whatsapp")
HOECHSTENS_ZIELE = 50
HOECHSTENS_STICHWORTE = 20

_KENNUNG = re.compile(r"^[A-Za-z0-9:@._\-]{1,200}$")
#: Wie whatsapp_gateway._MIN_ENTRY_DIGITS — kürzere Einträge verwirft der Kanal ohnehin.
_NUMMER = re.compile(r"^\+?[0-9]{10,20}$")
_ZIFFERN = re.compile(r"^[0-9]{5,30}$")


def gueltige_kennung(wert: str) -> bool:
    """Kennungen landen in URL-Pfaden (Graph, Discord) — nur Punkte („..“) wäre ein Pfadsprung."""
    return bool(_KENNUNG.match(wert)) and wert.strip(".") != ""


def ohne_zugangsdaten(config: dict | None) -> dict | None:
    """``config`` für die allgemeine Agenten-Antwort (``GET /agents``): ohne ``channels``.

    Die sehen auch Menschen, denen der Agent nur freigegeben ist. Kanal-Einstellungen enthalten
    verschlüsselte Zugangsdaten, Telefonnummern und Chat-Kennungen — die gibt es nur über
    ``GET /agents/{id}/kanaele``, und dort für Freigegebene nur als Zusammenfassung."""
    if not isinstance(config, dict) or "channels" not in config:
        return config
    return {k: v for k, v in config.items() if k != "channels"}


def zusammenfassung(stand: dict) -> dict:
    """Für Menschen, denen der Agent nur freigegeben ist: an/aus und Anzahl — keine
    Telefonnummern, Chat- oder Kanalkennungen."""
    return {"kanal": stand["kanal"], "aktiv": stand["aktiv"],
            "anzahl_ziele": len(stand.get("ziele") or []) + len(stand.get("teams_kanaele") or []),
            "anzahl_absender": len(stand.get("absender") or []), "nur_zusammenfassung": True}


class UngueltigeEinstellung(ValueError):
    pass


def _roh(config: dict | None, kanal: str) -> dict:
    return ((config or {}).get("channels") or {}).get(kanal) or {}


def _kennungen(werte, was: str, hoechstens: int = HOECHSTENS_ZIELE) -> list[str]:
    if werte is None:
        return []
    if not isinstance(werte, list) or len(werte) > hoechstens:
        raise UngueltigeEinstellung(f"{was}: höchstens {hoechstens} Einträge.")
    aus = []
    for w in werte:
        w = str(w or "").strip()
        if not w:
            continue
        if not gueltige_kennung(w):
            raise UngueltigeEinstellung(f"{was}: „{w[:40]}“ ist keine gültige Kennung.")
        if w not in aus:
            aus.append(w)
    return aus


def _stichworte(werte) -> list[str]:
    if werte is None:
        return []
    if not isinstance(werte, list) or len(werte) > HOECHSTENS_STICHWORTE:
        raise UngueltigeEinstellung(f"Stichworte: höchstens {HOECHSTENS_STICHWORTE}.")
    aus = []
    for w in werte:
        w = " ".join(str(w or "").split())
        if not w:
            continue
        if len(w) > 60:
            raise UngueltigeEinstellung("Ein Stichwort ist zu lang (höchstens 60 Zeichen).")
        if w.casefold() not in {a.casefold() for a in aus}:
            aus.append(w)
    return aus


#: Teams-Termine (services/teams_meetings, ``channels.meetings``) — Teil des Teams-Kanals.
BESPRECHUNGEN = "meetings"
ROLLEN = ("mitschreiber", "beisitzer", "teilnehmer")


def besprechungen_lesen(config: dict | None) -> dict:
    b = ((config or {}).get("channels") or {}).get(BESPRECHUNGEN) or {}
    return {"aktiv": bool(b.get("enabled")), "rolle": b.get("role") if b.get("role") in ROLLEN else "mitschreiber",
            "automatisch_annehmen": bool(b.get("auto_accept")), "annehmen_von": list(b.get("accept_from") or []),
            "mit_gaesten": bool(b.get("mit_gaesten"))}


def _besprechungen(alt: dict, e) -> dict:
    """Termin-Einstellungen prüfen. Felder, die fehlen, bleiben."""
    if not isinstance(e, dict):
        raise UngueltigeEinstellung("Besprechungen: Einstellungen fehlen.")
    b = dict(alt)
    if "aktiv" in e:
        b["enabled"] = bool(e["aktiv"])
    if "rolle" in e:
        if e["rolle"] not in ROLLEN:
            raise UngueltigeEinstellung("Besprechungen: Rolle ist „mitschreiber“, „beisitzer“ oder „teilnehmer“.")
        b["role"] = e["rolle"]
    if "automatisch_annehmen" in e:
        b["auto_accept"] = bool(e["automatisch_annehmen"])
    if "annehmen_von" in e:
        liste = e["annehmen_von"] or []
        if not isinstance(liste, list) or len(liste) > HOECHSTENS_ZIELE:
            raise UngueltigeEinstellung(f"Annehmen von: höchstens {HOECHSTENS_ZIELE} Einträge.")
        sauber = []
        for a in liste:
            a = str(a or "").strip().lower()
            if not a:
                continue
            if not re.fullmatch(r"[a-z0-9._%+\-]*@?[a-z0-9.\-]+\.[a-z]{2,}", a):
                raise UngueltigeEinstellung(f"„{a[:40]}“ ist keine Adresse oder Domäne.")
            # Domäne immer mit „@“ speichern: eindeutig eine Domäne, keine Adresse.
            sauber.append(a if "@" in a else "@" + a)
        b["accept_from"] = sauber
    if "mit_gaesten" in e:
        b["mit_gaesten"] = bool(e["mit_gaesten"])
    if b.get("enabled") and b.get("role") not in ROLLEN:
        b["role"] = "mitschreiber"
    return b


def lesen(config: dict | None, kanal: str) -> dict:
    """Was die Oberfläche zeigt — nie ein Token."""
    r = _roh(config, kanal)
    stand: dict = {"kanal": kanal, "aktiv": bool(r.get("enabled"))}
    if kanal == "teams":
        manuell = r.get("chat_ids_manual")
        if manuell is None:
            manuell = [c for c in (r.get("chat_ids") or []) if c not in (r.get("chat_ids_meetings") or [])]
        stand.update(ziele=list(manuell), teams_kanaele=list(r.get("channels") or []),
                     besprechungs_chats=len(r.get("chat_ids_meetings") or []),
                     nur_bei_erwaehnung=bool(r.get("mention_only", True)),
                     stichworte=list(r.get("mention_names") or []),
                     besprechungen=besprechungen_lesen(config))
    elif kanal == "slack":
        stand.update(ziele=list(r.get("channels") or []), nur_bei_erwaehnung=bool(r.get("mention_only", True)),
                     stichworte=list(r.get("mention_names") or []), bot_kennung=r.get("bot_user_id") or "",
                     token_gesetzt=bool(r.get("bot_token_enc")))
    elif kanal == "discord":
        stand.update(ziele=list(r.get("channels") or []), nur_bei_erwaehnung=bool(r.get("mention_required", True)),
                     stichworte=list(r.get("keywords") or []), bot_kennung=r.get("bot_user_id") or "",
                     token_gesetzt=bool(r.get("bot_token_enc")))
    elif kanal == "whatsapp":
        stand.update(telefon_id=r.get("phone_number_id") or "", absender=list(r.get("allowed_senders") or []),
                     token_gesetzt=bool(r.get("access_token_enc")))
    return stand


def setzen(config: dict | None, kanal: str, e: dict) -> tuple[dict, list[str]]:
    """Neue ``config`` (neues Dict) und eine Liste geänderter Felder fürs Protokoll (ohne Werte
    von Zugangsdaten). Wirft :class:`UngueltigeEinstellung`. Felder, die fehlen, bleiben."""
    if kanal not in KANAELE:
        raise UngueltigeEinstellung("Unbekannter Kanal.")
    neu = dict(config or {})
    kanaele = dict(neu.get("channels") or {})
    r = dict(kanaele.get(kanal) or {})
    geaendert: list[str] = []

    def setze(schluessel: str, wert, feld: str) -> None:
        if r.get(schluessel) != wert:
            r[schluessel] = wert
            geaendert.append(feld)

    if "aktiv" in e:
        setze("enabled", bool(e["aktiv"]), "aktiv")
    if kanal == "teams":
        if "ziele" in e:
            manuell = _kennungen(e["ziele"], "Chats")
            meetings = list(r.get("chat_ids_meetings") or [])
            setze("chat_ids_manual", manuell, "chats")
            r["chat_ids"] = sorted(set(manuell) | set(meetings))
        if "teams_kanaele" in e:
            liste = e["teams_kanaele"] or []
            if not isinstance(liste, list) or len(liste) > 20:
                raise UngueltigeEinstellung("Teams-Kanäle: höchstens 20.")
            sauber = []
            for k in liste:
                if not isinstance(k, dict):
                    raise UngueltigeEinstellung("Teams-Kanäle: Team und Kanal angeben.")
                t, c = _kennungen([k.get("team_id")], "Team"), _kennungen([k.get("channel_id")], "Kanal")
                if not (t and c):
                    raise UngueltigeEinstellung("Teams-Kanäle: Team und Kanal angeben.")
                if {"team_id": t[0], "channel_id": c[0]} not in sauber:
                    sauber.append({"team_id": t[0], "channel_id": c[0]})
            setze("channels", sauber, "teams_kanaele")
        if "nur_bei_erwaehnung" in e:
            setze("mention_only", bool(e["nur_bei_erwaehnung"]), "nur_bei_erwaehnung")
        if "stichworte" in e:
            setze("mention_names", _stichworte(e["stichworte"]), "stichworte")
        if "besprechungen" in e:
            alt = dict(kanaele.get(BESPRECHUNGEN) or {})
            b = _besprechungen(alt, e["besprechungen"])
            if b != alt:
                kanaele[BESPRECHUNGEN] = b
                geaendert.append("besprechungen")
    elif kanal in ("slack", "discord"):
        if "ziele" in e:
            setze("channels", _kennungen(e["ziele"], "Kanäle"), "kanaele")
        if "nur_bei_erwaehnung" in e:
            setze("mention_only" if kanal == "slack" else "mention_required", bool(e["nur_bei_erwaehnung"]),
                  "nur_bei_erwaehnung")
        if "stichworte" in e:
            setze("mention_names" if kanal == "slack" else "keywords", _stichworte(e["stichworte"]), "stichworte")
        if "bot_kennung" in e:
            k = _kennungen([e["bot_kennung"]], "Bot-Kennung") if e["bot_kennung"] else []
            setze("bot_user_id", k[0] if k else "", "bot_kennung")
        _token(r, e, "bot_token_enc", geaendert)
    elif kanal == "whatsapp":
        if "telefon_id" in e:
            t = str(e["telefon_id"] or "").strip()
            if t and not _ZIFFERN.match(t):
                raise UngueltigeEinstellung("Telefonnummer-ID ungültig (nur Ziffern).")
            setze("phone_number_id", t, "telefon_id")
        if "absender" in e:
            liste = e["absender"] or []
            if not isinstance(liste, list) or len(liste) > HOECHSTENS_ZIELE:
                raise UngueltigeEinstellung(f"Absender: höchstens {HOECHSTENS_ZIELE}.")
            nummern = []
            for n in liste:
                n = str(n or "").replace(" ", "").strip()
                if not n:
                    continue
                if not _NUMMER.match(n):
                    raise UngueltigeEinstellung(f"„{n[:25]}“ ist keine Telefonnummer.")
                nummern.append(n)
            setze("allowed_senders", nummern, "absender")
        _token(r, e, "access_token_enc", geaendert)
    kanaele[kanal] = r
    neu["channels"] = kanaele
    return neu, geaendert


def _token(r: dict, e: dict, schluessel: str, geaendert: list[str]) -> None:
    """``token`` fehlt: bleibt. Leer: wird entfernt. Sonst verschlüsselt gespeichert."""
    if "token" not in e or e["token"] is None:
        return
    t = str(e["token"]).strip()
    if len(t) > 1000:
        raise UngueltigeEinstellung("Das Token ist zu lang.")
    if not t:
        if r.pop(schluessel, None) is not None:
            geaendert.append("token entfernt")
        return
    r[schluessel] = encrypt_token(t)
    geaendert.append("token gesetzt")
