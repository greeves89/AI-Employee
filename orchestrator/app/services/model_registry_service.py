"""Dynamic model catalog: provider auto-discovery + admin freischaltung.

The curated lists in :mod:`app.core.model_catalog` stay as the SEED — the
always-available, provider-correct model strings that are known to run (incl.
Bedrock ARNs / Vertex ``@date`` variants that can't be derived from a public
API). This service layers two things on top, without touching the hard family
guards:

* **Discovery** queries the provider APIs (Anthropic ``/v1/models``, OpenAI
  ``/v1/models``) and records any *additional* models it finds, classified into
  a harness via :func:`model_family`. Bedrock/Vertex/Foundry have no simple
  public listing and stay seed-only.
* **Admin freischaltung** — an admin enables/disables individual models. The
  decision is persisted as a JSON override map in ``platform_settings``. Seed
  models default ENABLED (nothing regresses); a newly discovered non-seed model
  defaults DISABLED until an admin flips it on ("erkannt, aber Admin schaltet
  frei").

``GET /agents/models`` returns only ENABLED models. The family guards in
``model_catalog`` (``is_model_allowed_for_mode`` / ``coerce_model_for_mode``)
are unchanged and still reject cross-harness mistakes hard — this layer only
governs what the UI OFFERS, never what is technically permitted.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import NamedTuple

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.model_catalog import (
    MODEL_CATALOG,
    model_family,
)
from app.core.verbindungswiederholung import (
    PAUSEN_S,
    VERBINDUNGSFEHLER,
    mit_verbindungswiederholung,
)
from app.services.settings_service import SettingsService

logger = logging.getLogger(__name__)

# platform_settings keys (JSON payloads stored as text; not secret).
CACHE_KEY = "model_discovery_cache"
OVERRIDES_KEY = "model_enabled_overrides"

_ANTHROPIC_MODELS_URL = "https://api.anthropic.com/v1/models"
# _OPENAI_MODELS_URL: nicht mehr benutzt, seit _discover_openai() ein
# bewusster Leerlauf ist (siehe dort). Absichtlich stehen gelassen als
# Beleg, wo die Abfrage hin muesste, faende sich ein sicherer Weg,
# gegen das ChatGPT-Konto statt gegen den API-Schluessel zu pruefen.
_OPENAI_MODELS_URL = "https://api.openai.com/v1/models"
# 15 s fuer die Antwort, aber nur 5 s fuer den VERBINDUNGSAUFBAU. Der Aufbau ist
# genau die Klasse, die unten wiederholt wird (siehe ``mit_verbindungswiederholung``);
# mit 15 s waeren drei Versuche je Anbieter 45 s Wartezeit fuer einen Menschen, der
# vor dem Knopf sitzt. So sind es hoechstens 3x5 s + 2,0 s Pausen = 17 s je Anbieter.
_HTTP_TIMEOUT = httpx.Timeout(15.0, connect=5.0)


def _seed_models() -> list[dict]:
    """Flatten the curated MODEL_CATALOG into records with source='seed'."""
    out: list[dict] = []
    for mode, entry in MODEL_CATALOG.items():
        for provider, models in entry["providers"].items():
            for m in models:
                out.append(
                    {
                        "mode": mode,
                        "provider": provider,
                        "value": m["value"],
                        "label": m.get("label", m["value"]),
                        "tier": m.get("tier", ""),
                        "source": "seed",
                    }
                )
    return out


# Jeder Modell-Datensatz MUSS diese Felder tragen: ``_payload_from_records`` und
# ``_is_enabled`` greifen hart darauf zu (``rec["label"]``, ``rec["source"]``). Ein
# unvollstaendiger Datensatz im Zwischenspeicher hat deshalb bisher den ganzen
# Katalog mit ``KeyError`` beendet — und zwar in ``GET /agents/models`` fuer JEDEN
# Nutzer, nicht nur fuer Administratoren. Er wird darum beim Lesen ausgesiebt.
_PFLICHTFELDER = ("mode", "provider", "value", "label", "tier", "source")


def _brauchbar(rec: object) -> bool:
    """Taugt dieser Datensatz aus dem Zwischenspeicher fuer den Katalog?"""
    if not isinstance(rec, dict):
        return False
    if not all(isinstance(rec.get(f), str) for f in _PFLICHTFELDER):
        return False
    return all(rec[f] for f in ("mode", "provider", "value"))


async def _load_cache(svc: SettingsService) -> dict:
    raw = await svc.get(CACHE_KEY)
    if not raw:
        return {"discovered_at": None, "models": []}
    try:
        data = json.loads(raw)
        if isinstance(data, dict) and isinstance(data.get("models"), list):
            return data
    except (ValueError, TypeError):
        logger.warning("model_discovery_cache is corrupt — ignoring")
    return {"discovered_at": None, "models": []}


async def _load_overrides(svc: SettingsService) -> dict[str, bool]:
    raw = await svc.get(OVERRIDES_KEY)
    if not raw:
        return {}
    try:
        data = json.loads(raw)
        if isinstance(data, dict):
            return {str(k): bool(v) for k, v in data.items()}
    except (ValueError, TypeError):
        logger.warning("model_enabled_overrides is corrupt — ignoring")
    return {}


def _merge(seed: list[dict], discovered: list[dict]) -> list[dict]:
    """Seed + discovered, deduped by (mode, provider, value); seed wins."""
    seen: set[tuple[str, str, str]] = set()
    merged: list[dict] = []
    for rec in seed + discovered:
        key = (rec["mode"], rec["provider"], rec["value"])
        if key in seen:
            continue
        seen.add(key)
        merged.append(rec)
    return merged


def _is_enabled(rec: dict, overrides: dict[str, bool]) -> bool:
    """Seed models default enabled; discovered-extra default disabled."""
    if rec["value"] in overrides:
        return overrides[rec["value"]]
    return rec["source"] == "seed"


async def _all_records(db: AsyncSession) -> tuple[list[dict], dict[str, bool], dict]:
    svc = SettingsService(db)
    cache = await _load_cache(svc)
    overrides = await _load_overrides(svc)
    brauchbar = [r for r in cache.get("models", []) if _brauchbar(r)]
    if len(brauchbar) != len(cache.get("models", [])):
        logger.warning(
            "model_discovery_cache: %d unvollstaendige Datensaetze uebersprungen",
            len(cache.get("models", [])) - len(brauchbar),
        )
    merged = _merge(_seed_models(), brauchbar)
    return merged, overrides, cache


async def _configured_claude_providers(db: AsyncSession) -> set[str]:
    """Which claude_code providers actually have credentials behind them here.

    "anthropic" (direct API/OAuth) is always offered -- it's the platform's
    baseline path, shown even on a fresh install with nothing connected yet.
    bedrock/vertex/foundry are optional alternate hosting backends; showing
    their tab when nothing is configured for them just gives an agent a
    Provider button that can never actually run (picking it leaves the agent
    without a working credential, exactly the trap this whole picker exists
    to prevent). foundry can be configured two ways: the platform-wide
    Provider-Einstellungen key/resource (same check as its discovery, see
    ``_discover_foundry``), or a linked AI-Account of that type.
    """
    configured = {"anthropic"}
    if settings.foundry_api_key and settings.foundry_resource:
        configured.add("foundry")
    try:
        from sqlalchemy import select
        from app.models.ai_account import AIAccount
        rows = (await db.execute(
            select(AIAccount.provider_type).where(AIAccount.is_active.is_(True))
        )).scalars().all()
        configured.update(p for p in rows if p in ("bedrock", "vertex", "foundry"))
    except Exception:  # noqa: BLE001 -- never let this block the model list
        pass
    return configured


def _payload_from_records(
    records: list[dict],
    enabled_only: bool,
    overrides: dict[str, bool],
    configured_providers: set[str] | None = None,
) -> dict:
    """Group flat records into the catalog_payload() shape.

    default_model per mode = the seed default if it is enabled, else the first
    enabled model of that mode (keeps the UI's default sane when an admin turns
    the seed default off). Guards still coerce to a safe value at runtime.

    ``configured_providers``, when given, additionally drops claude_code
    provider tabs (bedrock/vertex/foundry) that have no real credential behind
    them on this install -- see ``_configured_claude_providers``. Only applied
    to claude_code: codex_cli has exactly one provider, custom_llm isn't in
    MODEL_CATALOG at all.
    """
    modes_out = []
    for mode, entry in MODEL_CATALOG.items():
        mode_recs = [r for r in records if r["mode"] == mode]
        if enabled_only:
            mode_recs = [r for r in mode_recs if _is_enabled(r, overrides)]
        if configured_providers is not None and mode == "claude_code":
            mode_recs = [r for r in mode_recs if r["provider"] in configured_providers]

        providers: dict[str, list[dict]] = {}
        for r in mode_recs:
            item = {"value": r["value"], "label": r["label"], "tier": r["tier"]}
            if not enabled_only:
                item["enabled"] = _is_enabled(r, overrides)
                item["source"] = r["source"]
            providers.setdefault(r["provider"], []).append(item)

        enabled_values = {
            r["value"] for r in mode_recs if enabled_only or _is_enabled(r, overrides)
        }
        seed_default = entry["default_model"]
        default_model = seed_default if seed_default in enabled_values else next(
            iter(enabled_values), seed_default
        )

        modes_out.append(
            {
                "mode": mode,
                "label": entry["label"],
                "default_provider": entry["default_provider"],
                "default_model": default_model,
                "providers": [
                    {"provider": prov, "models": models}
                    for prov, models in providers.items()
                ],
            }
        )
    return {"modes": modes_out}


async def get_effective_payload(db: AsyncSession) -> dict:
    """catalog_payload() shape, but only ENABLED models (what the UI offers),
    and only claude_code providers actually configured on this install."""
    records, overrides, _ = await _all_records(db)
    configured = await _configured_claude_providers(db)
    return _payload_from_records(records, enabled_only=True, overrides=overrides, configured_providers=configured)


async def get_admin_catalog(db: AsyncSession) -> dict:
    """Full catalog for the admin UI: every known model with enabled + source."""
    records, overrides, stand = await _all_records(db)
    payload = _payload_from_records(records, enabled_only=False, overrides=overrides)
    # ``discovered_at`` = Stempel des letzten VOLLSTAENDIGEN Standes. Die drei
    # Felder danach gehoeren dazu, sonst behauptet die Oberflaeche nach einem
    # Neuladen wieder etwas Falsches: ohne ``last_attempt_at`` sieht ein
    # Administrator nicht, dass gerade gesucht wurde, und ohne
    # ``providers_failed``/``providers_kept`` nicht, WARUM der Stempel steht (#984).
    payload["discovered_at"] = stand.get("discovered_at")
    payload["last_attempt_at"] = stand.get("last_attempt_at")
    payload["providers_failed"] = stand.get("providers_failed") or {}
    payload["providers_kept"] = stand.get("providers_kept") or []
    return payload


async def set_enabled_bulk(db: AsyncSession, overrides_in: dict[str, bool]) -> dict:
    """Merge admin enable/disable decisions into the persisted override map."""
    svc = SettingsService(db)
    current = await _load_overrides(svc)
    for value, enabled in overrides_in.items():
        current[str(value)] = bool(enabled)
    await svc.set(OVERRIDES_KEY, json.dumps(current))
    await db.commit()
    return await get_admin_catalog(db)


async def _anthropic_auth_headers(db: AsyncSession | None = None, user_id: str | None = None) -> dict | None:
    """Auth headers for the Anthropic models endpoint.

    Prefers an explicit API key (x-api-key). Falls back to the bot's OAuth token
    (Claude Pro/Team login) as a Bearer with the oauth beta header. Both of those
    are a PLATFORM-WIDE slot that, in this app's actual usage pattern, is almost
    always empty — Claude access here is normally the admin's OWN personal
    credential (``UserAiCredential``, harness ``claude_code``), the same one
    every agent run already falls back to (see ``agent_credentials.py``). So as
    a last resort, try the calling admin's personal credential too — without it,
    "Discover" silently found nothing on any install where nobody ever filled in
    the platform-wide key, which is the common case, not the exception.
    Best-effort throughout: if none of these are accepted for ``/v1/models`` the
    caller logs and skips, and the seed list + manual freischaltung still work.
    """
    if settings.anthropic_api_key:
        return {
            "x-api-key": settings.anthropic_api_key,
            "anthropic-version": "2023-06-01",
        }
    oauth = None
    try:
        from app.services.claude_token_service import ClaudeTokenService
        oauth = await ClaudeTokenService()._get_db_token()
    except Exception:  # noqa: BLE001
        oauth = None
    oauth = oauth or (settings.claude_code_oauth_token or None)
    if not oauth and db is not None and user_id:
        try:
            from app.core.agent_credentials import personal_credential
            oauth = await personal_credential(db, user_id, "claude_code")
        except Exception:  # noqa: BLE001
            oauth = None
    if oauth:
        # The personal credential can be either an API key (``sk-ant-…``, saved
        # verbatim by the user) or an OAuth token from ``claude setup-token`` /
        # the browser login — same field, no separate flag. Route by shape.
        if oauth.startswith("sk-ant-"):
            return {"x-api-key": oauth, "anthropic-version": "2023-06-01"}
        return {
            "authorization": f"Bearer {oauth}",
            "anthropic-version": "2023-06-01",
            "anthropic-beta": "oauth-2025-04-20",
        }
    return None


class Abruf(NamedTuple):
    """Ergebnis EINES Anbieter-Abrufs — die Liste allein reicht nicht.

    Eine leere Liste hat zwei voellig verschiedene Bedeutungen: "der Anbieter hat
    nichts (Neues)" und "wir haben ihn nicht erreicht". Nur die zweite darf den
    bisher entdeckten Stand NICHT ueberschreiben (#984). Deshalb ein benanntes
    Feld statt eines zweiten Rueckgabewerts im Tupel: ein nackter boolescher
    Zusatzwert wurde in #977 vom Aufrufer still ignoriert, und der ganze Fix war
    damit wirkungslos — ``ergebnis.gescheitert`` laesst sich nicht versehentlich
    uebersehen, und ``mypy``/Tests sprechen darueber.
    """

    modelle: list[dict]
    gescheitert: bool
    # Kurzer Grund fuer die Oberflaeche ("HTTP 401", "Verbindung nicht zustande
    # gekommen"). Ohne ihn kann ein Administrator einen dauerhaften Fehlzustand
    # (falscher Schluessel) nicht von einem Aussetzer unterscheiden und weiss
    # nicht, dass er handeln muss.
    grund: str = ""


def _kurzgrund(e: BaseException) -> str:
    """Einzeiliger Grund fuer die Oberflaeche — keine Stapelspur, kein Schluessel."""
    if isinstance(e, httpx.HTTPStatusError):
        return f"HTTP {e.response.status_code}"
    if isinstance(e, VERBINDUNGSFEHLER):
        return "Verbindung nicht zustande gekommen"
    if isinstance(e, httpx.TimeoutException):
        return "Zeitueberschreitung"
    return type(e).__name__


async def _discover_anthropic(db: AsyncSession | None = None, user_id: str | None = None) -> Abruf:
    headers = await _anthropic_auth_headers(db, user_id)
    if not headers:
        # Nicht konfiguriert ist nicht gescheitert: hier wurde niemand gefragt.
        return Abruf([], gescheitert=False)
    out: list[dict] = []
    try:
        async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as client:
            resp = await mit_verbindungswiederholung(
                lambda: client.get(_ANTHROPIC_MODELS_URL, headers=headers),
                zweck="Anthropic-Modellabruf",
                pausen=PAUSEN_S,
            )
            resp.raise_for_status()
            for m in resp.json().get("data", []):
                mid = m.get("id", "")
                if model_family(mid) != "claude_code":
                    continue
                out.append(
                    {
                        "mode": "claude_code",
                        "provider": "anthropic",
                        "value": mid,
                        "label": m.get("display_name") or mid,
                        "tier": "Discovered",
                        "source": "discovered",
                    }
                )
    except Exception as e:  # noqa: BLE001 — discovery is best-effort
        logger.warning("Anthropic model discovery failed: %s", e)
        return Abruf(out, gescheitert=True, grund=_kurzgrund(e))
    return Abruf(out, gescheitert=False)


async def _discover_openai() -> Abruf:
    """Bislang ausschliesslich codex_cli-Entdeckung ueber den API-Schluessel —
    und genau das ist das Problem, nicht nur Details davon.

    codex_cli laeuft ueber ein ChatGPT-Konto, nicht ueber den API-Schluessel:
    zwei getrennte Zugangswege mit je eigener Modell-Freigabe, die sich nur
    zufaellig in der Schreibweise ueberschneiden. Am 08.09.2026 listete die
    API-Schluessel-Uebersicht (``/v1/models``) "gpt-5.5" und "gpt-5.4" als
    vorhanden, waehrend das ChatGPT-Konto beide mit 400 „not supported when
    using Codex with a ChatGPT account" ablehnte — codex_cli meldete das als
    Verbindungsabbruch mit Wiederholungsversuchen, was wie ein Anmeldeproblem
    aussah, tatsaechlich aber ein aus dieser Entdeckung stammendes, laengst
    totes Modell war. Eine Musterregel ("gpt-5.<Ziffern>") kann diesen
    Unterschied nicht erkennen, nur ein echter Aufruf gegen das ChatGPT-Konto
    koennte es — den hat diese Funktion nicht und soll sie auch nicht
    nachbilden (das waere ein echter Codex-Lauf je Entdeckungsdurchlauf).
    Der Katalog fuer codex_cli bleibt deshalb kuratiert in
    ``app.core.model_catalog``, nicht entdeckt — diese Funktion ist bis auf
    Weiteres ein bewusster Leerlauf.
    """
    # Leerlauf, nicht Ausfall: es wurde nichts versucht, also ist nichts gescheitert.
    return Abruf([], gescheitert=False)


async def _discover_foundry() -> Abruf:
    """Claude-Deployments einer Azure-AI-Foundry-Ressource (Kundenbefund
    2026-08-18: fuer "foundry" gab es keinerlei Suchpfad — deployte Modelle
    tauchten nie im Katalog auf). Erreichbar nur, wenn in den Provider-
    Einstellungen Ressource + Key hinterlegt sind; best-effort wie die anderen.
    """
    key = settings.foundry_api_key
    resource = (settings.foundry_resource or "").strip()
    if not (key and resource):
        return Abruf([], gescheitert=False)
    base = resource if "://" in resource else f"https://{resource}.services.ai.azure.com"
    url = base.rstrip("/") + "/anthropic/v1/models"
    headers = {"x-api-key": key, "api-key": key, "anthropic-version": "2023-06-01"}
    out: list[dict] = []
    try:
        async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as client:
            resp = await mit_verbindungswiederholung(
                lambda: client.get(url, headers=headers),
                zweck="Foundry-Modellabruf",
                pausen=PAUSEN_S,
            )
            resp.raise_for_status()
            for m in resp.json().get("data", []):
                mid = m.get("id", "")
                if model_family(mid) != "claude_code":
                    continue
                out.append(
                    {
                        "mode": "claude_code",
                        "provider": "foundry",
                        "value": mid,
                        "label": m.get("display_name") or mid,
                        "tier": "Discovered",
                        "source": "discovered",
                    }
                )
    except Exception as e:  # noqa: BLE001 — discovery is best-effort
        logger.warning("Foundry model discovery failed: %s", e)
        return Abruf(out, gescheitert=True, grund=_kurzgrund(e))
    return Abruf(out, gescheitert=False)


async def discover(db: AsyncSession, user_id: str | None = None) -> dict:
    """Query provider APIs, store the non-seed extras, return an admin catalog.

    ``user_id`` is the calling admin — used only as a last-resort credential
    fallback (see ``_anthropic_auth_headers``) when no platform-wide key/OAuth
    token is configured.

    Only models NOT already in the seed are cached (the seed is authoritative
    for its own strings). Newly discovered models stay DISABLED until an admin
    enables them.

    **Ein Anbieter raeumt seinen Stand nur auf, wenn er gefragt wurde UND
    geantwortet hat** (#984). Vorher loeschte ein Knopfdruck waehrend eines
    Netzaussetzers den gesamten entdeckten Stand und setzte zugleich einen
    frischen Stempel — es gibt nur diesen einen Schluessel, aus dem sich das
    haette heilen koennen.

    Der Preis ist sichtbar und bewusst in Kauf genommen: ein Modell, das der
    Anbieter wirklich zurueckgezogen hat, steht nach einem gescheiterten Abruf
    laenger in der Liste. Damit das kein stiller Dauerzustand wird (falscher
    Schluessel, geloeschte Ressource), nennt die Antwort — und nach einem
    Neuladen auch ``get_admin_catalog`` — Ross und Reiter:
    ``providers_failed`` (Anbieter -> Grund, z. B. "HTTP 401"),
    ``providers_kept`` (wessen alter Stand drinsteht) und ``last_attempt_at``.
    """
    anthropic_queried = bool(await _anthropic_auth_headers(db, user_id))
    # _discover_openai() ist bewusst ein Leerlauf (siehe dort) — als "abgefragt"
    # zu melden waere falsch: ein API-Schluessel ist gesetzt, aber niemand hat
    # ihn dafuer benutzt.
    openai_queried = False
    foundry_queried = bool(settings.foundry_api_key and settings.foundry_resource)
    anthropic = await _discover_anthropic(db, user_id)
    openai = await _discover_openai()
    foundry = await _discover_foundry()
    abrufe = {"anthropic": anthropic, "openai": openai, "foundry": foundry}
    befragt = {
        "anthropic": anthropic_queried,
        "openai": openai_queried,
        "foundry": foundry_queried,
    }
    gescheitert = {
        name: (a.grund or "nicht erreichbar")
        for name, a in sorted(abrufe.items())
        if a.gescheitert
    }
    # DIE REGEL: ein Anbieter darf seinen bisherigen Stand nur aufraeumen, wenn er
    # GEFRAGT WURDE und GEANTWORTET hat. Beide Haelften sind noetig:
    #   * gescheitert -> wir wissen nichts Neues, der alte Stand bleibt (#984);
    #   * nicht gefragt -> ebenfalls nichts Neues. Das ist kein Randfall: die
    #     Anthropic-Zugangsdaten koennen aus der PERSOENLICHEN Ablage des
    #     aufrufenden Administrators kommen (siehe ``_anthropic_auth_headers``).
    #     Ohne diese Haelfte loescht ein zweiter Administrator ohne eigene
    #     Zugangsdaten mit einem Knopfdruck den Stand, den der erste gefunden hat.
    darf_aufraeumen = {
        name for name, a in abrufe.items() if befragt[name] and not a.gescheitert
    }
    # openai wird BEWUSST nicht entdeckt (siehe ``_discover_openai``), ist also nie
    # "befragt". Alte entdeckte openai-Eintraege sind damit Altlast und sollen
    # verschwinden — nicht von der Regel oben geschuetzt werden.
    darf_aufraeumen.add("openai")
    found = anthropic.modelle + openai.modelle + foundry.modelle

    svc = SettingsService(db)
    alter_stand = await _load_cache(svc)
    # Alter Stand der Anbieter, die nicht aufraeumen durften. Unvollstaendige
    # Datensaetze werden dabei NICHT mitgeschleppt (``_brauchbar``): sie beenden
    # den Katalogaufbau mit ``KeyError`` und wuerden hier sonst festgeschrieben,
    # solange der Anbieter unten ist.
    uebernommen = [
        rec
        for rec in alter_stand.get("models", [])
        if _brauchbar(rec) and rec["provider"] not in darf_aufraeumen
    ]

    seed_keys = {(r["mode"], r["provider"], r["value"]) for r in _seed_models()}
    extras: list[dict] = []
    seen: set[tuple[str, str, str]] = set()
    # Frische Treffer zuerst: bei gleichem Schluessel gewinnt das, was der
    # Anbieter gerade gesagt hat, gegen den uebernommenen Eintrag.
    aus_cache_drin = 0
    uebernommen_von: set[str] = set()
    for rec, aus_cache in [*((r, False) for r in found), *((r, True) for r in uebernommen)]:
        key = (rec.get("mode"), rec.get("provider"), rec.get("value"))
        if None in key or key in seed_keys or key in seen:
            continue
        seen.add(key)
        extras.append(rec)
        if aus_cache:
            # Erst HIER zaehlen, nicht vorher: ein uebernommener Eintrag, den ein
            # frischer Treffer verdraengt hat, wurde nicht uebernommen — die
            # Oberflaeche wuerde sonst eine Uebernahme behaupten, die es nicht gab.
            aus_cache_drin += 1
            uebernommen_von.add(rec["provider"])

    # ZWEI Stempel, weil zwei verschiedene Fragen dahinterstehen — ein einziger
    # kann sie nicht beide wahrheitsgemaess beantworten (#984):
    #   * ``discovered_at``   — seit wann ist dieser Stand VOLLSTAENDIG frisch.
    #     Steht im Stand auch nur ein uebernommener Eintrag, ruckt er nicht weiter;
    #     sonst meldet die Oberflaeche "gerade gesucht" ueber altem Wissen.
    #   * ``last_attempt_at`` — wann zuletzt gesucht WURDE. Ohne ihn sieht ein
    #     Administrator nach einem Ausfall nicht, dass sein Knopfdruck etwas getan
    #     hat, und beim allerersten Lauf stuende dort ewig "noch nie gesucht".
    # Timestamp comes from real wall-clock here (service call, not a workflow),
    # so datetime.now is fine.
    jetzt = datetime.now(timezone.utc).isoformat()
    vollstaendig = not gescheitert and aus_cache_drin == 0
    cache = {
        "discovered_at": jetzt if vollstaendig else alter_stand.get("discovered_at"),
        "last_attempt_at": jetzt,
        "models": extras,
        "providers_queried": {
            "anthropic": anthropic_queried,
            "openai": openai_queried,
            "foundry": foundry_queried,
        },
        "providers_failed": gescheitert,
        "providers_kept": sorted(uebernommen_von),
    }
    await svc.set(CACHE_KEY, json.dumps(cache))
    await db.commit()
    logger.info(
        "Model discovery: %d anthropic + %d openai + %d foundry found, %d extras cached%s",
        len(anthropic.modelle), len(openai.modelle), len(foundry.modelle), len(extras),
        "" if vollstaendig else (
            f" — Stand unvollstaendig: {aus_cache_drin} Modelle von "
            f"{', '.join(sorted(uebernommen_von)) or '-'} aus dem alten Stand uebernommen"
            f"{', nicht erreicht: ' + ', '.join(f'{k} ({v})' for k, v in gescheitert.items()) if gescheitert else ''}"
            f", Stempel bleibt {alter_stand.get('discovered_at')}"
        ),
    )
    result = await get_admin_catalog(db)
    result["last_discovery"] = {
        "anthropic_found": len(anthropic.modelle),
        "openai_found": len(openai.modelle),
        "foundry_found": len(foundry.modelle),
        "new_extras": len(extras),
        "anthropic_queried": anthropic_queried,
        "openai_queried": openai_queried,
        "foundry_queried": foundry_queried,
        # Ohne diese Felder meldet der Knopf "0 erkannt", wenn niemand gefragt
        # werden konnte — die Falschaussage aus #984.
        "providers_failed": gescheitert,
        "providers_kept": sorted(uebernommen_von),
        "kept_from_cache": aus_cache_drin,
        "last_attempt_at": jetzt,
    }
    return result
