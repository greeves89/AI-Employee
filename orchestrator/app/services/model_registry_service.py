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

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.model_catalog import (
    MODEL_CATALOG,
    is_model_allowed_for_mode,
    model_family,
)
from app.services.settings_service import SettingsService

logger = logging.getLogger(__name__)

# platform_settings keys (JSON payloads stored as text; not secret).
CACHE_KEY = "model_discovery_cache"
OVERRIDES_KEY = "model_enabled_overrides"

_ANTHROPIC_MODELS_URL = "https://api.anthropic.com/v1/models"
_OPENAI_MODELS_URL = "https://api.openai.com/v1/models"
_HTTP_TIMEOUT = 15.0


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


async def _all_records(db: AsyncSession) -> tuple[list[dict], dict[str, bool], str | None]:
    svc = SettingsService(db)
    cache = await _load_cache(svc)
    overrides = await _load_overrides(svc)
    merged = _merge(_seed_models(), cache.get("models", []))
    return merged, overrides, cache.get("discovered_at")


def _payload_from_records(records: list[dict], enabled_only: bool, overrides: dict[str, bool]) -> dict:
    """Group flat records into the catalog_payload() shape.

    default_model per mode = the seed default if it is enabled, else the first
    enabled model of that mode (keeps the UI's default sane when an admin turns
    the seed default off). Guards still coerce to a safe value at runtime.
    """
    modes_out = []
    for mode, entry in MODEL_CATALOG.items():
        mode_recs = [r for r in records if r["mode"] == mode]
        if enabled_only:
            mode_recs = [r for r in mode_recs if _is_enabled(r, overrides)]

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
    """catalog_payload() shape, but only ENABLED models (what the UI offers)."""
    records, overrides, _ = await _all_records(db)
    return _payload_from_records(records, enabled_only=True, overrides=overrides)


async def get_admin_catalog(db: AsyncSession) -> dict:
    """Full catalog for the admin UI: every known model with enabled + source."""
    records, overrides, discovered_at = await _all_records(db)
    payload = _payload_from_records(records, enabled_only=False, overrides=overrides)
    payload["discovered_at"] = discovered_at
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


async def _discover_anthropic() -> list[dict]:
    if not settings.anthropic_api_key:
        return []
    headers = {
        "x-api-key": settings.anthropic_api_key,
        "anthropic-version": "2023-06-01",
    }
    out: list[dict] = []
    try:
        async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as client:
            resp = await client.get(_ANTHROPIC_MODELS_URL, headers=headers)
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
    return out


async def _discover_openai() -> list[dict]:
    if not settings.openai_api_key:
        return []
    headers = {"Authorization": f"Bearer {settings.openai_api_key}"}
    out: list[dict] = []
    try:
        async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as client:
            resp = await client.get(_OPENAI_MODELS_URL, headers=headers)
            resp.raise_for_status()
            for m in resp.json().get("data", []):
                mid = m.get("id", "")
                # Only GPT/o-series that the codex_cli harness can actually run
                # (is_model_allowed_for_mode drops e.g. gpt-5-codex).
                if model_family(mid) != "codex_cli":
                    continue
                if not is_model_allowed_for_mode("codex_cli", mid):
                    continue
                out.append(
                    {
                        "mode": "codex_cli",
                        "provider": "codex",
                        "value": mid,
                        "label": mid,
                        "tier": "Discovered",
                        "source": "discovered",
                    }
                )
    except Exception as e:  # noqa: BLE001 — discovery is best-effort
        logger.warning("OpenAI model discovery failed: %s", e)
    return out


async def discover(db: AsyncSession) -> dict:
    """Query provider APIs, store the non-seed extras, return an admin catalog.

    Only models NOT already in the seed are cached (the seed is authoritative
    for its own strings). Newly discovered models stay DISABLED until an admin
    enables them.
    """
    anthropic = await _discover_anthropic()
    openai = await _discover_openai()
    found = anthropic + openai

    seed_keys = {(r["mode"], r["provider"], r["value"]) for r in _seed_models()}
    extras: list[dict] = []
    seen: set[tuple[str, str, str]] = set()
    for rec in found:
        key = (rec["mode"], rec["provider"], rec["value"])
        if key in seed_keys or key in seen:
            continue
        seen.add(key)
        extras.append(rec)

    svc = SettingsService(db)
    # Timestamp comes from real wall-clock here (service call, not a workflow),
    # so datetime.now is fine.
    cache = {
        "discovered_at": datetime.now(timezone.utc).isoformat(),
        "models": extras,
        "providers_queried": {
            "anthropic": bool(settings.anthropic_api_key),
            "openai": bool(settings.openai_api_key),
        },
    }
    await svc.set(CACHE_KEY, json.dumps(cache))
    await db.commit()
    logger.info(
        "Model discovery: %d anthropic + %d openai found, %d new extras cached",
        len(anthropic), len(openai), len(extras),
    )
    result = await get_admin_catalog(db)
    result["last_discovery"] = {
        "anthropic_found": len(anthropic),
        "openai_found": len(openai),
        "new_extras": len(extras),
        "anthropic_queried": bool(settings.anthropic_api_key),
        "openai_queried": bool(settings.openai_api_key),
    }
    return result
