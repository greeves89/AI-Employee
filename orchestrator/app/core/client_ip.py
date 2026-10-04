"""Die Adresse des Aufrufers — EINE Stelle für Rate-Limit und Anmelde-Bremse.

``request.client.host`` ist bereits die Adresse, die uvicorn aus
``X-Forwarded-For`` ermittelt hat — und zwar nur von den Proxys, denen es per
``--forwarded-allow-ips`` vertraut (siehe orchestrator/Dockerfile). Hier wird
``X-Forwarded-For`` bewusst NICHT noch einmal selbst ausgewertet: das wäre ein
zweiter, von außen fälschbarer Weg.
"""

from __future__ import annotations

import ipaddress

#: Interne Netze, in denen ein vorgeschalteter Proxy (Caddy, cloudflared, nginx im
#: Docker-Netz) sitzt. Bewusst ausdruecklich statt ``is_private`` — das zaehlt auch
#: Dokumentations- und Sondernetze mit.
_INTERN = tuple(ipaddress.ip_network(n) for n in (
    "10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "127.0.0.0/8", "169.254.0.0/16",
    "::1/128", "fc00::/7", "fe80::/10",
))

#: Kopfzeilen, an denen man erkennt, dass ein Proxy dazwischen sitzt.
_PROXY_SPUREN = ("x-forwarded-for", "cf-ray", "cf-connecting-ip", "forwarded")


def client_ip(request) -> str | None:
    """Die von uvicorn ermittelte Adresse, ``None`` ohne Verbindungsdaten."""
    client = getattr(request, "client", None)
    host = getattr(client, "host", None)
    return host or None


def eindeutige_client_ip(request) -> str | None:
    """Die Adresse, wenn sie wirklich EINEN Absender meint — sonst ``None``.

    Kommt eine Anfrage über einen Proxy, dessen Weiterleitungsangabe uvicorn
    nicht übernimmt (z. B. Cloudflare-Tunnel → Caddy: Caddy schreibt dann die
    interne Adresse von cloudflared in ``X-Forwarded-For``), sehen ALLE Nutzer
    gleich aus: eine private/interne Adresse mit Proxy-Spuren. Eine Sperre auf
    diese Adresse sperrte alle zugleich aus — dann lieber keine IP-Sperre.
    """
    ip = client_ip(request)
    if not ip:
        return None
    try:
        adresse = ipaddress.ip_address(ip)
    except ValueError:
        return None  # "testclient", "unknown" …
    if any(adresse in netz for netz in _INTERN if netz.version == adresse.version):
        headers = getattr(request, "headers", None) or {}
        if any(headers.get(k) for k in _PROXY_SPUREN):
            return None
    return ip
