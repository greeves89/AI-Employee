"""MS Graph MCP Server — per-AGENT transport.

Endpoint: POST /mcp/msgraph/{agent_id}
Auth:      X-Agent-ID + Authorization: Bearer <agent_hmac_token>

Tool definitions, Graph calls, and JSON-RPC dispatch live in
``app.core.msgraph_mcp`` (shared with the external per-user transport in
``mcp_msgraph_external.py``). This module only adds agent-token auth and resolves
the Microsoft access token from the agent's OWNER's connected account.
"""

import logging

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import async_session_factory
from app.dependencies import agent_zugang_pruefen
from app.models.agent import Agent
from app.core import ms_access
from app.core.msgraph_mcp import handle_mcp_request, mcp_error

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/mcp", tags=["mcp-msgraph"])


async def _get_access_token(agent_id: str, db: AsyncSession) -> str | None:
    """Valid Microsoft access token for the agent's owner user (auto-refreshed).

    Returns None when the agent has no owner, the owner has not connected M365,
    or the token is expired without a refresh token — all of which the MCP
    dispatch surfaces to the model as "Microsoft account not connected".
    """
    agent = (await db.execute(select(Agent).where(Agent.id == agent_id))).scalar_one_or_none()
    if not agent or not agent.user_id:
        return None
    # EINE Stelle entscheidet: eigenes Konto des Agenten, sonst das des Besitzers
    # (core/agenten_konto.token_fuer — ohne stillen Rückfall, wenn das eigene scheitert).
    from app.core.agenten_konto import token_fuer
    return await token_fuer(db, agent)


async def _teams_gesendet_merken(agent_id: str, body: dict, resp: dict, modus: str) -> None:
    """Im Modus „Schreiben“ direkt gesendete Teams-Nachrichten merken (Schleifenschutz,
    services/teams_gateway) — sonst läse der Agent seine eigene Nachricht als neue Anfrage."""
    import re as _re

    from app.core.msgraph_mcp import TEAMS_SENDE_TOOLS

    params = (body.get("params") or {}) if body.get("method") == "tools/call" else {}
    if modus != ms_access.SCHREIBEN or params.get("name") not in TEAMS_SENDE_TOOLS:
        return
    ergebnis = resp.get("result") or {}
    if ergebnis.get("isError"):
        return
    text = " ".join(str(c.get("text") or "") for c in ergebnis.get("content") or [] if isinstance(c, dict))
    treffer = _re.search(r"ID: (\S+)", text)
    args = params.get("arguments") or {}
    ziel = {"chat_id": str(args.get("chat_id"))} if params.get("name") == "ms_send_chat_message" else {
        "team_id": str(args.get("team_id")), "channel_id": str(args.get("channel_id"))}
    if treffer:
        from app.services.teams_gateway import als_eigen_merken

        await als_eigen_merken(agent_id, ziel, treffer.group(1))


_TEAMS_HINWEIS = {
    "freigabe": ("Nicht gesendet: Die Teams-Nachricht wartet auf Freigabe durch deinen Menschen. Nach der "
                 "Freigabe sendet die Plattform genau diesen Text — nicht noch einmal selbst versuchen."),
    "gesendet": "Teams-Nachricht gesendet.",
    "aus": "Nicht gesendet: Dieser Agent darf in Microsoft 365 nur lesen.",
    "zu_viele": ("Nicht gesendet: Es warten schon 20 Teams-Nachrichten auf Freigabe. Erst wenn dein Mensch "
                 "sie bearbeitet hat, kommen weitere dazu."),
    "zu_lang": ("Nicht gesendet: Zu lang für eine Freigabe — dein Mensch muss den ganzen Text sehen. "
                "Bitte kürzen."),
}


