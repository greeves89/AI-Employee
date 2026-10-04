"""Passwortregeln an EINER Stelle (#914).

Vorher stand ``len(password) < 8`` zweimal im Code (Registrierung, Anlage durch
den Administrator) — und das Zurücksetzen prüfte gar nichts. Drei Wege, zwei
Regeln, eine davon zu schwach: im Markttest als Befund vermerkt.

Die Regel hier gilt für alle drei Wege:
- mindestens ``MINDESTLAENGE`` Zeichen,
- nicht die E-Mail-Adresse (auch nicht deren Teil vor dem @),
- keins der häufigsten Passwörter (auch nicht mit angehängten Ziffern).

Bewusst KEINE Zeichenklassen-Pflicht (Groß/Klein/Sonderzeichen): sie führt zu
„Passwort1!“ statt zu langen Passphrasen. Länge schlägt Komplexität.
"""

from __future__ import annotations

import re

MINDESTLAENGE = 12

#: Häufige Passwörter und Muster, kleingeschrieben. Kürzere als die Mindestlänge
#: fängt schon die Länge ab; hier stehen die, die lang genug, aber trotzdem
#: wertlos sind — und die Stämme, an die Leute Ziffern hängen.
_HAEUFIGE_STAEMME = frozenset({
    "password", "passwort", "kennwort", "qwertz", "qwerty", "asdfgh", "yxcvbn",
    "zxcvbn", "letmein", "welcome", "willkommen", "admin", "administrator",
    "iloveyou", "sommer", "winter", "fruehling", "herbst", "hallo", "geheim",
    "changeme", "secret", "abc", "test", "master", "dragon", "monkey", "football",
    "fussball", "schalke", "bayern", "baseball", "sunshine", "princess", "starwars",
    "login", "user", "benutzer", "default", "start", "ichliebedich",
})
_HAEUFIGE = frozenset({
    "123456789012", "1234567890123", "12345678901234", "qwertzuiopü", "qwertyuiop12",
    "000000000000", "111111111111", "123123123123", "abcdefghijkl", "1q2w3e4r5t6z",
    "1q2w3e4r5t6y", "qwertz123456", "qwerty123456", "passwort1234", "password1234",
    "passwort123!", "password123!", "willkommen123", "willkommen1!", "hallo1234567",
    "administrator1", "letmein12345", "iloveyou1234",
})


def _ist_haeufig(passwort: str) -> bool:
    p = passwort.strip().lower()
    if p in _HAEUFIGE:
        return True
    # Stamm + Ziffern/Satzzeichen („Passwort2026!!“, „Sommer2025!“ …)
    stamm = re.sub(r"[\d\W_]+$", "", p)
    if stamm in _HAEUFIGE_STAEMME:
        return True
    # Ein einziges Zeichen wiederholt („aaaaaaaaaaaa“) oder eine reine Ziffernfolge
    if len(set(p)) <= 2:
        return True
    if p.isdigit() and (p in "01234567890123456789" or p in "98765432109876543210"):
        return True
    return False


def passwort_fehler(passwort: str | None, email: str | None = None) -> str | None:
    """Was am Passwort nicht stimmt — oder ``None``, wenn es die Regeln erfüllt.

    Der Text geht so an den Menschen, der das Passwort gewählt hat.
    """
    passwort = passwort or ""
    if len(passwort) < MINDESTLAENGE:
        return f"Das Passwort muss mindestens {MINDESTLAENGE} Zeichen lang sein."
    if email:
        mail = email.strip().lower()
        lokal = mail.split("@", 1)[0]
        p = passwort.strip().lower()
        if p == mail or (lokal and p == lokal):
            return "Das Passwort darf nicht der E-Mail-Adresse entsprechen."
    if _ist_haeufig(passwort):
        return "Dieses Passwort ist zu häufig und leicht zu erraten. Bitte ein anderes wählen."
    return None


def passwort_pruefen(passwort: str | None, email: str | None = None) -> None:
    """Wie ``passwort_fehler``, wirft aber 400 — für die Endpunkte."""
    fehler = passwort_fehler(passwort, email)
    if fehler:
        from fastapi import HTTPException

        raise HTTPException(status_code=400, detail=fehler)
