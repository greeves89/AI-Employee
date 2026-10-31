"""Sicherheitsstatus der Anlage — die Ampel unter Admin → Betrieb.

Jede Prüfung liest nur (Einstellungen, Datenbank, Docker) und sagt in einem Satz,
was ihr Ergebnis bedeutet und wie man es behebt. Nichts hier ändert etwas: Die
Seite soll einem Administrator zeigen, wo die Anlage steht — und dieselbe Liste
ist der Nachweis im Sicherheitskonzept für Kunden.

Zustände: ``ok`` · ``warnung`` (sollte behoben werden) · ``kritisch`` (Lücke) ·
``info`` (bewusste Entscheidung, zur Kenntnis).
"""

from __future__ import annotations

import logging
import os

from sqlalchemy import select

from app.config import settings

logger = logging.getLogger(__name__)

OK, WARNUNG, KRITISCH, INFO = "ok", "warnung", "kritisch", "info"


def _punkt(schluessel: str, titel: str, stand: str, text: str, beheben: str = "") -> dict:
    return {"schluessel": schluessel, "titel": titel, "stand": stand, "text": text, "beheben": beheben}


def _einstellungen() -> list[dict]:
    punkte = []
    acl = bool(settings.redis_acl_enabled)
    punkte.append(_punkt(
        "redis_acl", "Zugriffsregeln für Redis", OK if acl else KRITISCH,
        "Agenten haben in Redis nur ihre eigenen Schlüssel." if acl else
        "Ohne Zugriffsregeln kann ein Agent Chats anderer Nutzer mitlesen und Freigaben selbst setzen.",
        "" if acl else "In der .env REDIS_ACL_ENABLED=true setzen und den Orchestrator neu starten."))

    standard_pw = "changeme-redis-password" in (settings.redis_url or "")
    punkte.append(_punkt(
        "redis_passwort", "Redis-Passwort", KRITISCH if standard_pw else OK,
        "Redis läuft mit dem Standardpasswort aus der Vorlage." if standard_pw else "Eigenes Passwort gesetzt.",
        "REDIS_PASSWORD in der .env durch ein zufälliges ersetzen." if standard_pw else ""))

    try:
        from cryptography.fernet import Fernet
        Fernet(settings.encryption_key.encode())
        schluessel_ok = True
    except Exception:  # noqa: BLE001 — leer oder kein gültiger Fernet-Schlüssel
        schluessel_ok = False
    punkte.append(_punkt(
        "encryption_key", "Schlüssel für gespeicherte Zugangsdaten", OK if schluessel_ok else KRITISCH,
        "ENCRYPTION_KEY ist ein gültiger Schlüssel." if schluessel_ok else
        "ENCRYPTION_KEY fehlt oder ist ungültig. Gespeicherte Zugangsdaten können beim nächsten Neustart unlesbar werden.",
        "" if schluessel_ok else "Den bereits aktiven Schlüssel in die .env übernehmen — nie einen neuen erzeugen, "
                                 "solange Daten verschlüsselt sind."))

    stark = len(settings.api_secret_key or "") >= 32
    punkte.append(_punkt(
        "api_secret", "Schlüssel für Anmeldungen und Agenten-Token", OK if stark else KRITISCH,
        "API_SECRET_KEY ist lang genug." if stark else "API_SECRET_KEY ist kürzer als 32 Zeichen.",
        "" if stark else "Einen langen Zufallswert setzen. Danach müssen sich alle neu anmelden."))

    sentinel = bool(settings.sentinel_enabled)
    punkte.append(_punkt(
        "sentinel", "Sentinel (Wächter)", OK if sentinel else WARNUNG,
        "Der Sentinel liest mit und hält Agenten bei eingeschleusten Anweisungen oder offengelegten "
        "Geheimnissen an." if sentinel else "Der Sentinel ist aus — auffällige Agenten werden nicht angehalten.",
        "" if sentinel else "In der .env SENTINEL_ENABLED=true setzen."))

    cors = os.environ.get("CORS_ALLOW_ORIGIN", "").strip()
    punkte.append(_punkt(
        "cors", "Fremde Webseiten (CORS)", OK if cors and cors != "*" else WARNUNG,
        f"Nur {cors} darf die Schnittstelle aus dem Browser ansprechen." if cors and cors != "*" else
        "Jede Webseite darf die Schnittstelle aus dem Browser ansprechen.",
        "" if cors and cors != "*" else "In der .env CORS_ALLOW_ORIGIN auf die eigene Adresse setzen, "
                                        "z. B. https://ai.example.com."))

    kiosk = bool(settings.kiosk_enabled)
    punkte.append(_punkt(
        "kiosk", "Kiosk-Ansicht ohne Anmeldung", WARNUNG if kiosk else OK,
        "Die Kiosk-Ansicht ist ohne Anmeldung erreichbar." if kiosk else "Aus.",
        "Nur auf einem echten Kiosk-Gerät einschalten (KIOSK_ENABLED)." if kiosk else ""))

    nur_lesen = bool(settings.msgraph_read_only)
    punkte.append(_punkt(
        "m365_nur_lesen", "Microsoft 365 nur lesend", INFO,
        "Agenten können Microsoft 365 nur lesen, keine Mails senden." if nur_lesen else
        "Agenten dürfen Microsoft 365 schreiben, soweit ihre Einstellung und Rechte-Matrix es erlauben.",
        ""))
    return punkte


