"""Realtime voice model catalog + credential resolution per provider.

Single source of truth for which realtime speech models exist per provider type,
and how to turn a configured AI-Account into ready-to-use credentials for the
matching ``RealtimeVoiceSession`` engine.

Configure the provider (AWS Bedrock / Azure Realtime / Brave) as an **AI-Account**
(encrypted creds, reusable). The voice setup then lists the models below for each
active account and lets the user pick model ↔ provider.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

# provider_type → {label, engine, models[]}. `engine` selects the session backend.
REALTIME_PROVIDERS: dict[str, dict] = {
    "bedrock": {
        "label": "AWS Bedrock",
        "engine": "nova_sonic",
        "models": [
            {"id": "amazon.nova-2-sonic-v1:0", "label": "Nova Sonic 2"},
            {"id": "amazon.nova-sonic-v1:0", "label": "Nova Sonic"},
        ],
    },
    "azure-realtime": {
        "label": "Azure OpenAI Realtime",
        "engine": "azure_realtime",  # engine not yet implemented — listed for config
        "models": [
            {"id": "gpt-4o-realtime-preview", "label": "GPT-4o Realtime"},
            {"id": "gpt-4o-mini-realtime-preview", "label": "GPT-4o mini Realtime"},
        ],
    },
}

# Engines that actually have a working session backend today.
IMPLEMENTED_ENGINES = {"nova_sonic"}


def is_realtime_provider(provider_type: str | None) -> bool:
    return (provider_type or "") in REALTIME_PROVIDERS


def models_for_provider(provider_type: str) -> list[dict]:
    return REALTIME_PROVIDERS.get(provider_type, {}).get("models", [])


def engine_for_provider(provider_type: str) -> str | None:
    return REALTIME_PROVIDERS.get(provider_type, {}).get("engine")


def resolve_credentials(account) -> dict | None:
    """Turn an AIAccount row into realtime credentials, or None if unusable.

    Returns e.g. {"engine": "nova_sonic", "access_key": ..., "secret_key": ...,
                  "session_token": None, "region": "us-east-1", "model_id": ...}.
    """
    from app.core.encryption import decrypt_token

    pt = getattr(account, "provider_type", None)
    extra = (getattr(account, "extra", None) or {})
    enc = getattr(account, "api_key_encrypted", None)
    models = getattr(account, "models", None) or []
    model_id = (models[0] or {}).get("name") if models and isinstance(models[0], dict) else None

    if pt == "bedrock":
        try:
            secret = decrypt_token(enc) if enc else ""
        except Exception:  # noqa: BLE001
            logger.warning("bedrock account %s: secret decrypt failed", getattr(account, "id", "?"))
            return None
        access = (extra.get("aws_access_key_id") or "").strip()
        if not (access and secret):
            return None
        return {
            "engine": "nova_sonic",
            "access_key": access,
            "secret_key": secret,
            "session_token": (extra.get("aws_session_token") or None),
            "region": (extra.get("aws_region") or "us-east-1").strip(),
            "model_id": model_id or "amazon.nova-2-sonic-v1:0",
        }

    # azure-realtime / brave-search: config surface exists; engine TBD.
    return None