@router.post("/msgraph/{agent_id}")
async def mcp_msgraph_endpoint(agent_id: str, request: Request):
    """MCP Streamable HTTP endpoint for MS Graph tools (agent transport)."""
    # Auth: the agent_id is already in the URL path; the agent's MCP client sends
    # only `Authorization: Bearer <token>` (no X-Agent-ID header), so verify the
    # bearer directly against the HMAC token derived from the path agent_id.
    token = request.headers.get("Authorization", "").removeprefix("Bearer ").strip()
    if not agent_zugang_pruefen(request, agent_id, token):
        return JSONResponse(mcp_error(None, -32600, "Unauthorized"), status_code=401)

    try:
        body = await request.json()
    except Exception:
        return JSONResponse(mcp_error(None, -32700, "Parse error"), status_code=400)

    # Der Token beweist nur, WELCHER Agent fragt — nicht, dass er an das Postfach darf.
    from app.core.faehigkeiten import anbindung_zugewiesen
    async with async_session_factory() as db:
        if not await anbindung_zugewiesen(db, agent_id, "microsoft"):
            return JSONResponse(mcp_error(body.get("id"), -32600,
                                          "Die Microsoft-Anbindung ist diesem Agenten nicht zugewiesen."), status_code=403)

    async def resolve_token() -> str | None:
        async with async_session_factory() as db:
            return await _get_access_token(agent_id, db)

    # Wie darf dieser Agent senden? EINE Entscheidung für Werkzeuge und Teams-Kanal
    # (core/ms_access.sendemodus): Nur-Lesen-Schalter, Einstellung des Agenten, Sekretariat
    # und Rechte-Matrix „E-Mail / M365 senden“ (stuft nur herab).
    async with async_session_factory() as db:
        agent = (await db.execute(select(Agent).where(Agent.id == agent_id))).scalar_one_or_none()
        modus = await ms_access.sendemodus(db, agent)
        im_sekretariat = False
        if agent is not None:
            from app.core.sekretariat import ist_sekretariats_agent

            im_sekretariat = await ist_sekretariats_agent(db, agent_id)
    may_write = modus == ms_access.SCHREIBEN
    entwurf = teams_freigabe = None
    if modus == ms_access.ENTWURF:
        from app.core.mail_freigabe import anfragen

        async def entwurf(message_id: str, betreff: str) -> str:
            return await anfragen(agent_id, message_id, betreff)

        async def teams_freigabe(ziel: dict, text: str) -> str:
            # Teams-Nachricht im Modus „Entwurf“: gebundene Freigabe, der Server sendet danach.
            from app.services.teams_gateway import senden_nach_regeln

            ergebnis = await senden_nach_regeln(agent, ziel, text, format_="text", quelle="werkzeug")
            return _TEAMS_HINWEIS.get(ergebnis, "Die Teams-Nachricht wurde nicht gesendet.")

    verschieben = (im_sekretariat and body.get("method") == "tools/call"
                   and (body.get("params") or {}).get("name") == "ms_move_email")
    betreff = await _betreff_vor_dem_verschieben(body, resolve_token) if verschieben else None
    resp, status = await handle_mcp_request(body, resolve_token, write_enabled=may_write, entwurf_freigabe=entwurf,
                                            teams_freigabe=teams_freigabe)
    if verschieben and not ((resp.get("result") or {}).get("isError")):
        await _verschieben_protokollieren(agent, body, betreff)
    await _teams_gesendet_merken(agent_id, body, resp, modus)
    return JSONResponse(resp, status_code=status)


async def _betreff_vor_dem_verschieben(body: dict, resolve_token) -> str | None:
    """Betreff der Mail, die ein Sekretariats-Agent gleich verschiebt — fürs Protokoll.
    Danach trägt die Mail eine neue Kennung; deshalb vorher."""
    from app.core.msgraph_mcp import _gid, _graph

    mail_id = ((body.get("params") or {}).get("arguments") or {}).get("email_id")
    try:
        token = await resolve_token()
        if not token or not mail_id:
            return None
        return (await _graph("GET", f"/me/messages/{_gid(mail_id)}", token, params={"$select": "subject"})).get("subject")
    except Exception:  # noqa: BLE001 — das Protokoll darf das Verschieben nie verhindern
        return None


async def _verschieben_protokollieren(agent, body: dict, betreff: str | None) -> None:
    """Protokoll „Was das Sekretariat heute getan hat“ (core/sekretariat.protokoll)."""
    from app.core.sekretariat import protokollieren

    ziel = str(((body.get("params") or {}).get("arguments") or {}).get("folder") or "").strip()
    namen = {"archive": "Archiv", "deleteditems": "Gelöschte Elemente", "junkemail": "Junk-E-Mail"}
    ziel = namen.get(ziel.lower(), ziel if len(ziel) <= 40 else "einen eigenen Ordner")
    try:
        async with async_session_factory() as db:
            await protokollieren(db, str(agent.user_id or ""), "verschoben",
                                 f"Mail „{(betreff or '(ohne Betreff)')[:200]}“ nach „{ziel}“ verschoben",
                                 agent_id=agent.id)
            await db.commit()
    except Exception as e:  # noqa: BLE001
        logger.warning("Sekretariat: Verschieben nicht protokolliert: %s", e)