async def _agenten(db) -> list[dict]:
    from app.core import autonomy_matrix as am
    from app.models.agent import Agent

    zeilen = (await db.execute(select(Agent.name, Agent.autonomy_level, Agent.access_policy))).all()
    mit_sudo = sorted(n for n, stufe, policy in zeilen
                      if am.effective_permissions(policy or {}, (stufe or "l3").lower()))
    return [_punkt(
        "sudo", "Agenten mit sudo", WARNUNG if mit_sudo else OK,
        f"{len(mit_sudo)} Agent(en) können Software als root installieren: {', '.join(mit_sudo[:5])}"
        + (" …" if len(mit_sudo) > 5 else "") if mit_sudo else "Kein Agent hat sudo.",
        "Wo nicht nötig, in der Feineinstellung „Pakete / Config“ auf Verboten stellen." if mit_sudo else "")]


def _docker(container_ids: list[str]) -> list[dict]:
    from app.config import get_agent_version
    from app.services.docker_service import DockerService

    punkte = []
    try:
        client = DockerService().client
        version = client.version().get("Version", "0")
    except Exception as e:  # noqa: BLE001
        return [_punkt("docker", "Docker", KRITISCH, f"Docker nicht erreichbar: {e}")]

    haupt = int((version.split(".") or ["0"])[0] or 0)
    punkte.append(_punkt(
        "docker_version", "Docker-Version", OK if haupt >= 26 else KRITISCH,
        f"Docker {version}." + ("" if haupt >= 26 else " Ab Version 26 trennt die Plattform Ordner je Nutzer "
                                                       "und bindet App-Ordner sicher ein."),
        "" if haupt >= 26 else "Docker auf Version 26 oder neuer aktualisieren."))

    aktuell = get_agent_version()
    alt = []
    for cid in container_ids:
        try:
            c = client.containers.get(cid)
        except Exception:  # noqa: BLE001
            continue
        if c.status == "running" and c.labels.get("ai-employee.version") != aktuell:
            alt.append(c.name)
    punkte.append(_punkt(
        "abbild", "Agenten auf aktuellem Stand", WARNUNG if alt else OK,
        f"{len(alt)} laufende(r) Agent(en) auf einem älteren Abbild — Schutzfunktionen neuerer Versionen "
        f"fehlen dort: {', '.join(alt[:5])}" if alt else f"Alle laufenden Agenten auf {aktuell}.",
        "Die Agenten über „Aktualisieren“ neu erstellen." if alt else ""))

    try:
        plattform = client.networks.get("ai-employee-network")
        plattform.reload()
        apps = []
        for cid in plattform.attrs.get("Containers") or {}:
            c = client.containers.get(cid)
            if str(c.labels.get("com.docker.compose.project", "")).startswith("agent-"):
                apps.append(c.name)
        punkte.append(_punkt(
            "app_netz", "Agenten-Apps vom Plattformnetz getrennt", KRITISCH if apps else OK,
            f"{len(apps)} App(s) im Netz der Plattform — sie erreichen Datenbank und Redis: {', '.join(apps[:5])}"
            if apps else "Jede App läuft im eigenen Netz ihres Agenten.",
            "Den Orchestrator neu starten: er zieht die Apps beim Start um." if apps else ""))
    except Exception:
        logger.debug("Plattformnetz nicht lesbar", exc_info=True)
    return punkte


