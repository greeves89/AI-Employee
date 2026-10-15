"""Wer welchen Suchindex nutzen darf — an JEDEM Zugang dieselbe Entscheidung.

Den Nachrichtenindex erreicht man auf drei Wegen: das eigene Werkzeug
``news_search`` (``/agent-search/news``), die allgemeine Websuche, wenn der
Admin ``brave_news`` als Provider eingestellt hat (``/agent-search/web``), und
die Websuche der Sprachfront. Stand die Pruefung nur am ersten Weg, war die
Sperre ueber die beiden anderen umgehbar (Review zu #812, K2). Deshalb liegt
die Entscheidung hier und alle drei fragen dieselbe Funktion.
"""

from __future__ import annotations

import logging

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.log_redaction import scrub_log

logger = logging.getLogger(__name__)

NEWS = "news"
WEB = "web"


async def agent_may_use_index(agent_id: str, index: str, db: AsyncSession) -> bool:
    """Ob der BESITZER des Agenten diesen Suchindex nutzen darf.

    Die Berechtigung haengt am Menschen, nicht am Agenten — ein Agent ist nur
    das Werkzeug seines Besitzers und soll nicht mehr duerfen als dieser.

    - unbekannter Agent: nein.
    - kein Besitzer: nur, wenn der Agent ausdruecklich als Plattform-Agent
      freigegeben ist (``is_platform_agent``). ``user_id=NULL`` allein ist
      meist ein Versehen (geloeschter Nutzer, Skript ohne Besitzer), keine
      Entscheidung — siehe ``models/agent.py`` (Review zu #812, K4).
    - Besitzer gesetzt, aber geloescht: nein — sonst waere ein geloeschter
      Nutzer der Weg zu mehr Rechten, als der lebende hatte.
    - sonst: die Rolle des Besitzers entscheidet.
    """
    from sqlalchemy import select

    from app.core.permissions import can_use_search_index, get_effective_permissions
    from app.models.agent import Agent
    from app.models.user import User

    agent = (await db.execute(select(Agent).where(Agent.id == agent_id))).scalar_one_or_none()
    if agent is None:
        return False
    if not agent.user_id:
        if getattr(agent, "is_platform_agent", False):
            return True
        logger.warning(
            "Agent %s hat keinen Besitzer und ist kein Plattform-Agent — Suchindex %s verweigert",
            scrub_log(agent_id), scrub_log(index),
        )
        return False
    user = (await db.execute(select(User).where(User.id == agent.user_id))).scalar_one_or_none()
    if user is None:
        logger.warning(
            "Agent %s verweist auf nicht vorhandenen Nutzer %s — Suchindex %s verweigert",
            scrub_log(agent_id), scrub_log(agent.user_id), scrub_log(index),
        )
        return False
    perms = await get_effective_permissions(user, db)
    return can_use_search_index(perms, index)
