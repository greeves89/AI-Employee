"""Docker-Wächter starten: ``python -m app.docker_waechter`` (Dienst ``docker-waechter``).

Umgebung:
  WAECHTER_UPSTREAM       Socket-Proxy, Standard ``docker-socket-proxy:2375``
  DOCKER_WAECHTER_TOKEN   Dienst-Schlüssel für ``/aie/compose`` (vom Orchestrator)
  AGENT_MOUNT_CATALOG, SECONDBRAIN_BASE   wie im Orchestrator: erlaubte Host-Pfade
  (der Orchestrator sieht Second Brains unter /srv/secondbrain, Standard der Einstellung)
"""

import asyncio
import logging
import os
from types import SimpleNamespace

import docker

from app.config import settings
from app.core.mounts import parse_mount_catalog
from app.docker_waechter import compose
from app.docker_waechter.proxy import Waechter


def erlaubte_host_pfade() -> list[str]:
    """Second Brains und die Einträge des festen Mount-Katalogs — sonst kein Host-Pfad."""
    katalog = parse_mount_catalog(settings.agent_mount_catalog or "")
    return [settings.secondbrain_base, *(e.host_path for e in katalog.values())]


async def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    host, _, port = os.environ.get("WAECHTER_UPSTREAM", "docker-socket-proxy:2375").partition(":")
    upstream = (host, int(port or 2375))
    runner = SimpleNamespace(client=docker.DockerClient(base_url=f"tcp://{host}:{upstream[1]}", timeout=120))
    schluessel = os.environ.get("DOCKER_WAECHTER_TOKEN", "")
    if len(schluessel) < 32:
        logging.getLogger("docker_waechter").warning("DOCKER_WAECHTER_TOKEN fehlt — Agenten-Apps sind gesperrt")
        schluessel = ""
    pfade = erlaubte_host_pfade()
    # Dasselbe Profil, das docker_service den Agenten mitgibt — nur genau das ist erlaubt.
    from app.services.docker_service import _SECCOMP_PROFILE
    waechter = Waechter(upstream, pfade, dienst_schluessel=schluessel, seccomp_profil=_SECCOMP_PROFILE,
                        compose=lambda daten: compose.bearbeiten(runner, daten),
                        orchestrator_name=settings.orchestrator_container_name)
    server = await waechter.starten("0.0.0.0", 2375)
    logging.getLogger("docker_waechter").info("Docker-Wächter vor %s:%s, erlaubte Host-Pfade: %s",
                                              host, upstream[1], ", ".join(pfade))
    async with server:
        await server.serve_forever()


if __name__ == "__main__":
    asyncio.run(main())
