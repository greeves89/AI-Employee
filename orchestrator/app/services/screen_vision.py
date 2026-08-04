"""Screenshots für den Sprach-Agenten lesbar machen.

Das Realtime-Sprachmodell (Nova Sonic) ist Sprache-zu-Sprache: Ton rein, Ton raus.
Es hat **keinen Bildkanal**. Ein Screenshot der Desktop-Bridge geht an den Browser
des Nutzers, aber niemals in den Kontext des Modells — auf „was siehst du?" konnte
der Agent deshalb nur passen (Kundenmeldung 2026-08-04).

Hier läuft das Bild durch ein bildfähiges Modell, und die Stimme bekommt dessen
Beschreibung als Text. Damit sieht der Sprach-Agent effektiv den Bildschirm, ohne
dass das Sprachmodell selbst Bilder können müsste.
"""

from __future__ import annotations

import logging

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

DEFAULT_VISION_MODEL = "claude-sonnet-5"
#: Ein Screenshot soll in Sekunden beschrieben sein — im Gespräch wartet jemand.
_TIMEOUT_S = 45.0
_MAX_TOKENS = 700


class ScreenVisionError(RuntimeError):
    """Beschreibung nicht möglich — der Aufrufer sagt das dem Nutzer, statt zu raten."""


def _prompt(question: str, platform: str = "") -> str:
    os_hint = f"Der Bildschirm gehört zu einem {platform}-Rechner. " if platform else ""
    ask = question.strip() or "Was ist auf dem Bildschirm zu sehen?"
    return (
        f"{os_hint}Du siehst einen Screenshot vom Bildschirm eines Nutzers. "
        f"Beantworte knapp und konkret: {ask}\n\n"
        "Wichtig:\n"
        "- Nenne die sichtbaren Fenster/Apps und worum es darin geht.\n"
        "- Nenne Beschriftungen wörtlich, wenn sie für die Frage zählen.\n"
        "- Deine Antwort wird VORGELESEN: höchstens 3 kurze Sätze, keine Aufzählung, "
        "keine Koordinaten.\n"
        "- Siehst du im Wesentlichen nur den Schreibtischhintergrund ohne Fensterinhalte, "
        "sage das ausdrücklich — unter macOS fehlt dann meist die Freigabe zur "
        "Bildschirmaufnahme.\n"
        "- Erfinde nichts. Was du nicht erkennst, lässt du weg."
    )


async def describe_screenshot(
    screenshot_b64: str,
    question: str = "",
    platform: str = "",
    model: str | None = None,
) -> str:
    """Beschreibt einen Screenshot in einem Satz oder dreien. Wirft bei Misserfolg."""
    if not screenshot_b64:
        raise ScreenVisionError("Kein Bild erhalten.")
    api_key = settings.anthropic_api_key
    if not api_key:
        raise ScreenVisionError(
            "Für das Erkennen von Bildschirminhalten fehlt der Anthropic-Zugang."
        )

    blocks = [
        {"type": "image",
         "source": {"type": "base64", "media_type": "image/png", "data": screenshot_b64}},
        {"type": "text", "text": _prompt(question, platform)},
    ]
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT_S) as client:
            resp = await client.post(
                "https://api.anthropic.com/v1/messages",
                headers={
                    "x-api-key": api_key,
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json",
                },
                json={
                    "model": model or DEFAULT_VISION_MODEL,
                    "max_tokens": _MAX_TOKENS,
                    "messages": [{"role": "user", "content": blocks}],
                },
            )
    except httpx.RequestError as e:
        raise ScreenVisionError(f"Bilderkennung nicht erreichbar: {type(e).__name__}") from e

    if resp.status_code != 200:
        # Kein Klartext des Fehlers an den Nutzer — der kann den API-Key enthalten.
        logger.warning("screen vision failed: %s %s", resp.status_code, resp.text[:300])
        raise ScreenVisionError("Die Bilderkennung hat den Screenshot abgelehnt.")

    text = "".join(
        b.get("text", "") for b in (resp.json().get("content") or [])
        if b.get("type") == "text"
    ).strip()
    if not text:
        raise ScreenVisionError("Die Bilderkennung kam ohne Antwort zurück.")
    return text