SIGNATUR_DATEI = "/host-auth/signaturpruefung"


def _abbild_signaturen(datei: str = SIGNATUR_DATEI) -> list[dict]:
    """Ergebnis der Signaturpruefung beim letzten Update (scripts/lib/abbilder.sh)."""
    from app.config import get_agent_version
    titel = "Signaturen der Abbilder"
    try:
        ergebnis, version, _ = open(datei).read().split()[:3]
    except (OSError, ValueError):
        return [_punkt("signaturen", titel, INFO,
                       "Noch kein Update mit Signaturprüfung gelaufen (ab 1.419.0).",
                       "Beim nächsten ./scripts/update.sh wird das Ergebnis hier angezeigt.")]
    aktuell = get_agent_version()
    if ergebnis == "geprueft" and version == aktuell:
        return [_punkt("signaturen", titel, OK,
                       f"Die Abbilder von {version} sind beim Update per cosign geprüft worden.")]
    if ergebnis == "geprueft":
        return [_punkt("signaturen", titel, WARNUNG,
                       f"Zuletzt geprüft wurde {version}, installiert ist {aktuell}.",
                       "Update über ./scripts/update.sh ausführen, nicht von Hand.")]
    texte = {
        "cosign_fehlt": ("cosign ist auf dem Server nicht installiert — Abbilder werden ungeprüft benutzt.",
                         "cosign installieren (Handbuch Kap. 37.4), danach ./scripts/update.sh."),
        "aus": ("Die Signaturprüfung ist abgeschaltet (AI_EMPLOYEE_SIGNATUR_PRUEFEN).",
                "Schalter aus der .env entfernen."),
        "ungueltig": ("Beim letzten Update war eine Signatur ungültig; die Abbilder wurden lokal gebaut.",
                      "Ursache prüfen, bevor erneut aus der Registry geholt wird."),
    }
    text, beheben = texte.get(ergebnis, (f"Beim letzten Update nicht geprüft ({ergebnis}).",
                                         "Protokoll von ./scripts/update.sh ansehen."))
    return [_punkt("signaturen", titel, WARNUNG, text, beheben)]


async def _schluessel(db) -> list[dict]:
    """Wie viele vergebene Keys ein Agent im Klartext sieht (core/egress.py)."""
    from app.core import egress
    from app.models.agent_secret import AgentSecret, AgentSecretAssignment
    from app.models.agent import Agent

    # Je Zuweisung: Key, Ziele und ob der Behaelter des Agenten schon mit dem
    # Proxy entstand. Ein Agent von vor dem Einschalten hat den Klartext noch in sich.
    vergeben = (await db.execute(
        select(AgentSecret.key_name, AgentSecret.erlaubte_hosts, Agent.config)
        .join(AgentSecretAssignment, AgentSecretAssignment.secret_id == AgentSecret.id)
        .join(Agent, Agent.id == AgentSecretAssignment.agent_id)
        .where(AgentSecret.is_active.is_(True))
    )).all()
    # Die Git-Anbindung der Plattform zaehlt wie ein Key mit festen Zielen.
    from app.models.oauth_integration import OAuthIntegration, OAuthProvider
    anbindung = (await db.execute(select(OAuthIntegration.id).where(
        OAuthIntegration.provider == OAuthProvider.GITHUB, OAuthIntegration.user_id.is_(None)))).first()
    if anbindung:
        for (cfg,) in (await db.execute(select(Agent.config))).all():
            if "github" in ((cfg or {}).get("integrations") or []):
                vergeben.append(("Git-Anbindung", ["git"], cfg))
    titel = "Keys in Agenten"
    if settings.egress_proxy_enabled and not egress.aktiv():
        return [_punkt("egress", titel, KRITISCH,
                       "Der Egress-Proxy ist eingeschaltet, aber ohne Dienst-Schlüssel — er bleibt aus.",
                       "scripts/update.sh legt EGRESS_PROXY_TOKEN in der .env an; danach den Orchestrator "
                       "neu starten.")]
    if not vergeben:
        return [_punkt("egress", titel, OK, "Kein Agent hat einen Key aus der Schlüsselverwaltung.")]
    klartext = sorted({k for k, hosts, _ in vergeben if not (egress.aktiv() and hosts)})
    alte_behaelter = {k for k, hosts, cfg in vergeben
                      if egress.aktiv() and hosts and egress.veraltet(cfg)}
    if not klartext and not alte_behaelter:
        return [_punkt("egress", titel, OK,
                       f"Alle {len({k for k, _, _ in vergeben})} vergebenen Keys sehen Agenten nur als "
                       "Platzhalter; der Egress-Proxy setzt sie erst am erlaubten Ziel ein.")]
    if not klartext:
        return [_punkt("egress", titel, WARNUNG,
                       f"{len(alte_behaelter)} Key(s) stecken noch im Klartext in Agenten, die vor dem "
                       f"Einschalten des Egress-Proxys entstanden sind: {', '.join(sorted(alte_behaelter)[:5])}.",
                       "Die betroffenen Agenten aktualisieren (sie zeigen „Aktualisieren“).")]
    liste = ", ".join(klartext[:5]) + (" …" if len(klartext) > 5 else "")
    if not egress.aktiv():
        return [_punkt("egress", titel, WARNUNG,
                       f"{len(klartext)} Key(s) liegen im Klartext in Agenten: {liste}. "
                       "Ein Agent kann sie ausgeben oder an beliebige Ziele schicken.",
                       "Egress-Proxy einschalten (EGRESS_PROXY_ENABLED=true in der .env) und je Key "
                       "unter Schlüssel die erlaubten Ziele eintragen.")]
    dazu = (f" Dazu {len(alte_behaelter)} Key(s) in Agenten von vor dem Einschalten (Aktualisieren)."
            if alte_behaelter else "")
    return [_punkt("egress", titel, WARNUNG,
                   f"{len(klartext)} Key(s) ohne erlaubte Ziele liegen weiter im Klartext in Agenten: {liste}.{dazu}",
                   "Unter Schlüssel je Key die erlaubten Ziele eintragen, z. B. api.github.com.")]


async def stand(db) -> dict:
    """Alle Punkte, kritischste zuerst, plus eine Zusammenfassung."""
    import asyncio

    from app.models.agent import Agent

    ids = [c for (c,) in (await db.execute(
        select(Agent.container_id).where(Agent.container_id.is_not(None)))).all()]
    punkte = (_einstellungen() + await _agenten(db) + await _schluessel(db) + _abbild_signaturen()
              + await asyncio.to_thread(_docker, ids))
    rang = {KRITISCH: 0, WARNUNG: 1, INFO: 2, OK: 3}
    punkte.sort(key=lambda p: rang[p["stand"]])
    zaehlung = {s: sum(1 for p in punkte if p["stand"] == s) for s in (KRITISCH, WARNUNG, OK, INFO)}
    return {"punkte": punkte, "zaehlung": zaehlung}
