#!/usr/bin/env python3
"""
AI-Employee Computer-Use Bridge
Runs on the user's Mac/Windows machine and connects to the AI-Employee orchestrator.
Provides AXUIElement-based desktop control + screenshot capture to remote agents.

Usage:
    python bridge.py --url wss://your-ai-employee.com --token YOUR_JWT_TOKEN

Or set env vars:
    AI_EMPLOYEE_URL=wss://... AI_EMPLOYEE_TOKEN=... python bridge.py
"""
from __future__ import annotations

import argparse
import asyncio
import base64
import io
import json
import logging
import os
import platform
import queue
import ssl
import sys
import threading
import time
import urllib.parse
from typing import Any

import websockets

try:
    import certifi
except ImportError:  # pragma: no cover — kept optional, see _default_ssl_context
    certifi = None  # type: ignore[assignment]


def _default_ssl_context() -> ssl.SSLContext:
    """``ssl.create_default_context()``, but pointed at certifi's CA bundle.

    A PyInstaller-frozen build has no OpenSSL default cert directory to fall
    back on the way a normal Python install does — ``load_default_certs()``
    then finds nothing, and even a perfectly valid, CA-signed certificate
    fails verification. This is why _system_handshake_ok kept returning
    False for a real Cloudflare cert in the packaged app while the exact
    same call succeeded from a plain `python3` shell (reported + diagnosed
    2026-08-27) — the frozen build was silently falling through to strict
    TLS pinning for a normal, publicly-trusted host, not just self-signed
    ones. requirements.txt/spec files now bundle certifi's cacert.pem so
    this file is always present in a shipped build.
    """
    if certifi is not None:
        return ssl.create_default_context(cafile=certifi.where())
    return ssl.create_default_context()


def _setup_logging() -> logging.Logger:
    logger = logging.getLogger(__name__)
    logger.setLevel(logging.INFO)
    formatter = logging.Formatter("[%(asctime)s] [Bridge] %(message)s")

    if not logging.getLogger().handlers:
        console = logging.StreamHandler()
        console.setFormatter(formatter)
        logging.getLogger().addHandler(console)
        logging.getLogger().setLevel(logging.INFO)

    try:
        log_dir = os.path.expanduser("~/Library/Logs/ai-employee")
        if platform.system() != "Darwin":
            log_dir = os.path.join(os.path.expanduser("~"), ".ai-employee", "logs")
        os.makedirs(log_dir, exist_ok=True)
        file_handler = logging.FileHandler(os.path.join(log_dir, "bridge.log"))
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)
    except Exception:
        pass

    return logger


log = logging.getLogger(__name__)
log = _setup_logging()

try:
    from _version import BRIDGE_VERSION
except ImportError:
    BRIDGE_VERSION = "dev"

# ── Platform checks ──────────────────────────────────────────────────────────

IS_MAC = platform.system() == "Darwin"
IS_WIN = platform.system() == "Windows"


# ── TLS-Vertrauen (eine Wahrheit fuer Tray-HTTP UND Bridge-WebSocket) ─────────
#
# Vorher stand hier (und in tray_app.py noch einmal) ein globaler SSL-Kontext
# mit ``verify_mode = CERT_NONE`` — JEDE Verbindung, Login samt Passwort und
# Token eingeschlossen, war gegen einen Mitleser in der Mitte ungeschuetzt.
# Der Grund war verstaendlich (Kundenserver mit selbstsigniertem Zertifikat),
# die Loesung nicht: Verifikation abschalten schuetzt niemanden.
#
# So laeuft es jetzt, wie bei SSH:
#   1. Oeffentlich gueltiges Zertifikat  → normale System-Verifikation.
#   2. Selbstsigniertes Zertifikat       → beim ERSTEN Kontakt wird es
#      gespeichert („gepinnt") und der Fingerabdruck laut protokolliert.
#      Ab dann akzeptiert die Bridge NUR noch genau dieses Zertifikat —
#      geprueft im TLS-Handshake selbst, BEVOR Passwort oder Token die
#      Leitung beruehren (cadata + VERIFY_X509_PARTIAL_CHAIN, deshalb
#      funktioniert auch ein von einer Firmen-CA ausgestelltes Blatt).
#   3. Aendert sich das Zertifikat       → harter Abbruch mit beiden
#      Fingerabdruecken. Neu vertraut wird nur bei einer ausdruecklichen
#      Neu-Anmeldung (Einstellungen → Anmelden), nie stillschweigend.
#
# Der Pin liegt in ~/.ai_employee_bridge.json unter "tls" und gilt pro Host.
# Fuer Notfaelle gibt es {"tls": {"mode": "insecure"}} — bewusst nur von Hand
# eintragbar und mit lauter Warnung, nie Voreinstellung.


class TlsTrustError(RuntimeError):
    """Das Server-Zertifikat ist nicht (mehr) vertrauenswuerdig."""


def _config_read() -> dict:
    try:
        with open(BRIDGE_CONFIG_PATH, encoding="utf-8") as fh:
            cfg = json.load(fh)
        return cfg if isinstance(cfg, dict) else {}
    except (OSError, ValueError):
        return {}


def _config_write(cfg: dict) -> None:
    tmp = BRIDGE_CONFIG_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(cfg, fh, indent=2)
    os.replace(tmp, BRIDGE_CONFIG_PATH)
    try:
        os.chmod(BRIDGE_CONFIG_PATH, 0o600)
    except OSError:
        pass


def _host_port(url: str) -> tuple[str, int]:
    parsed = urllib.parse.urlparse(url if "//" in url else f"//{url}")
    host = parsed.hostname or ""
    port = parsed.port or (80 if parsed.scheme in ("http", "ws") else 443)
    return host, port


def _fingerprint(der: bytes) -> str:
    import hashlib
    return hashlib.sha256(der).hexdigest()


def format_fingerprint(fp: str) -> str:
    """``ab12…`` → ``AB:12:…`` — so, wie Browser und openssl ihn anzeigen."""
    fp = fp.replace(":", "").lower()
    return ":".join(fp[i:i + 2] for i in range(0, len(fp), 2)).upper()


def _probe_server_cert(host: str, port: int, timeout: float = 10.0) -> tuple[str, str]:
    """Das Zertifikat des Servers holen, OHNE ihm etwas zu schicken.

    Der Handshake ohne Verifikation dient nur dem Lesen des Zertifikats —
    ueber diese Verbindung geht danach kein Byte, insbesondere kein Token.
    """
    import socket
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    with socket.create_connection((host, port), timeout=timeout) as sock:
        with ctx.wrap_socket(sock, server_hostname=host) as tls:
            der = tls.getpeercert(binary_form=True)
    if not der:
        raise TlsTrustError(f"{host}:{port} hat kein Zertifikat praesentiert.")
    return ssl.DER_cert_to_PEM_cert(der), _fingerprint(der)


def _pinned_context(pem: str) -> ssl.SSLContext:
    """Kontext, der GENAU das gepinnte Zertifikat akzeptiert — im Handshake.

    ``VERIFY_X509_PARTIAL_CHAIN`` macht das gepinnte Blatt selbst zum
    Vertrauensanker; ohne das Flag wuerde ein von einer internen CA
    ausgestelltes (nicht selbstsigniertes) Zertifikat an der fehlenden
    Kette scheitern. ``check_hostname`` ist aus, weil der Pin die Identitaet
    IST — ein selbstsigniertes Zertifikat traegt oft weder Host noch IP.
    """
    ctx = ssl.create_default_context(cadata=pem)
    ctx.check_hostname = False
    ctx.verify_flags |= ssl.VERIFY_X509_PARTIAL_CHAIN
    return ctx


def _system_handshake_ok(host: str, port: int, timeout: float = 10.0) -> bool:
    """Besteht der Server die normale System-Verifikation (oeffentliche CA)?"""
    import socket
    ctx = _default_ssl_context()
    try:
        with socket.create_connection((host, port), timeout=timeout) as sock:
            with ctx.wrap_socket(sock, server_hostname=host):
                return True
    except (ssl.SSLError, OSError):
        # Not just SSLCertVerificationError: a plain network failure here
        # (offline, DNS hiccup, timeout) must also fall through to the
        # pinned/self-signed path below, not crash ssl_context_for outright.
        return False


def ssl_context_for(url: str, allow_pin_new: bool = True) -> ssl.SSLContext | None:
    """Der richtige SSL-Kontext fuer diese Adresse — oder ein klarer Fehler.

    ``allow_pin_new`` steuert NUR den Erstkontakt (noch kein Pin fuer den
    Host): normale Verbindungen duerfen pinnen (TOFU, wie SSH), stille
    Hintergrundaufrufe koennen es abschalten. Pinning ist die Vertrauensbasis
    NUR fuer selbstsignierte Server (eine Kundenanlage, lokale Boxen) — dort wird ein
    GEAENDERTES Zertifikat nie automatisch akzeptiert, das braucht immer
    ``repin_server`` per ausdruecklicher Nutzeraktion.

    Ein Host, der schon die normale System-CA-Pruefung besteht (oeffentliche
    CA, gueltige Kette), wird IMMER darueber vertraut — auch wenn von
    frueher noch ein Pin fuer denselben Host existiert. Grund: hinter einem
    CDN/Tunnel (z. B. Cloudflare) kann sich das ausgelieferte Blatt-Zertifikat
    aendern, obwohl es weiterhin eine gueltige, CA-signierte Kette ist — das
    ist keine Kompromittierung, das ist normaler CDN-Betrieb. Ein starrer Pin
    hat das faelschlich als "Zertifikat geaendert" behandelt und die Bridge
    dauerhaft mit einem irrefuehrenden "server nicht erreichbar" blockiert
    (gemeldet 2026-08-27, betraf agents.future-app.de). Das Sicherheitsniveau
    sinkt dadurch nicht: eine gueltige CA-Kette ist bereits der Vertrauens-
    anker, den TLS ueberall sonst auch verwendet.
    """
    if not url.lower().startswith(("https://", "wss://")):
        return None

    host, port = _host_port(url)
    cfg = _config_read()
    tls_cfg = cfg.get("tls") if isinstance(cfg.get("tls"), dict) else {}

    if tls_cfg.get("mode") == "insecure":
        log.warning(
            "TLS-Verifikation ist per Konfiguration ABGESCHALTET (tls.mode=insecure). "
            "Jede Verbindung ist gegen Mitleser ungeschuetzt."
        )
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        return ctx

    if _system_handshake_ok(host, port):
        return _default_ssl_context()

    if tls_cfg.get("mode") == "pinned" and tls_cfg.get("host") == host:
        pem = str(tls_cfg.get("cert_pem") or "")
        pinned_fp = str(tls_cfg.get("fingerprint") or "")
        try:
            _, current_fp = _probe_server_cert(host, port)
        except TlsTrustError:
            raise
        except OSError as e:
            raise TlsTrustError(f"{host}:{port} nicht erreichbar: {e}") from e
        if current_fp != pinned_fp:
            raise TlsTrustError(
                f"Das Zertifikat von {host} hat sich GEAENDERT.\n"
                f"  Gepinnt: {format_fingerprint(pinned_fp)}\n"
                f"  Aktuell: {format_fingerprint(current_fp)}\n"
                "Wenn der Server wirklich ein neues Zertifikat bekommen hat: in der "
                "Bridge unter Einstellungen neu anmelden — dabei wird neu vertraut. "
                "Wenn nicht, spricht hier gerade jemand anderes fuer den Server."
            )
        return _pinned_context(pem)

    if not allow_pin_new:
        raise TlsTrustError(
            f"{host} verwendet ein selbstsigniertes Zertifikat und ist noch nicht "
            "gepinnt. Bitte einmal ueber Einstellungen → Anmelden verbinden."
        )
    pem, fp = _probe_server_cert(host, port)
    cfg = _config_read()
    cfg["tls"] = {"mode": "pinned", "host": host, "fingerprint": fp, "cert_pem": pem}
    _config_write(cfg)
    log.warning(
        "Selbstsigniertes Zertifikat von %s beim Erstkontakt gepinnt — "
        "SHA-256 %s. Ab jetzt wird NUR dieses Zertifikat akzeptiert.",
        host, format_fingerprint(fp),
    )
    return _pinned_context(pem)


def repin_server(url: str) -> str:
    """Dem aktuellen Zertifikat des Servers NEU vertrauen — Nutzeraktion noetig.

    Wird ausschliesslich aus der ausdruecklichen Neu-Anmeldung heraus
    aufgerufen (Einstellungen → Anmelden). Gibt den neuen Fingerabdruck
    zurueck, damit die Oberflaeche ihn anzeigen kann.
    """
    host, port = _host_port(url)
    if _system_handshake_ok(host, port):
        cfg = _config_read()
        if isinstance(cfg.get("tls"), dict) and cfg["tls"].get("mode") == "pinned":
            del cfg["tls"]
            _config_write(cfg)
        return ""
    pem, fp = _probe_server_cert(host, port)
    cfg = _config_read()
    cfg["tls"] = {"mode": "pinned", "host": host, "fingerprint": fp, "cert_pem": pem}
    _config_write(cfg)
    log.warning("Zertifikat von %s auf Nutzerwunsch neu gepinnt — SHA-256 %s.",
                host, format_fingerprint(fp))
    return fp


def _check_deps() -> list[str]:
    missing = []
    try:
        import PIL  # noqa
    except ImportError:
        missing.append("Pillow")
    try:
        import pyautogui  # noqa
    except ImportError:
        missing.append("pyautogui")
    if IS_MAC:
        try:
            import AppKit  # noqa
        except ImportError:
            missing.append("pyobjc-framework-Cocoa")
    return missing


# ── Screenshot ───────────────────────────────────────────────────────────────

class ScreenRecordingPermissionError(RuntimeError):
    """Die Bildschirmaufnahme ist nicht freigegeben — ein Screenshot waere wertlos."""


_input_permission_prompted = False


class InputPermissionError(RuntimeError):
    """Die Bedienungshilfen sind nicht freigegeben — Tippen und Klicken tun NICHTS.

    Ohne diese Freigabe schlaegt der Weg ueber System Events mit Fehler 1002 fehl
    ("osascript ist nicht berechtigt, Tastatureingaben zu senden"), und der
    Rueckfall auf pyautogui tut still gar nichts. Bis hierher meldete die Bridge
    in dem Fall trotzdem Erfolg — der Agent sagte "Erledigt", im Browser stand
    nichts, und niemand konnte sich erklaeren warum. Ein Fehlschlag, der sich als
    Erfolg ausgibt, ist schlimmer als ein Fehler.
    """


def input_permission_granted(fragen: bool = False) -> bool | None:
    """Duerfen wir Tasten und Klicks senden? ``None`` = nicht feststellbar.

    Nur macOS kennt diese Huerde (Bedienungshilfen). Unter Windows gibt es keine
    vergleichbare Freigabe — dort sendet die Bridge Eingaben ohne Nachfrage.

    ``fragen=True`` zeigt den macOS-Dialog. Das ist der Unterschied zwischen
    ``AXIsProcessTrusted`` (still pruefen) und
    ``AXIsProcessTrustedWithOptions(prompt)`` (fragen). Bis hierher wurde nur
    geprueft — die App wusste, dass sie nicht darf, und sagte es nur. Wer die
    Freigabe nie erteilt hatte oder sie zurueckgesetzt hat, bekam nie wieder
    eine Gelegenheit dazu.
    """
    if not IS_MAC:
        return True
    try:
        import ApplicationServices as AS  # type: ignore
        if not fragen:
            return bool(AS.AXIsProcessTrusted())
        try:
            return bool(AS.AXIsProcessTrustedWithOptions(
                {AS.kAXTrustedCheckOptionPrompt: True}
            ))
        except Exception:  # noqa: BLE001 — ohne Dialog wenigstens die Auskunft
            return bool(AS.AXIsProcessTrusted())
    except Exception:  # noqa: BLE001 — lieber weitermachen als blockieren
        return None


def _capture_macos_inprocess(display_id=None):
    """Bildschirmaufnahme IM EIGENEN PROZESS via Quartz — oder None.

    `pyautogui.screenshot()` startet auf macOS bei JEDEM Aufruf das Programm
    `screencapture` als eigenen Prozess. Die Freigabe zur Bildschirmaufnahme haengt
    aber an der anfragenden Anwendung: ein kurzlebiger, fremder Prozess bekommt sie
    nicht zuverlaessig zugeordnet, und macOS fragt bei jeder Anfrage erneut — auch
    wenn der Nutzer sie laengst erteilt hat (Meldung 2026-08-04).

    Quartz nimmt innerhalb dieses Prozesses auf. Die Freigabe gilt dann fuer die
    Bridge selbst: einmal erteilt, nie wieder gefragt.
    """
    try:
        from PIL import Image
        from Quartz import (  # type: ignore
            CGDataProviderCopyData, CGDisplayCreateImage, CGImageGetBytesPerRow,
            CGImageGetDataProvider, CGImageGetHeight, CGImageGetWidth, CGMainDisplayID,
        )
    except Exception:  # noqa: BLE001 — kein Quartz: Aufrufer nimmt den alten Weg
        return None
    # macOS beantwortet direkt, ob DIESER Prozess aufnehmen darf. Ohne die Freigabe
    # liefert CGDisplayCreateImage KEINEN Fehler, sondern ein Bild mit Schreibtisch
    # und Menueleiste, aber OHNE Fensterinhalte. Das sieht auf den ersten Blick nach
    # einem gueltigen Screenshot aus und hat schon Menschen wie Modelle getaeuscht
    # ("ein Safari-Fenster mit einem Landschaftsfoto" — das war der Hintergrund).
    # Deshalb hier hart abbrechen statt ein wertloses Bild zurueckzugeben.
    try:
        from Quartz import CGPreflightScreenCaptureAccess  # type: ignore
        if not CGPreflightScreenCaptureAccess():
            # FRAGEN, nicht nur pruefen. Vorher stand hier ausschliesslich der
            # stille Preflight — die App stellte fest, dass sie nicht darf, und
            # sagte es nur. Den macOS-Dialog loest allein
            # CGRequestScreenCaptureAccess aus. Ohne ihn gibt es fuer den Nutzer
            # KEINEN Weg, die Freigabe zu erteilen, ausser die App von Hand in
            # die Liste zu ziehen — und nach einem Zuruecksetzen der Freigaben
            # (oder bei einer Neuinstallation) fragt nie wieder jemand.
            try:
                from Quartz import CGRequestScreenCaptureAccess  # type: ignore
                if CGRequestScreenCaptureAccess():
                    log.info("Bildschirmaufnahme soeben freigegeben.")
                else:
                    raise ScreenRecordingPermissionError(
                        "Der Bridge fehlt die Freigabe zur Bildschirmaufnahme. macOS hat "
                        "gefragt — bitte erlauben. Erscheint kein Dialog: Systemeinstellungen "
                        "→ Datenschutz & Sicherheit → Bildschirmaufnahme, dort "
                        "AI-Employee Bridge hinzufügen. Danach die App komplett beenden "
                        "und neu starten."
                    )
            except ImportError:
                raise ScreenRecordingPermissionError(
                    "Der Bridge fehlt die Freigabe zur Bildschirmaufnahme. Systemeinstellungen "
                    "→ Datenschutz & Sicherheit → Bildschirmaufnahme: AI-Employee Bridge "
                    "aktivieren, danach die App komplett beenden und neu starten."
                )
    except ImportError:
        pass
    try:
        # Ohne Angabe der Hauptbildschirm — mit Angabe genau der gewuenschte.
        cg = CGDisplayCreateImage(display_id if display_id is not None else CGMainDisplayID())
        if cg is None:
            return None
        w, h = CGImageGetWidth(cg), CGImageGetHeight(cg)
        raw = bytes(CGDataProviderCopyData(CGImageGetDataProvider(cg)))
        # macOS liefert BGRA; die Zeilenlaenge ist gepolstert und MUSS mitgegeben
        # werden, sonst verscheert das Bild.
        return Image.frombuffer(
            "RGBA", (w, h), raw, "raw", "BGRA", CGImageGetBytesPerRow(cg), 1
        ).convert("RGB")
    except ScreenRecordingPermissionError:
        raise                       # muss beim Nutzer ankommen, nicht im Rueckfall verschwinden
    except Exception as e:  # noqa: BLE001
        log.warning("Quartz screenshot failed, falling back to pyautogui: %s", e)
        return None


def list_displays() -> list[dict]:
    """Alle angeschlossenen Bildschirme mit Nummer, Lage und Groesse.

    Bis 1.259.x nahm die Bridge ausschliesslich den HAUPTbildschirm auf
    (``CGMainDisplayID``). Wer zwei Monitore hat, konnte dem Agenten den
    zweiten nicht zeigen — und ihn auch nicht darauf schicken. Gemeldet am
    21.08.2026: „bei Screenshot AUCH ALLE ANDEREN Bildschirme … das ich dem
    Agenten sagen kann geh bitte auf Bildschirm 1 oder 2".

    Nummer 1 ist immer der Hauptbildschirm — das ist die Zaehlweise, die ein
    Mensch am Telefon benutzt.
    """
    if sys.platform != "darwin":
        w, h = _logical_screen_size()
        return [{"number": 1, "primary": True, "width": w, "height": h, "x": 0, "y": 0}]
    try:
        from Quartz import (  # type: ignore
            CGDisplayBounds, CGGetActiveDisplayList, CGMainDisplayID,
        )
    except Exception:  # noqa: BLE001
        w, h = _logical_screen_size()
        return [{"number": 1, "primary": True, "width": w, "height": h, "x": 0, "y": 0}]

    try:
        fehler, kennungen, _anzahl = CGGetActiveDisplayList(16, None, None)
        if fehler or not kennungen:
            raise RuntimeError("keine Bildschirme gemeldet")
        haupt = CGMainDisplayID()
        # Der Hauptbildschirm zuerst — er ist Nummer 1.
        sortiert = sorted(kennungen, key=lambda d: (d != haupt, d))
        aus = []
        for i, kennung in enumerate(sortiert, start=1):
            b = CGDisplayBounds(kennung)
            aus.append({
                "number": i,
                "id": int(kennung),
                "primary": bool(kennung == haupt),
                "x": int(b.origin.x), "y": int(b.origin.y),
                "width": int(b.size.width), "height": int(b.size.height),
            })
        return aus
    except Exception as e:  # noqa: BLE001 — lieber ein Bildschirm als gar keiner
        log.warning("Bildschirmliste nicht ermittelbar: %s", e)
        w, h = _logical_screen_size()
        return [{"number": 1, "primary": True, "width": w, "height": h, "x": 0, "y": 0}]


def _logical_screen_size() -> tuple[int, int]:
    """Bildschirmgroesse in LOGISCHEN Punkten — der Raum, in dem geklickt wird.

    pyautogui setzt Klicks in logischen Punkten (auf Retina also z.B. 1440,
    nicht 2880 physische Pixel). Genau in diesem Raum muss eine Klickkoordinate
    am Ende liegen.
    """
    try:
        import pyautogui
        w, h = pyautogui.size()
        return int(w), int(h)
    except Exception:  # noqa: BLE001 — ohne Auskunft lieber 1:1 als falsch skalieren
        return 0, 0


def capture_screenshot(scale: float = 1.0, display: int | None = None) -> tuple[str, dict]:
    """Screenshot als base64-PNG PLUS der Maszstab Bild→Bildschirm.

    Der Kern des Retina-Klickproblems: Das Bild wird auf 1280px Breite
    herunterskaliert (damit das Modell keine Koordinaten >1280 halluziniert),
    aber ein Klick geht an pyautogui, das in LOGISCHEN Punkten (z.B. 1440)
    arbeitet. Das Modell sieht also ein 1280er Bild, nennt eine Koordinate
    darin — und der Klick landet systematisch um den Faktor logisch/1280
    daneben. Beim Nutzer sichtbar: „Klicks landen nicht, wo ich sie hinsetze",
    der Agent musste auf Cmd+L/URL ausweichen.

    Deshalb liefert diese Funktion den Umrechnungsfaktor gleich mit. Der
    Dispatcher merkt ihn sich und rechnet jede Klick-/Bewegungs-/Scroll-
    Koordinate aus dem BILDRAUM zurueck in den KLICKRAUM. ``scale_*`` ist
    logisch/Bild: bei einem 1280er Bild und 1440 logischer Breite also 1.125.
    """
    from PIL import Image

    bildschirme = list_displays()
    gewaehlt = None
    if display:
        gewaehlt = next((b for b in bildschirme if b["number"] == int(display)), None)
        if gewaehlt is None:
            raise ValueError(
                f"Bildschirm {display} gibt es nicht — verfuegbar sind "
                f"1 bis {len(bildschirme)}."
            )

    img = (
        _capture_macos_inprocess(gewaehlt.get("id") if gewaehlt else None)
        if sys.platform == "darwin" else None
    )
    if img is None:
        import pyautogui
        img = pyautogui.screenshot()

    # Scale down large Retina screenshots (Claude hallucinates coordinates >1280px)
    max_width = 1280
    if img.width > max_width or scale != 1.0:
        ratio = min(max_width / img.width, scale) if scale == 1.0 else scale
        new_size = (int(img.width * ratio), int(img.height * ratio))
        img = img.resize(new_size, Image.LANCZOS)

    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    b64 = base64.b64encode(buf.getvalue()).decode()

    if gewaehlt:
        # Bei einem Nebenbildschirm gilt SEINE Groesse, nicht die des Haupts.
        logical_w, logical_h = gewaehlt["width"], gewaehlt["height"]
    else:
        logical_w, logical_h = _logical_screen_size()
    # Ohne verlaessliche Bildschirmgroesse NICHT skalieren (1.0) — ein falscher
    # Faktor waere schlimmer als gar keiner.
    scale_x = (logical_w / img.width) if (logical_w and img.width) else 1.0
    scale_y = (logical_h / img.height) if (logical_h and img.height) else 1.0
    meta = {
        "image_w": img.width, "image_h": img.height,
        "logical_w": logical_w, "logical_h": logical_h,
        "scale_x": scale_x, "scale_y": scale_y,
        "display": (gewaehlt or next((b for b in bildschirme if b["primary"]), bildschirme[0]))["number"],
        "displays": bildschirme,
    }
    return b64, meta


def take_screenshot(scale: float = 1.0) -> str:
    """Nur das Bild (base64-PNG) — fuer Aufrufer ohne Koordinatenbedarf."""
    return capture_screenshot(scale)[0]


class InputRecorder:
    """Capture what the HUMAN does (clicks + typed text) to demonstrate a workflow.

    Replay-Modus has two recording sources: the agent's own tool calls (recorded
    server-side) and this one — a person doing the task once by hand so the agent
    can learn it. Events are buffered here and drained by the WebSocket loop.

    PRIVACY: while active this observes EVERY click and EVERY keystroke on the
    machine, including other windows and anything typed into a password field.
    It therefore only runs between an explicit start/stop, logs loudly at both
    ends, and never writes to disk — events go straight out to the session that
    asked for them. Typed text is flushed as whole strings on Enter/Tab/click,
    so the transcript reads as "typed X into the field" rather than a raw
    keylogger stream.
    """

    def __init__(self, emit) -> None:
        self._emit = emit          # called with one event dict per captured step
        self._listeners: list = []
        self._text_buffer: list[str] = []
        # Maus- und Tastatur-Listener laufen auf ZWEI verschiedenen
        # pynput-Threads: ein Klick (flush) und ein Tastendruck (append)
        # koennen denselben Puffer gleichzeitig anfassen — dann verliert
        # der Mitschnitt Zeichen oder mischt zwei Eingaben.
        self._buffer_lock = threading.Lock()
        self.active = False

    def _flush_text(self) -> None:
        with self._buffer_lock:
            if not self._text_buffer:
                return
            text = "".join(self._text_buffer)
            self._text_buffer.clear()
        if text.strip():
            self._push("type", {"text": text})

    def _push(self, action: str, params: dict) -> None:
        try:
            shot = take_screenshot(0.5)
        except Exception:  # noqa: BLE001 — a failed screenshot must not kill capture
            shot = None
        self._emit({
            "action": action,
            "params": params,
            "ts": time.time(),
            "screenshot_b64": shot,
            "source": "human",
        })

    def _on_click(self, x, y, button, pressed) -> None:
        if not pressed:
            return
        self._flush_text()  # a click ends whatever was being typed
        self._push("click", {"x": int(x), "y": int(y), "button": str(button).split(".")[-1]})

    def _on_key(self, key) -> None:
        from pynput import keyboard

        if key in (keyboard.Key.enter, keyboard.Key.tab):
            self._flush_text()
            self._push("key", {"keys": [str(key).split(".")[-1]]})
            return
        if key == keyboard.Key.backspace:
            with self._buffer_lock:
                if self._text_buffer:
                    self._text_buffer.pop()
            return
        char = getattr(key, "char", None)
        if char is not None:
            with self._buffer_lock:
                self._text_buffer.append(char)
        elif key == keyboard.Key.space:
            with self._buffer_lock:
                self._text_buffer.append(" ")

    def start(self) -> dict:
        if self.active:
            return {"ok": True, "already_active": True}
        try:
            from pynput import keyboard, mouse
        except ImportError:
            return {
                "ok": False,
                "error": "pynput is not installed — human input capture unavailable. "
                         "Install it with: pip install pynput",
            }
        log.warning(
            "INPUT CAPTURE STARTED — every click and keystroke on this machine is being "
            "recorded until you stop it. Do not type passwords while this runs."
        )
        with self._buffer_lock:
            self._text_buffer.clear()
        m = mouse.Listener(on_click=self._on_click)
        k = keyboard.Listener(on_press=self._on_key)
        m.start()
        k.start()
        self._listeners = [m, k]
        self.active = True
        return {"ok": True}

    def stop(self) -> dict:
        if not self.active:
            return {"ok": True, "already_stopped": True}
        self._flush_text()
        for listener in self._listeners:
            try:
                listener.stop()
            except Exception:  # noqa: BLE001
                pass
        self._listeners.clear()
        self.active = False
        log.warning("INPUT CAPTURE STOPPED — no longer recording clicks/keystrokes.")
        return {"ok": True}


class VoiceCapture:
    """Capture microphone audio from the human at the keyboard, chunked and
    streamed upstream — the desktop counterpart to the browser's getUserMedia()
    capture (issue #478 phase 1). Server-side wiring (feeding chunks into a
    RealtimeVoiceSession) is a later phase; here the bridge only records and
    forwards, same lifecycle discipline as InputRecorder: only between an
    explicit start/stop, never buffered to disk, logs loudly at both ends.
    """

    SAMPLE_RATE = 16000  # matches what the STT providers already expect
    CHANNELS = 1
    CHUNK_MS = 100  # small enough for low latency, large enough not to flood the WS

    def __init__(self, emit, on_active_change=None) -> None:
        self._emit = emit          # called with one {chunk_b64, ...} dict per chunk
        # Optional GUI callback: on_active_change(True|False) — lets a tray icon
        # show a mic-status indicator (issue #478 phase 3) without polling.
        self._on_active_change = on_active_change
        self._stream = None
        self.active = False

    def _on_audio(self, indata, frames, time_info, status) -> None:
        if status:
            log.debug("Voice capture status: %s", status)
        import numpy as np

        pcm16 = (indata[:, 0] * 32767.0).astype(np.int16).tobytes()
        self._emit({
            "chunk_b64": base64.b64encode(pcm16).decode("ascii"),
            "sample_rate": self.SAMPLE_RATE,
            "channels": self.CHANNELS,
            "ts": time.time(),
        })

    def start(self) -> dict:
        if self.active:
            return {"ok": True, "already_active": True}
        try:
            import sounddevice as sd
        except ImportError:
            return {
                "ok": False,
                "error": "sounddevice is not installed — voice capture unavailable. "
                         "Install it with: pip install sounddevice",
            }
        try:
            stream = sd.InputStream(
                samplerate=self.SAMPLE_RATE,
                channels=self.CHANNELS,
                blocksize=int(self.SAMPLE_RATE * self.CHUNK_MS / 1000),
                callback=self._on_audio,
            )
            stream.start()
        except Exception as e:  # noqa: BLE001 — no mic, permission denied, device busy, ...
            return {"ok": False, "error": f"Mikrofon liess sich nicht oeffnen: {e}"}
        self._stream = stream
        log.warning(
            "VOICE CAPTURE STARTED — microphone audio is being streamed until you stop it."
        )
        self.active = True
        self._notify_active_change()
        return {"ok": True}

    def stop(self) -> dict:
        if not self.active:
            return {"ok": True, "already_stopped": True}
        if self._stream is not None:
            try:
                self._stream.stop()
                self._stream.close()
            except Exception:  # noqa: BLE001
                pass
            self._stream = None
        self.active = False
        log.warning("VOICE CAPTURE STOPPED — no longer streaming microphone audio.")
        self._notify_active_change()
        return {"ok": True}

    def _notify_active_change(self) -> None:
        if self._on_active_change is None:
            return
        try:
            self._on_active_change(self.active)
        except Exception:  # noqa: BLE001 — a broken GUI callback must not break capture
            log.debug("voice capture on_active_change callback failed", exc_info=True)


def _applescript_string_literal(value: str) -> str:
    """Escape a value for safe interpolation into a double-quoted AppleScript
    string literal. Without this, an app name containing '"' can break out of
    the literal and inject arbitrary AppleScript (e.g. `do shell script ...`)."""
    return value.replace("\\", "\\\\").replace('"', '\\"')


def _keystroke_script(text: str) -> str:
    """AppleScript, das ``text`` tippt — Zeilenumbrueche als Return-Taste.

    Ein rohes ``\\n`` INNERHALB eines AppleScript-Literals ist ein Syntaxfehler:
    mehrzeiliger Text (jede E-Mail, jedes Formular mit Textfeld) liess
    ``osascript`` scheitern, und der stille Rueckfall auf pyautogui tippte dann
    layout-falsch weiter (`-` wird `ß`). Deshalb wird der Text an den
    Umbruechen zerlegt und dazwischen ``keystroke return`` gesendet.
    """
    lines: list[str] = []
    parts = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    for i, part in enumerate(parts):
        if part:
            lines.append(f'keystroke "{_applescript_string_literal(part)}"')
        if i < len(parts) - 1:
            lines.append("keystroke return")
    if not lines:
        return ""
    return 'tell application "System Events"\n' + "\n".join(lines) + "\nend tell"


# ── Bedienungshilfen-Baum (macOS AXUIElement / Windows UI Automation) ─────────
#
# EINE Baumform fuer beide Systeme: {role, title, label, value, bbox, children}.
# Nur der Erzeuger ist plattformabhaengig — `find_element` und `wait_for_element`
# arbeiten unveraendert weiter, egal woher der Baum kommt. Ohne das haette Windows
# einen zweiten Suchpfad gebraucht, und jede Verbesserung waere nur auf einer
# Plattform angekommen.


def _win_ui_tree(app_name: str | None = None, max_depth: int = 6) -> dict:
    """Windows-Gegenstueck zum AX-Baum, ueber UI Automation.

    Ohne das konnte die Bridge unter Windows nur klicken, wohin jemand zeigte —
    Elemente FINDEN ging nicht, also war jede Bedienung innerhalb einer Anwendung
    ein Blindflug ueber geratene Koordinaten.
    """
    try:
        import uiautomation as auto  # type: ignore
    except ImportError:
        return {"error": (
            "Windows-Bedienungshilfen fehlen. Installiere sie einmalig mit "
            "'pip install uiautomation', dann kann ich Elemente auch unter Windows finden."
        )}

    def _to_node(ctrl, depth: int) -> dict | None:
        if depth <= 0:
            return None
        try:
            node: dict[str, Any] = {"role": ctrl.ControlTypeName or ""}
            name = (ctrl.Name or "").strip()
            if name:
                node["title"] = name
            help_text = ""
            try:
                help_text = (ctrl.HelpText or "").strip()
            except Exception:
                pass
            aid = ""
            try:
                aid = (ctrl.AutomationId or "").strip()
            except Exception:
                pass
            label = help_text or aid
            if label:
                node["label"] = label
            try:
                pattern = ctrl.GetValuePattern()
                if pattern is not None and pattern.Value:
                    node["value"] = str(pattern.Value)[:200]
            except Exception:
                pass
            try:
                r = ctrl.BoundingRectangle
                if r and (r.right - r.left) > 0 and (r.bottom - r.top) > 0:
                    node["bbox"] = {"x": r.left, "y": r.top,
                                    "w": r.right - r.left, "h": r.bottom - r.top}
            except Exception:
                pass
            children = []
            try:
                for child in ctrl.GetChildren():
                    child_node = _to_node(child, depth - 1)
                    if child_node:
                        children.append(child_node)
            except Exception:
                pass
            if children:
                node["children"] = children
            return node
        except Exception:
            return None

    try:
        root = auto.GetRootControl()
        if app_name:
            wanted = app_name.strip().lower()
            for window in root.GetChildren():
                title = (getattr(window, "Name", "") or "").lower()
                proc = ""
                try:
                    proc = (window.ProcessId and auto.ProcessIdToProcessName(window.ProcessId) or "").lower()
                except Exception:
                    pass
                if wanted in title or (proc and wanted in proc):
                    return _to_node(window, max_depth) or {}
            return {"error": f"Kein Fenster gefunden, das zu '{app_name}' passt."}
        return _to_node(root, max_depth) or {}
    except Exception as e:  # noqa: BLE001 — ein kaputter Baum darf die Bridge nicht killen
        return {"error": str(e)}


BROWSER_PROFILE_DIR = os.path.join(
    os.path.expanduser("~"), ".ai-employee", "browser-profile"
)

# Was diese Bridge kann — EINE Liste, die beim Verbinden gemeldet wird.
#
# Sie stand frueher als zweite, fest getippte Aufzaehlung mitten im Verbindungs-
# aufbau. Beim Ergaenzen neuer Aktionen wurde sie prompt vergessen: Fenster- und
# Browser-Steuerung liefen zwar, wurden dem Server aber nie gemeldet — die
# Oberflaeche und alles, was "was kann dieser Rechner" fragt, sah sie nicht.
# `test_bridge_announces_what_it_can_do.py` haelt sie mit dem Dispatcher
# zusammen, damit das nicht noch einmal auseinanderlaeuft.
BASE_ACTIONS = [
    "screenshot", "list_displays", "click", "type", "key", "hotkey", "scroll", "move", "drag",
    "open_app", "open_url", "close_app", "list_windows", "focus_window",
    "get_clipboard", "set_clipboard",
    "start_input_capture", "stop_input_capture",
    "start_voice_capture", "stop_voice_capture",
    "shell_run",
    "browser_navigate", "browser_snapshot", "browser_click", "browser_fill",
    "browser_wait", "browser_capture", "browser_tabs", "browser_close",
    # ego lite: lokale JS-Automation gegen die ECHTE, eingeloggte Browsersitzung
    # des Nutzers (siehe EgoBrowser-Abschnitt unten). Wie `browser_*` immer
    # angekuendigt, auch ohne installiertes ego lite — der Fehler kommt dann
    # erst beim Aufruf, mit klarer Meldung statt stiller Zusage.
    "ego_run",
    # Diskrete Gegenstuecke zu browser_* — dieselbe echte Sitzung, aber ohne
    # dass ein Modell selbst JS schreiben muss.
    "ego_navigate", "ego_snapshot", "ego_click", "ego_fill", "ego_wait",
    "ego_capture", "ego_tabs", "ego_close",
]

# Nur wenn der Bedienungshilfen-Baum ueberhaupt verfuegbar ist — ohne ihn waere
# eine Elementsuche eine Zusage, die die Bridge nicht halten kann.
AX_ACTIONS = ["ax_tree", "find_element", "wait_for_element"]


class BrowserController:
    """Der Browser, den der Agent bedienen darf — im EIGENEN Profil.

    Warum ein eigenes Profil und nicht das des Nutzers: Seit Chrome/Edge 136
    wird ``--remote-debugging-port`` auf dem STANDARD-Profil ignoriert (Haertung
    gegen Cookie-/Passwort-Diebstahl). Fernsteuerung geht nur noch mit einem
    eigenen ``user_data_dir``. Das ist kein Umweg, sondern der vorgesehene Weg —
    und er hat einen zweiten Vorteil: man sieht jederzeit, mit welchen Konten
    der Agent unterwegs ist. Cookies aus dem echten Profil zu kopieren waere
    genau das, wogegen die Haertung gebaut wurde; das tun wir bewusst nicht.
    Der Mensch meldet sich einmal an, danach bleibt die Anmeldung im Profil.

    Warum ein eigener Thread: ``dispatch`` laeuft ueber
    ``run_in_executor(None, ...)`` auf WECHSELNDEN Threads des Standard-Pools.
    Playwrights Sync-API ist aber an den Thread gebunden, der sie erzeugt hat —
    ein Klick vom falschen Thread wirft "greenlet" -Fehler. Deshalb besitzt der
    Browser hier einen festen Thread, und alle Befehle laufen als Auftraege
    durch eine Queue.
    """

    def __init__(self, profile_dir: str = BROWSER_PROFILE_DIR):
        self.profile_dir = profile_dir
        self._queue: "queue.Queue[tuple]" = queue.Queue()
        self._thread: threading.Thread | None = None
        self._started = threading.Event()
        self._start_error: str = ""
        self._ctx = None
        self._pw = None

    # ── Lebenszyklus ─────────────────────────────────────────────────────────
    def _prepare_profile_dir(self) -> None:
        """Profil-Verzeichnis anlegen — NUR fuer den angemeldeten Nutzer lesbar.

        Dort liegen nach der Einmal-Anmeldung die Sitzungs-Cookies und
        Anmeldedaten des Browsers. Mit der Vorgabe von ``makedirs`` (0755 nach
        umask) koennte jeder andere lokale Account sie mitlesen — genau der
        Diebstahl, gegen den die Chrome/Edge-136-Haertung gebaut wurde, nur eine
        Ebene tiefer. Damit waere die Begruendung des ganzen Entwurfs ("kein
        Cookie-Import aus dem privaten Profil") hinfaellig.

        ``mode=`` allein genuegt nicht: es wird von der umask beschnitten, und
        bei ``exist_ok=True`` behaelt ein bereits vorhandenes Verzeichnis seine
        alten, moeglicherweise weiten Rechte. Deshalb zusaetzlich ``chmod``.
        """
        os.makedirs(self.profile_dir, mode=0o700, exist_ok=True)
        try:
            os.chmod(self.profile_dir, 0o700)
        except OSError:
            # Windows kennt keine POSIX-Rechte; dort erbt das Verzeichnis die
            # ACL des Benutzerprofils und ist ohnehin nicht fuer andere lesbar.
            pass

    def _run(self) -> None:
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            self._start_error = (
                "Playwright fehlt in der Bridge. Einmalig 'pip install playwright' "
                "ausfuehren; der Browser selbst wird nicht mitgeliefert, es wird das "
                "installierte Edge/Chrome genutzt."
            )
            self._started.set()
            return

        self._prepare_profile_dir()
        # „Im Hintergrund" wie der ego-lite-Browser: headless startet den
        # Browser OHNE sichtbares Fenster — der Agent steuert ihn ueber den
        # DOM/CDP, ohne dass ein Fenster den Vordergrund des Nutzers kapert.
        # Opt-in ueber ~/.ai_employee_bridge.json ("browser_headless": true),
        # gesetzt vom Berechtigungs-Dialog. Standard bleibt SICHTBAR: eine
        # eingeloggte Sitzung unsichtbar laufen zu lassen, soll eine bewusste
        # Entscheidung sein, kein Automatismus.
        headless = bool(_config_read().get("browser_headless", False))
        try:
            self._pw = sync_playwright().start()
            # Reihenfolge mit Absicht: erst der im Haus freigegebene Browser,
            # dann Chrome, erst zuletzt das mitgelieferte Chromium. Ein Klinik-
            # Arbeitsplatz soll den Browser fahren, den die IT freigegeben hat.
            last_err: Exception | None = None
            for channel in ("msedge", "chrome", None):
                try:
                    kwargs = {"user_data_dir": self.profile_dir, "headless": headless}
                    if channel:
                        kwargs["channel"] = channel
                    self._ctx = self._pw.chromium.launch_persistent_context(**kwargs)
                    break
                except Exception as e:  # noqa: BLE001 — naechsten Kanal versuchen
                    last_err = e
            if self._ctx is None:
                self._start_error = f"Kein Browser startbar: {last_err}"
                self._started.set()
                return
            log.info("Agent-Browser gestartet (%s).",
                     "unsichtbar/headless" if headless else "sichtbares Fenster")
        except Exception as e:  # noqa: BLE001
            self._start_error = f"Browser-Start fehlgeschlagen: {e}"
            self._started.set()
            return

        self._started.set()
        while True:
            job, box = self._queue.get()
            if job is None:          # Beenden
                break
            try:
                box.append(("ok", job(self._page())))
            except Exception as e:   # noqa: BLE001 — Fehler gehoert zum Agenten, nicht ins Log-Nirwana
                box.append(("err", str(e)))
        try:
            if self._ctx:
                self._ctx.close()
            if self._pw:
                self._pw.stop()
        except Exception:  # noqa: BLE001
            pass

    def _page(self):
        """Die aktive Seite — eine leere wird bei Bedarf angelegt."""
        if not self._ctx.pages:
            return self._ctx.new_page()
        return self._ctx.pages[-1]

    def _ensure_started(self) -> str:
        if self._thread is None:
            self._thread = threading.Thread(target=self._run, name="bridge-browser", daemon=True)
            self._thread.start()
            # Beim Beenden der App den Browser mitnehmen — sonst haelt ein
            # verwaister Chrome/Edge die Profil-Sperrdatei, und der naechste
            # Start scheitert mit "Kein Browser startbar".
            import atexit
            atexit.register(self.close)
        # Der Rueckgabewert von wait() ist die einzige Auskunft darueber, ob der
        # Start ueberhaupt fertig wurde. Wurde er ignoriert, war `_start_error`
        # noch leer, der Auftrag ging an einen halb gestarteten Browser und der
        # Nutzer bekam eine Minute spaeter das irrefuehrende "Browser antwortet
        # nicht" statt "Browser startet noch".
        if not self._started.wait(timeout=90):
            return ("Der Browser braucht ungewöhnlich lange zum Starten (über 90s) — "
                    "eventuell prüft ein Virenscanner ihn gerade. Bitte gleich nochmal "
                    "versuchen.")
        return self._start_error

    def _call(self, job, timeout: float = 60.0) -> dict:
        err = self._ensure_started()
        if err:
            return {"ok": False, "error": err}
        box: list = []
        self._queue.put((job, box))
        waited = 0.0
        while not box and waited < timeout:
            time.sleep(0.05)
            waited += 0.05
        if not box:
            return {"ok": False, "error": f"Browser antwortet nicht (>{int(timeout)}s)"}
        kind, value = box[0]
        return value if kind == "ok" else {"ok": False, "error": value}

    def close(self) -> dict:
        """Browser beenden — und ehrlich melden, ob es geklappt hat.

        Vorher wurde hier bedingungslos ``{"ok": True}`` gemeldet und der Zustand
        zurueckgesetzt, auch wenn der Thread nach zehn Sekunden noch lief. Der
        alte Browser hielt dann weiter die Sperrdatei auf dem Profilverzeichnis,
        und der naechste ``browser_navigate`` scheiterte mit "Kein Browser
        startbar" — nach einem ``close``, das Erfolg gemeldet hatte.
        """
        if self._thread is None:
            return {"ok": True}
        self._queue.put((None, []))
        self._thread.join(timeout=10)
        if self._thread.is_alive():
            # Zustand NICHT zuruecksetzen: der Thread haelt Kontext und Profil
            # weiter. Ein neuer Start gegen dasselbe Profil wuerde scheitern.
            return {"ok": False, "error": (
                "Der Browser liess sich nicht beenden (läuft noch). Bitte das "
                "Browserfenster von Hand schliessen."
            )}
        self._thread = None
        self._started.clear()
        self._ctx = None
        self._pw = None
        return {"ok": True}

    # ── Aktionen ─────────────────────────────────────────────────────────────
    # Die Adress-Freigabe wird SERVERSEITIG geprueft (computer_use.py), bevor der
    # Befehl hier ankommt — hier steht keine zweite, abweichende Wahrheit.

    def navigate(self, url: str, timeout_ms: int = 30000) -> dict:
        def job(page):
            page.goto(url, timeout=timeout_ms, wait_until="domcontentloaded")
            return {"ok": True, "url": page.url, "title": page.title()}
        return self._call(job)

    def snapshot(self, max_chars: int = 20000) -> dict:
        """Struktur statt Pixel: der Bedienungshilfen-Baum der Seite.

        ``page.accessibility`` gab es frueher, ist in aktuellen Playwright-
        Fassungen aber entfernt (1.62 kennt es nicht mehr). Der heutige Weg ist
        ``locator.aria_snapshot()``; der alte bleibt als Rueckfall stehen, damit
        eine aeltere Installation nicht bricht.
        """
        def job(page):
            text = ""
            body = page.locator("body")
            if hasattr(body, "aria_snapshot"):
                text = body.aria_snapshot()
            elif hasattr(page, "accessibility"):
                text = json.dumps(page.accessibility.snapshot() or {}, ensure_ascii=False)
            else:
                return {"ok": False, "error": (
                    "Diese Playwright-Fassung kann keinen Seitenbaum liefern — "
                    "bitte 'pip install -U playwright' ausfuehren."
                )}
            truncated = len(text) > max_chars
            return {"ok": True, "url": page.url, "title": page.title(),
                    "snapshot": text[:max_chars], "truncated": truncated}
        return self._call(job)

    def click(self, selector: str = "", text: str = "") -> dict:
        def job(page):
            target = page.get_by_text(text, exact=False).first if text and not selector \
                else page.locator(selector).first
            target.click(timeout=15000)
            return {"ok": True, "clicked": selector or text, "url": page.url}
        return self._call(job)

    def fill(self, selector: str, value: str) -> dict:
        def job(page):
            page.locator(selector).first.fill(value, timeout=15000)
            return {"ok": True, "filled": selector}
        return self._call(job)

    def wait(self, selector: str = "", timeout_ms: int = 15000) -> dict:
        def job(page):
            if selector:
                page.locator(selector).first.wait_for(state="visible", timeout=timeout_ms)
            else:
                page.wait_for_load_state("networkidle", timeout=timeout_ms)
            return {"ok": True, "url": page.url}
        return self._call(job, timeout=timeout_ms / 1000 + 15)

    def capture(self, full_page: bool = False) -> dict:
        def job(page):
            png = page.screenshot(full_page=full_page)
            return {"ok": True, "url": page.url,
                    "screenshot_b64": base64.b64encode(png).decode()}
        return self._call(job)

    def tabs(self, index: int | None = None) -> dict:
        def job(page):
            pages = self._ctx.pages
            if index is not None:
                if not 0 <= index < len(pages):
                    return {"ok": False, "error": f"Kein Tab mit Nummer {index}"}
                pages[index].bring_to_front()
                return {"ok": True, "active": index, "url": pages[index].url}
            return {"ok": True, "tabs": [
                {"index": i, "url": p.url, "title": p.title()} for i, p in enumerate(pages)
            ]}
        return self._call(job)


def _ego_browser_binary() -> str | None:
    """Pfad zum lokalen ``ego-browser``-CLI — oder ``None``, wenn ego lite auf
    diesem Rechner nicht installiert bzw. nicht eingerichtet ist.

    Das CLI wird beim Onboarding der ego-lite-App unter ``~/.local/bin/ego-browser``
    registriert (siehe ``~/.claude/skills/ego-browser/references/install.md``).
    Als Tray-/LaunchAgent-Prozess gestartet hat die Bridge dieses Verzeichnis oft
    NICHT im PATH — deshalb zusaetzlich der feste Pfad, nicht nur ``shutil.which``.
    """
    import shutil
    found = shutil.which("ego-browser")
    if found:
        return found
    candidate = os.path.join(os.path.expanduser("~"), ".local", "bin", "ego-browser")
    if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
        return candidate
    return None


def ego_browser_available() -> bool:
    return _ego_browser_binary() is not None


def ego_run(script: str, timeout: int = 120) -> dict:
    """Ein JS-Snippet an die lokale ego-lite-Automation schicken.

    ego lite arbeitet BEWUSST in der echten, eingeloggten Sitzung des Nutzers —
    Task Spaces erben den Login-Status, anders als ``BrowserController`` oben,
    der bewusst ein eigenes, isoliertes Profil haelt (siehe dessen Docstring).
    Das ist hier der ganze Zweck: der Agent soll ohne erneute Anmeldung an
    bereits eingeloggten Konten arbeiten koennen. Die Kehrseite steht
    serverseitig in ``computer_use.py``: die Faehigkeitsgruppe ``ego_browser``
    ist wie ``shell`` standardmaessig AUS, der Nutzer schaltet sie bewusst frei
    — und anders als ``browser_navigate`` gibt es HIER keine feingranulare
    Adress-Freigabe: ein JS-Skript ist kein einzelnes Ziel, das sich vorab
    pruefen liesse. Die Capability-Gruppe ist die einzige Schranke.

    Ausfuehrung ueber ``ego-browser nodejs`` (dasselbe CLI, das die
    ``ego-browser``-Skill lokal fuer Claude Code nutzt) — das Skript geht per
    stdin rein, die komplette ``cliLog()``-Ausgabe kommt per stdout zurueck.
    """
    import subprocess
    binary = _ego_browser_binary()
    if not binary:
        return {"ok": False, "error": (
            "ego lite ist auf diesem Rechner nicht gefunden (weder auf dem PATH "
            "noch unter ~/.local/bin/ego-browser). Installation: https://lite.ego.app/"
        )}
    if not script.strip():
        return {"ok": False, "error": "Kein Skript uebergeben."}
    try:
        result = subprocess.run(
            [binary, "nodejs"],
            input=script,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": f"ego-browser antwortet nicht (>{timeout}s)"}
    except OSError as e:
        return {"ok": False, "error": f"ego-browser liess sich nicht starten: {e}"}
    if result.returncode != 0:
        return {"ok": False, "error": (
            result.stderr.strip() or result.stdout.strip()
            or f"ego-browser beendete sich mit Code {result.returncode}"
        )}
    # cliLog() schreibt nachweislich auf stderr, nicht stdout (leer bei jedem
    # lokalen Test) — vermutlich reserviert das CLI stdout fuer ein eigenes
    # Protokoll. stdout wird trotzdem mitgenommen, falls eine kuenftige
    # Fassung dort etwas ausgibt.
    output = result.stderr
    if result.stdout:
        output = (output + "\n" + result.stdout) if output else result.stdout
    _ego_bring_to_front()
    return {"ok": True, "output": output}


def _ego_bring_to_front() -> None:
    """ego lite ins Vordergrund holen — live beim Nutzer gefunden: startet die
    ``ego-browser``-CLI die App selbst (siehe ``_ego_browser_binary``-Docstring
    oben), laeuft sie im HINTERGRUND als ``--startup-ego-browser-service``, ganz
    ohne sichtbares Fenster im Vordergrund. Automatisierung lief korrekt (echte
    Tabs, echte Suchergebnisse), der Nutzer sah davon aber nichts und hielt es
    fuer kaputt. Best-effort: ein fehlgeschlagener Vordergrund-Wechsel darf das
    eigentliche Ergebnis nicht kaputt machen, deshalb wird hier nichts geworfen.
    """
    if not IS_MAC:
        return
    import subprocess
    try:
        subprocess.run(["open", "-a", "ego lite"], capture_output=True, timeout=5)
    except Exception:  # noqa: BLE001 — rein kosmetisch, nie den Aufruf sprengen
        pass


# ── Diskrete ego-Aktionen (wie browser_*, aber gegen die echte Sitzung) ───────
#
# ego_run oben bleibt fuer freie/zusammengesetzte Skripte. Diese Aktionen sind
# die gaengigsten Einzelschritte als eigene, typisierte Aktion — wie
# browser_navigate/-click/-fill/... — damit ein Modell (auch die Sprachfront)
# sie ohne eigenes JS aufrufen kann. Jede baut EIN kleines Skript, das sein
# Ergebnis als JSON-Zeile ueber cliLog() zurueckgibt, und parst das hier.
#
# Alle teilen sich denselben Task-Space-Namen ("bridge"), damit navigate →
# click → fill → snapshot in AUFEINANDERFOLGENDEN aufrufen dieselbe Sitzung
# und denselben Tab weiterbenutzen (jeder Aufruf ist ein eigener Prozess -
# ohne denselben Namen wuerde jede Aktion einen neuen Tab aufmachen).
_EGO_TASK_SPACE = "bridge"


def _ego_script_json(script: str, timeout: int = 60) -> dict:
    """Wie ego_run, erwartet aber die letzte cliLog()-Zeile als EIN JSON-Objekt."""
    result = ego_run(script, timeout)
    if not result.get("ok"):
        return result
    lines = [ln for ln in (result.get("output") or "").splitlines() if ln.strip()]
    if not lines:
        return {"ok": False, "error": "ego lite hat keine Ausgabe geliefert."}
    try:
        return json.loads(lines[-1])
    except ValueError:
        return {"ok": False, "error": f"Unerwartete Ausgabe von ego lite: {lines[-1][:300]}"}


def ego_navigate(url: str, timeout_ms: int = 30000) -> dict:
    secs = max(1, timeout_ms // 1000)
    script = (
        f"const t = await useOrCreateTaskSpace({json.dumps(_EGO_TASK_SPACE)});"
        f"await openOrReuseTab({json.dumps(url)}, {{wait:true, timeout:{secs}}});"
        "const info = await pageInfo();"
        "cliLog(JSON.stringify({ok:true, url:info.url, title:info.title}));"
    )
    return _ego_script_json(script, timeout=secs + 20)


def ego_snapshot(max_chars: int = 20000) -> dict:
    script = (
        f"const t = await useOrCreateTaskSpace({json.dumps(_EGO_TASK_SPACE)});"
        "const text = await snapshotText();"
        "const info = await pageInfo();"
        f"const max = {int(max_chars)};"
        "cliLog(JSON.stringify({ok:true, url:info.url, title:info.title, "
        "snapshot: text.slice(0, max), truncated: text.length > max}));"
    )
    return _ego_script_json(script, timeout=45)


def ego_click(selector: str = "", text: str = "") -> dict:
    """``click()`` in ego lite nimmt EIN Ziel (CSS/xpath=/@N/loc=) — anders als
    Playwright kennt es kein separates "nach sichtbarem Text suchen". Ohne
    Selektor wird der Text deshalb selbst in ein XPath gegossen."""
    target = selector.strip()
    if not target and text.strip():
        escaped = text.strip().replace('"', '\\"')
        target = f'xpath=//*[contains(normalize-space(.), "{escaped}")]'
    if not target:
        return {"ok": False, "error": "Kein Ziel angegeben (selector oder text)."}
    script = (
        f"const t = await useOrCreateTaskSpace({json.dumps(_EGO_TASK_SPACE)});"
        f"await click({json.dumps(target)}, {{label:'click'}});"
        "const info = await pageInfo();"
        f"cliLog(JSON.stringify({{ok:true, clicked:{json.dumps(target)}, url:info.url}}));"
    )
    return _ego_script_json(script, timeout=30)


def ego_fill(selector: str, value: str) -> dict:
    script = (
        f"const t = await useOrCreateTaskSpace({json.dumps(_EGO_TASK_SPACE)});"
        f"await fillInput({json.dumps(selector)}, {json.dumps(value)});"
        f"cliLog(JSON.stringify({{ok:true, filled:{json.dumps(selector)}}}));"
    )
    return _ego_script_json(script, timeout=30)


def ego_wait(selector: str = "", timeout_ms: int = 15000) -> dict:
    if not selector.strip():
        return {"ok": False, "error": "Kein Element zum Warten angegeben."}
    secs = max(1, timeout_ms // 1000)
    script = (
        f"const t = await useOrCreateTaskSpace({json.dumps(_EGO_TASK_SPACE)});"
        f"await waitForElement({json.dumps(selector)}, {{timeout:{secs}}});"
        "const info = await pageInfo();"
        "cliLog(JSON.stringify({ok:true, url:info.url}));"
    )
    return _ego_script_json(script, timeout=secs + 20)


def ego_capture() -> dict:
    """``captureScreenshot()`` liefert live verifiziert einen Dateipfad auf
    DIESER Maschine zurueck, kein Base64 — die Bridge liest die Datei selbst
    und raeumt sie danach auf (dieselbe Verantwortung wie ueberall sonst, wo
    die Bridge Screenshots als Base64 zurueckgibt)."""
    script = (
        f"const t = await useOrCreateTaskSpace({json.dumps(_EGO_TASK_SPACE)});"
        "const path = await captureScreenshot();"
        "const info = await pageInfo();"
        "cliLog(JSON.stringify({ok:true, path, url:info.url}));"
    )
    result = _ego_script_json(script, timeout=30)
    if not result.get("ok"):
        return result
    path = result.get("path") or ""
    try:
        with open(path, "rb") as f:
            data = f.read()
    except OSError as e:
        return {"ok": False, "error": f"Screenshot-Datei nicht lesbar: {e}"}
    finally:
        try:
            os.remove(path)
        except OSError:
            pass
    return {"ok": True, "url": result.get("url"), "screenshot_b64": base64.b64encode(data).decode()}


def ego_tabs(index: int | None = None) -> dict:
    if index is None:
        script = (
            f"const t = await useOrCreateTaskSpace({json.dumps(_EGO_TASK_SPACE)});"
            "const tabs = await listTabs();"
            "cliLog(JSON.stringify({ok:true, tabs: tabs.map((tb,i)=>"
            "({index:i, url:tb.url, title:tb.title}))}));"
        )
        return _ego_script_json(script, timeout=20)
    script = (
        f"const t = await useOrCreateTaskSpace({json.dumps(_EGO_TASK_SPACE)});"
        "const tabs = await listTabs();"
        f"const idx = {int(index)};"
        "if (idx < 0 || idx >= tabs.length) {"
        "cliLog(JSON.stringify({ok:false, error:'Kein Tab mit dieser Nummer'}));"
        "} else {"
        "await switchTab(tabs[idx].targetId);"
        "cliLog(JSON.stringify({ok:true, active: idx, url: tabs[idx].url}));"
        "}"
    )
    return _ego_script_json(script, timeout=20)


def ego_close() -> dict:
    script = (
        f"const t = await useOrCreateTaskSpace({json.dumps(_EGO_TASK_SPACE)});"
        "await closeTab();"
        "cliLog(JSON.stringify({ok:true}));"
    )
    return _ego_script_json(script, timeout=20)


def allowed_shell_dirs() -> list[str]:
    """Die in der Tray-App freigegebenen Ordner — frisch von der Platte.

    Absichtlich bei JEDEM Aufruf gelesen: aendert der Nutzer die Liste im
    Berechtigungs-Dialog, gilt sie sofort, ohne Neustart der Bridge. Nur
    real existierende Ordner zaehlen; ``realpath`` loest Symlinks auf, damit
    der spaetere Praefix-Vergleich nicht ueber einen Link ausgetrickst wird.
    """
    dirs: list[str] = []
    for raw in _config_read().get("allowed_paths") or []:
        p = os.path.realpath(os.path.expanduser(str(raw)))
        if os.path.isdir(p):
            dirs.append(p)
    return dirs


def shell_run(command: str, cwd: str | None = None, timeout: int = 120) -> dict:
    """Einen Shell-Befehl ausfuehren — NUR wenn Ordner freigegeben sind.

    Diese Aktion stand seit jeher in der Server-Gruppenliste (`shell`) und im
    Berechtigungs-Dialog ("Shell-Befehle sind auf diese Ordner beschraenkt"),
    war in der Bridge aber NIE implementiert: Dialog und Ordnerliste
    versprachen eine Durchsetzung, hinter der gar keine Funktion lag.

    Durchgesetzt wird, was durchsetzbar ist — ehrlich benannt:
    * Ohne freigegebene Ordner ist die Aktion GESPERRT (fail-closed), selbst
      wenn die Faehigkeit `shell` serverseitig eingeschaltet ist.
    * Das Arbeitsverzeichnis muss in einem freigegebenen Ordner liegen.
    * Was der Befehl selbst tut, kann eine cwd-Schranke nicht einsperren —
      deshalb heisst die Ordnerliste im Dialog jetzt "Startordner", nicht
      mehr "beschraenkt auf".
    """
    import subprocess

    if not str(command or "").strip():
        return {"ok": False, "error": "Kein Befehl angegeben."}

    dirs = allowed_shell_dirs()
    if not dirs:
        return {"ok": False, "error": (
            "Shell-Befehle sind gesperrt: In der Bridge ist kein Ordner "
            "freigegeben. Der Nutzer kann unter Berechtigungen → Ordner-Zugriff "
            "einen Startordner hinzufuegen."
        )}

    if cwd:
        workdir = os.path.realpath(os.path.expanduser(str(cwd)))
        if not any(workdir == d or workdir.startswith(d + os.sep) for d in dirs):
            return {"ok": False, "error": (
                f"Ordner nicht freigegeben: {cwd}. "
                f"Freigegeben: {', '.join(dirs)}"
            )}
        if not os.path.isdir(workdir):
            return {"ok": False, "error": f"Ordner existiert nicht: {cwd}"}
    else:
        workdir = dirs[0]

    timeout = max(1, min(int(timeout or 120), 300))
    try:
        result = subprocess.run(
            command, shell=True, cwd=workdir,
            capture_output=True, text=True, timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return {"ok": False, "cwd": workdir,
                "error": f"Befehl nach {timeout}s abgebrochen (Timeout)."}
    except OSError as e:
        return {"ok": False, "cwd": workdir, "error": str(e)}

    return {
        "ok": result.returncode == 0,
        "returncode": result.returncode,
        "cwd": workdir,
        # Begrenzt, damit ein `cat` einer Riesendatei nicht die WebSocket-
        # Nachricht sprengt — abgeschnitten wird vorn, das Ende ist bei
        # Fehlern das Interessante.
        "stdout": result.stdout[-20000:],
        "stderr": result.stderr[-20000:],
    }


def list_windows() -> dict:
    """Welche Fenster stehen gerade offen? (Titel + zugehoerige Anwendung)

    Ohne das muss der Agent raten, was gerade vorn ist: Ein Screenshot zeigt ihm
    Pixel, der AX-Baum immer nur EINE Anwendung. Fuer "arbeite in Excel weiter"
    braucht er erst die Liste, dann `focus_window`.
    """
    if IS_MAC:
        # System Events kennt jedes Fenster jeder sichtbaren Anwendung. Ein
        # Fehler pro Anwendung darf die ganze Liste nicht kippen, deshalb die
        # `try`-Klammer INNERHALB der Schleife.
        script = (
            'set out to ""\n'
            'tell application "System Events"\n'
            '  repeat with p in (every process whose visible is true)\n'
            '    try\n'
            '      repeat with w in (every window of p)\n'
            '        set out to out & (name of p) & "\\t" & (name of w) & "\\n"\n'
            '      end repeat\n'
            '    end try\n'
            '  end repeat\n'
            'end tell\n'
            'return out'
        )
        import subprocess
        result = subprocess.run(["osascript", "-e", script], capture_output=True, text=True)
        if result.returncode != 0:
            stderr = result.stderr.strip()
            if "-1743" in stderr or "not allowed" in stderr.lower():
                stderr = ("Bedienungshilfen-Freigabe fehlt. Systemeinstellungen > "
                          "Datenschutz & Sicherheit > Bedienungshilfen.")
            return {"error": stderr or "Fensterliste nicht lesbar"}
        windows = []
        for line in result.stdout.splitlines():
            if "\t" not in line:
                continue
            app, _, title = line.partition("\t")
            if app.strip():
                windows.append({"app": app.strip(), "title": title.strip()})
        return {"windows": windows, "count": len(windows)}

    if IS_WIN:
        try:
            import uiautomation as auto  # type: ignore
        except ImportError:
            return {"error": ("Windows-Bedienungshilfen fehlen. Einmalig "
                              "'pip install uiautomation' ausfuehren.")}
        windows = []
        try:
            for ctrl in auto.GetRootControl().GetChildren():
                try:
                    title = (ctrl.Name or "").strip()
                    if not title:
                        continue
                    app = ""
                    try:
                        app = (ctrl.ClassName or "").strip()
                    except Exception:
                        pass
                    windows.append({"app": app or title, "title": title})
                except Exception:
                    continue
        except Exception as e:  # noqa: BLE001
            return {"error": f"Fensterliste nicht lesbar: {e}"}
        return {"windows": windows, "count": len(windows)}

    return {"error": "list_windows auf dieser Plattform nicht unterstuetzt"}


def focus_window(app: str, title: str = "") -> dict:
    """Eine Anwendung (optional ein bestimmtes Fenster) nach vorn holen.

    Tippen und Klicken gehen immer an das Fenster im Vordergrund — ohne diesen
    Schritt landet die Eingabe in der zuletzt benutzten Anwendung, nicht in der
    gemeinten.
    """
    if not app.strip():
        return {"ok": False, "error": "app ist erforderlich"}
    import subprocess
    if IS_MAC:
        safe_app = _applescript_string_literal(app)
        script = f'tell application "{safe_app}" to activate'
        if title.strip():
            safe_title = _applescript_string_literal(title)
            script += (
                '\ntell application "System Events" to tell process '
                f'"{safe_app}" to perform action "AXRaise" of '
                f'(first window whose name contains "{safe_title}")'
            )
        result = subprocess.run(["osascript", "-e", script], capture_output=True, text=True)
        if result.returncode != 0:
            stderr = result.stderr.strip()
            if "-1743" in stderr:
                stderr = (f'Automatisierungs-Freigabe fuer "{app}" fehlt. '
                          "Systemeinstellungen > Datenschutz & Sicherheit > Automation.")
            return {"ok": False, "app": app, "error": stderr or "Fenster nicht aktivierbar"}
        return {"ok": True, "app": app, "title": title}

    if IS_WIN:
        try:
            import uiautomation as auto  # type: ignore
        except ImportError:
            return {"ok": False, "error": ("Windows-Bedienungshilfen fehlen. Einmalig "
                                           "'pip install uiautomation' ausfuehren.")}
        try:
            needle = (title or app).lower()
            for ctrl in auto.GetRootControl().GetChildren():
                name = (ctrl.Name or "")
                if needle in name.lower():
                    ctrl.SetActive()
                    return {"ok": True, "app": app, "title": name}
            return {"ok": False, "app": app, "error": f'Kein Fenster gefunden zu "{needle}"'}
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "app": app, "error": str(e)}

    return {"ok": False, "error": "focus_window auf dieser Plattform nicht unterstuetzt"}


def ax_tree_available() -> bool:
    """Kann DIESE Maschine einen Bedienungshilfen-Baum liefern?

    Unter Windows haengt das an einem nachinstallierbaren Paket — deshalb wird
    gefragt statt geraten, sonst verspricht die Bridge etwas, das dann fehlschlaegt.
    """
    if IS_MAC:
        return True
    if IS_WIN:
        try:
            import uiautomation  # type: ignore  # noqa: F401
            return True
        except ImportError:
            return False
    return False


def _role_matches(node_role: str, wanted: str) -> bool:
    """Rollenname tolerant vergleichen — „button" trifft AXButton UND ButtonControl.

    macOS nennt einen Knopf ``AXButton``, Windows ``ButtonControl``. Wer beides
    exakt treffen muesste, koennte keine Anweisung schreiben, die auf beiden Systemen
    funktioniert — und genau das ist die Vorgabe: gleiche Faehigkeit ueberall.
    """
    if not wanted:
        return True
    a = node_role.lower().removeprefix("ax").removesuffix("control")
    b = wanted.lower().removeprefix("ax").removesuffix("control")
    return a == b or (bool(b) and b in a)


def search_tree(node: dict | None, query: str, role: str = "") -> dict | None:
    """Ersten passenden Knoten mit Klickpunkt zurueckgeben — eine Suche fuer alle.

    ``find_element`` und ``wait_for_element`` hatten je eine eigene, leicht
    unterschiedliche Fassung: die eine sah in ``value`` nach, die andere nicht. Wer
    ein Textfeld ueber seinen Inhalt suchte, fand es also nur beim Suchen und nicht
    beim Warten.
    """
    if not node:
        return None
    q = (query or "").lower()
    title = node.get("title", "").lower()
    label = node.get("label", "").lower()
    value = node.get("value", "").lower()
    if (_role_matches(node.get("role", ""), role)
            and (not q or q in title or q in label or q in value)
            and node.get("bbox")):
        bbox = node["bbox"]
        return {
            "found": True,
            "role": node.get("role"),
            "title": node.get("title", ""),
            "label": node.get("label", ""),
            "bbox": bbox,
            "center": {
                "x": int(bbox["x"] + bbox["w"] / 2),
                "y": int(bbox["y"] + bbox["h"] / 2),
            },
        }
    for child in node.get("children", []):
        found = search_tree(child, query, role)
        if found:
            return found
    return None


def get_ax_tree(app_name: str | None = None, max_depth: int = 6) -> dict:
    """Read the accessibility tree. Same node shape on macOS and Windows."""
    if IS_WIN:
        return _win_ui_tree(app_name, max_depth)
    if not IS_MAC:
        return {"error": "Accessibility tree only available on macOS and Windows"}

    try:
        import ApplicationServices as AS  # type: ignore

        def _elem_to_dict(elem, depth: int) -> dict | None:
            if depth <= 0:
                return None
            try:
                role = elem.AXRole or ""
                title = elem.AXTitle or ""
                label = elem.AXLabel or elem.AXDescription or ""
                value = ""
                try:
                    v = elem.AXValue
                    value = str(v)[:200] if v is not None else ""
                except Exception:
                    pass

                pos = elem.AXPosition
                size = elem.AXSize
                bbox = None
                if pos and size:
                    bbox = {"x": pos.x, "y": pos.y, "w": size.width, "h": size.height}

                children = []
                try:
                    for child in (elem.AXChildren or []):
                        child_dict = _elem_to_dict(child, depth - 1)
                        if child_dict:
                            children.append(child_dict)
                except Exception:
                    pass

                node: dict[str, Any] = {"role": role}
                if title:
                    node["title"] = title
                if label:
                    node["label"] = label
                if value:
                    node["value"] = value
                if bbox:
                    node["bbox"] = bbox
                if children:
                    node["children"] = children
                return node
            except Exception:
                return None

        system_ref = AS.AXUIElementCreateSystemWide()

        if app_name:
            import subprocess
            result = subprocess.run(
                ["osascript", "-e", f'id of app "{_applescript_string_literal(app_name)}"'],
                capture_output=True, text=True
            )
            bundle_id = result.stdout.strip()
            if bundle_id:
                import AppKit  # type: ignore
                running = AppKit.NSRunningApplication.runningApplicationsWithBundleIdentifier_(bundle_id)
                if running:
                    pid = running[0].processIdentifier()
                    app_ref = AS.AXUIElementCreateApplication(pid)
                    return _elem_to_dict(app_ref, max_depth) or {}

        return _elem_to_dict(system_ref, max_depth) or {}

    except Exception as e:
        return {"error": str(e)}


# ── Input Controller ──────────────────────────────────────────────────────────

class InputController:
    def __init__(self):
        import pyautogui
        pyautogui.FAILSAFE = True  # Move to top-left corner to abort
        pyautogui.PAUSE = 0.05
        self._pyautogui = pyautogui

    def _require_input_permission(self) -> None:
        """Vor JEDER gesendeten Eingabe — sonst meldet die Bridge Erfolg fuer
        etwas, das nie passiert ist.

        macOS verwirft synthetische Klicks, Tasten und Scrolls von Prozessen ohne
        Bedienungshilfen-Freigabe LAUTLOS: pyautogui wirft nichts, der Dispatcher
        gibt ``{"ok": True}`` zurueck, der Agent haelt die Aktion fuer erledigt
        und baut darauf auf. Genau das war beim Nutzer zu sehen — "Erledigt",
        aber im Browser stand nichts.

        Nur bei einem ausdruecklichen Nein abbrechen: ``None`` heisst "nicht
        feststellbar" (etwa fehlendes pyobjc), und dann ist Weitermachen
        besser als eine Blockade.
        """
        global _input_permission_prompted
        if input_permission_granted() is False:
            if not _input_permission_prompted:
                _input_permission_prompted = True
                input_permission_granted(fragen=True)   # einmalig den Dialog zeigen
            raise InputPermissionError(
                "Der Bridge fehlt die Freigabe für Bedienungshilfen — Klicks und "
                "Tastatureingaben bleiben wirkungslos. macOS wurde gefragt; bitte "
                "erlauben. Erscheint kein Dialog: Systemeinstellungen → Datenschutz "
                "& Sicherheit → Bedienungshilfen, dort AI-Employee Bridge über „+“ "
                "hinzufügen. Danach die App komplett beenden und neu starten."
            )

    def click(self, x: int, y: int, button: str = "left", double: bool = False) -> None:
        self._require_input_permission()
        if double:
            self._pyautogui.doubleClick(x, y, button=button)
        else:
            self._pyautogui.click(x, y, button=button)

    def type_text(self, text: str, interval: float = 0.02) -> None:
        """Text tippen — layouttreu.

        `pyautogui.typewrite` schickt TASTENPOSITIONEN, keine Zeichen. Auf einer
        deutschen Tastatur kommt damit aus `-` ein `ß` und aus `"` ein `#`: aus
        `open -a "Google Chrome"` wurde beim Kunden `open ßa #Google Chrome#`.
        Auf macOS tippt System Events zeichenbasiert und damit unabhaengig vom
        Layout; nur dort, wo es das nicht gibt, bleibt der alte Weg.
        """
        self._require_input_permission()
        if sys.platform == "darwin" and text:
            import subprocess
            r = subprocess.run(
                ["osascript", "-e", _keystroke_script(text)],
                capture_output=True, text=True,
            )
            if r.returncode == 0:
                return
            stderr = r.stderr.strip()
            # Fehler 1002 heisst: die Bedienungshilfen fehlen. Der Rueckfall auf
            # pyautogui wuerde dann ebenfalls nichts tun — nur eben lautlos und
            # mit Erfolgsmeldung. Deshalb hier abbrechen statt so zu tun.
            if "1002" in stderr or "nicht berechtigt" in stderr or "not allowed" in stderr.lower():
                # Beim ersten Fehlschlag den macOS-Dialog anstossen — sonst hat der
                # Nutzer keinen Weg, die Freigabe je zu erteilen.
                input_permission_granted(fragen=True)
                raise InputPermissionError(
                    "Der Bridge fehlt die Freigabe für Bedienungshilfen — Tippen und "
                    "Klicken bleiben wirkungslos. macOS wurde gefragt; bitte erlauben. "
                    "Erscheint kein Dialog: Systemeinstellungen → Datenschutz & "
                    "Sicherheit → Bedienungshilfen, dort AI-Employee Bridge hinzufügen. "
                    "Danach die App komplett beenden und neu starten."
                )
            log.warning("keystroke via System Events failed, falling back: %s", stderr)
        self._pyautogui.typewrite(text, interval=interval)

    def key_press(self, keys: list[str]) -> None:
        self._require_input_permission()
        if len(keys) == 1:
            self._pyautogui.press(keys[0])
        else:
            self._pyautogui.hotkey(*keys)

    def scroll(self, x: int, y: int, amount: int) -> None:
        self._require_input_permission()
        self._pyautogui.scroll(amount, x=x, y=y)

    def move(self, x: int, y: int) -> None:
        self._require_input_permission()
        self._pyautogui.moveTo(x, y)

    def drag(self, x1: int, y1: int, x2: int, y2: int, duration: float = 0.3) -> None:
        self._require_input_permission()
        self._pyautogui.dragTo(x2, y2, duration=duration, startX=x1, startY=y1)


# ── Command Dispatcher ────────────────────────────────────────────────────────

class CommandDispatcher:
    def __init__(self):
        self._ctrl = InputController()
        # Set by the WS client so human-capture events can be pushed upstream.
        self.input_recorder: InputRecorder | None = None
        # Set by the WS client so microphone chunks can be pushed upstream.
        self.voice_capture: VoiceCapture | None = None
        # Erst beim ersten browser_*-Befehl wirklich gestartet — wer die
        # Browser-Steuerung nie freigibt, bekommt auch kein Browserfenster.
        self._browser = BrowserController()
        # Maszstab Bildraum→Klickraum vom letzten Screenshot. (1.0, 1.0) heisst
        # „noch kein Screenshot" bzw. „kein Retina-Versatz" — dann werden
        # Koordinaten unveraendert durchgereicht (altes Verhalten). Sobald ein
        # Screenshot lief, klickt der Agent im Raum, den er auch SIEHT.
        self._coord_scale: tuple[float, float] = (1.0, 1.0)
        #: Ursprung des zuletzt aufgenommenen Bildschirms im gemeinsamen
        #: Klickraum. Bei mehreren Monitoren liegt der zweite NICHT bei 0/0 —
        #: ohne diesen Versatz landet jeder Klick auf dem zweiten Monitor auf
        #: dem ersten.
        self._coord_offset: tuple[int, int] = (0, 0)

    def _display_offset(self, display) -> tuple[int, int] | None:
        """Ursprung eines AUSDRUECKLICH genannten Bildschirms.

        Ohne das haengt der Versatz allein am zuletzt aufgenommenen Screenshot.
        Sagt der Nutzer „klick auf Bildschirm zwei", ohne dass unmittelbar davor
        ein Screenshot GENAU DIESES Bildschirms lief, klickt der Agent sonst mit
        dem Versatz des falschen Monitors — beim Nutzer am 21.08.2026 sichtbar:
        „das ging tatsaechlich voll daneben".
        """
        if not display:
            return None
        try:
            nummer = int(display)
        except (TypeError, ValueError):
            return None
        for b in list_displays():
            if b.get("number") == nummer:
                return int(b.get("x", 0)), int(b.get("y", 0))
        return None

    def _to_click_space(self, x, y, display=None) -> tuple[int, int]:
        """Eine Koordinate aus dem Bildraum (was das Modell sieht) in den
        logischen Klickraum (was pyautogui erwartet) umrechnen."""
        sx, sy = self._coord_scale
        ox, oy = self._display_offset(display) or self._coord_offset
        return round(float(x) * sx) + ox, round(float(y) * sy) + oy

    def _to_image_space(self, x, y) -> tuple[int, int]:
        """Rueckrichtung: eine logische Koordinate (z.B. aus dem
        Bedienungshilfen-Baum) in den Bildraum bringen, damit sie im SELBEN
        Raum liegt wie alles, was das Modell sonst klickt."""
        sx, sy = self._coord_scale
        ox, oy = self._coord_offset
        x, y = float(x) - ox, float(y) - oy
        return round(x / sx) if sx else int(x), round(y / sy) if sy else int(y)

    def _element_to_image_space(self, element: dict | None) -> dict | None:
        """Einen Element-Treffer (center + bbox in logischen Punkten) in den
        Bildraum umrechnen — damit sein Klickpunkt zum selben Raum gehoert wie
        das, was das Modell im Screenshot sieht. Bei Maszstab 1.0 unveraendert.

        Ohne das waere `find_element` die eine Klickquelle, die NICHT skaliert
        wird — der Klick liefe durch `_to_click_space` ein zweites Mal und
        landete daneben. Beide Quellen sprechen jetzt Bildraum.
        """
        if not element or self._coord_scale == (1.0, 1.0):
            return element
        out = dict(element)
        center = out.get("center")
        if isinstance(center, dict) and "x" in center and "y" in center:
            ix, iy = self._to_image_space(center["x"], center["y"])
            out["center"] = {"x": ix, "y": iy}
        bbox = out.get("bbox")
        if isinstance(bbox, dict) and all(k in bbox for k in ("x", "y", "w", "h")):
            bx, by = self._to_image_space(bbox["x"], bbox["y"])
            bw, bh = self._to_image_space(bbox["w"], bbox["h"])
            out["bbox"] = {"x": bx, "y": by, "w": bw, "h": bh}
        return out

    def dispatch(self, command: dict) -> dict:
        action = command.get("action", "")
        params = command.get("params", {})

        try:
            if action == "start_input_capture":
                if not self.input_recorder:
                    return {"ok": False, "error": "input capture not wired up"}
                return self.input_recorder.start()

            elif action == "stop_input_capture":
                if not self.input_recorder:
                    return {"ok": False, "error": "input capture not wired up"}
                return self.input_recorder.stop()

            elif action == "start_voice_capture":
                if not self.voice_capture:
                    return {"ok": False, "error": "voice capture not wired up"}
                return self.voice_capture.start()

            elif action == "stop_voice_capture":
                if not self.voice_capture:
                    return {"ok": False, "error": "voice capture not wired up"}
                return self.voice_capture.stop()

            elif action == "screenshot":
                # Fehlende Freigabe ist KEIN Screenshot — lieber ein klarer Fehler als
                # ein Bild, auf dem nur der Schreibtisch zu sehen ist.
                scale = params.get("scale", 1.0)
                # `display` waehlt den Bildschirm (1 = Hauptbildschirm). Ohne
                # Angabe bleibt es beim Hauptbildschirm — so verhaelt sich jeder
                # bestehende Aufrufer wie bisher.
                display = params.get("display")
                try:
                    b64, meta = capture_screenshot(scale, display)
                    # Den Maszstab merken: die NAECHSTEN Klicks liegen im Raum
                    # dieses Bildes und muessen zurueckgerechnet werden.
                    self._coord_scale = (meta["scale_x"], meta["scale_y"])
                    # Bei einem Nebenbildschirm liegt der Ursprung NICHT bei 0/0:
                    # pyautogui klickt ueber alle Bildschirme hinweg in einem
                    # gemeinsamen Raum. Ohne diesen Versatz landet jeder Klick auf
                    # dem zweiten Monitor auf dem ersten.
                    gewaehlt = next(
                        (b for b in meta["displays"] if b["number"] == meta["display"]), None
                    )
                    self._coord_offset = (
                        (gewaehlt.get("x", 0), gewaehlt.get("y", 0)) if gewaehlt else (0, 0)
                    )
                    return {
                        "screenshot_b64": b64,
                        "image_size": {"w": meta["image_w"], "h": meta["image_h"]},
                        "display": meta["display"],
                        "displays": meta["displays"],
                    }
                except ValueError as e:
                    return {"ok": False, "error": str(e)}
                except ScreenRecordingPermissionError as e:
                    return {"ok": False, "error": str(e)}

            elif action == "list_displays":
                return {"displays": list_displays()}

            elif action == "ax_tree":
                app = params.get("app")
                depth = params.get("max_depth", 6)
                tree = get_ax_tree(app, depth)
                if isinstance(tree, dict) and set(tree.keys()) == {"error"}:
                    # Fehler oben melden wie ueberall sonst — vorher steckte er
                    # als {"ax_tree": {"error": ...}} im Ergebnis und sah fuer
                    # jeden Aufrufer wie ein (leerer) Baum aus.
                    return {"ok": False, "error": tree["error"]}
                return {"ax_tree": tree}

            elif action in ("click", "mouse_click"):
                cx, cy = self._to_click_space(params["x"], params["y"], params.get("display"))
                self._ctrl.click(
                    cx, cy,
                    button=params.get("button", "left"),
                    double=params.get("double", False)
                )
                return {"ok": True}

            elif action == "type":
                self._ctrl.type_text(params["text"], params.get("interval", 0.02))
                return {"ok": True}

            elif action == "key":
                self._ctrl.key_press(params["keys"])
                return {"ok": True}

            elif action == "hotkey":
                self._ctrl.key_press(params["keys"])
                return {"ok": True}

            elif action in ("scroll", "mouse_scroll"):
                sx, sy = self._to_click_space(params["x"], params["y"], params.get("display"))
                self._ctrl.scroll(sx, sy, params.get("amount", 3))
                return {"ok": True}

            elif action in ("move", "mouse_move"):
                mx, my = self._to_click_space(params["x"], params["y"], params.get("display"))
                self._ctrl.move(mx, my)
                return {"ok": True}

            elif action == "drag":
                x1, y1 = self._to_click_space(params["x1"], params["y1"], params.get("display"))
                x2, y2 = self._to_click_space(params["x2"], params["y2"], params.get("display"))
                self._ctrl.drag(x1, y1, x2, y2, params.get("duration", 0.3))
                return {"ok": True}

            elif action == "open_app":
                app = params.get("app") or params["name"]
                import subprocess
                if IS_WIN:
                    # `open -a` gibt es unter Windows nicht — bis hierher war
                    # open_app dort schlicht kaputt (FileNotFoundError). Wie bei
                    # open_url geht os.startfile direkt an die Shell-API, ohne
                    # dass cmd.exe die Argumente noch einmal interpretiert.
                    try:
                        os.startfile(app)  # noqa: S606 — Windows-only, keine Shell dazwischen
                    except OSError as e:
                        return {"ok": False, "app": app, "error": str(e)}
                    return {"ok": True, "app": app}
                if not IS_MAC:
                    return {"ok": False, "app": app,
                            "error": "open_app auf dieser Plattform nicht unterstuetzt"}
                result = subprocess.run(["open", "-a", app], capture_output=True, text=True)
                if result.returncode != 0:
                    return {"ok": False, "app": app, "error": result.stderr.strip() or f'"{app}" not found'}
                return {"ok": True, "app": app}

            elif action == "shell_run":
                # Freigabe-Kette: Server prueft die Faehigkeit `shell` (default
                # aus), die Bridge prueft die Ordnerliste (fail-closed) — beide
                # muessen ja sagen.
                return shell_run(
                    str(params.get("command") or ""),
                    params.get("cwd"),
                    int(params.get("timeout") or 120),
                )

            elif action == "list_windows":
                return list_windows()

            elif action == "focus_window":
                return focus_window(
                    str(params.get("app") or params.get("name") or ""),
                    str(params.get("title") or ""),
                )

            # ── Browser im eigenen Profil ────────────────────────────────────
            # Die Adress-Freigabe prueft der Server, bevor der Befehl hier
            # ankommt (computer_use.py:_scope_violation) — hier steht bewusst
            # keine zweite, moeglicherweise abweichende Wahrheit.
            elif action == "browser_navigate":
                return self._browser.navigate(str(params.get("url") or ""))

            elif action == "browser_snapshot":
                return self._browser.snapshot(int(params.get("max_chars") or 20000))

            elif action == "browser_click":
                return self._browser.click(
                    str(params.get("selector") or ""), str(params.get("text") or "")
                )

            elif action == "browser_fill":
                return self._browser.fill(
                    str(params.get("selector") or ""), str(params.get("value") or "")
                )

            elif action == "browser_wait":
                return self._browser.wait(
                    str(params.get("selector") or ""),
                    int(params.get("timeout_ms") or 15000),
                )

            elif action == "browser_capture":
                return self._browser.capture(bool(params.get("full_page")))

            elif action == "browser_tabs":
                idx = params.get("index")
                return self._browser.tabs(int(idx) if idx is not None else None)

            elif action == "browser_close":
                return self._browser.close()

            # ── ego lite (echte, eingeloggte Sitzung) ────────────────────────
            elif action == "ego_run":
                return ego_run(
                    str(params.get("script") or ""),
                    int(params.get("timeout") or 120),
                )

            elif action == "ego_navigate":
                return ego_navigate(
                    str(params.get("url") or ""),
                    int(params.get("timeout_ms") or 30000),
                )

            elif action == "ego_snapshot":
                return ego_snapshot(int(params.get("max_chars") or 20000))

            elif action == "ego_click":
                return ego_click(
                    str(params.get("selector") or ""), str(params.get("text") or "")
                )

            elif action == "ego_fill":
                return ego_fill(
                    str(params.get("selector") or ""), str(params.get("value") or "")
                )

            elif action == "ego_wait":
                return ego_wait(
                    str(params.get("selector") or ""),
                    int(params.get("timeout_ms") or 15000),
                )

            elif action == "ego_capture":
                return ego_capture()

            elif action == "ego_tabs":
                idx = params.get("index")
                return ego_tabs(int(idx) if idx is not None else None)

            elif action == "ego_close":
                return ego_close()

            elif action == "open_url":
                # Eigener Zweig, weil `open -a <url>` NICHT funktioniert: -a erwartet eine
                # Anwendung. Eine Adresse geht ohne -a an den Standardbrowser. Genau daran
                # scheiterte "oeffne google" im Sprachmodus.
                url = str(params.get("url") or "").strip()
                if not url.startswith(("http://", "https://")):
                    return {"ok": False, "error": "Nur http/https-Adressen."}
                # Steuerzeichen und Leerraum haben in einer URL nichts zu suchen und
                # sind das Vehikel fuer Zeilenumbruch-/Argument-Tricks.
                if any(ch.isspace() or ord(ch) < 0x20 for ch in url):
                    return {"ok": False, "error": "Adresse enthaelt unerlaubte Zeichen."}
                import subprocess
                if sys.platform.startswith("win"):
                    # NIEMALS ueber `cmd /c start`: cmd.exe parst die Argumente ERNEUT,
                    # ein `&` in der URL wird damit zum Befehlstrenner — und `&` steht in
                    # jeder zweiten Query. `os.startfile` geht direkt an die Shell-API,
                    # ohne Kommandozeilen-Interpretation.
                    try:
                        os.startfile(url)  # noqa: S606 — Windows-only, keine Shell dazwischen
                    except OSError as e:
                        return {"ok": False, "url": url, "error": str(e)}
                    return {"ok": True, "url": url}
                cmd = ["open", url] if sys.platform == "darwin" else ["xdg-open", url]
                result = subprocess.run(cmd, capture_output=True, text=True)
                if result.returncode != 0:
                    return {"ok": False, "url": url,
                            "error": result.stderr.strip() or "Browser liess sich nicht oeffnen"}
                return {"ok": True, "url": url}

            elif action == "close_app":
                app = params.get("app") or params["name"]
                import subprocess
                if IS_MAC:
                    safe_app = _applescript_string_literal(app)
                    result = subprocess.run(
                        ["osascript", "-e", f'tell application "{safe_app}" to quit'],
                        capture_output=True, text=True,
                    )
                    if result.returncode != 0:
                        stderr = result.stderr.strip()
                        if "-1743" in stderr:
                            stderr = (
                                f'Automation permission missing for "{app}". '
                                "Grant it in System Settings > Privacy & Security > Automation."
                            )
                        return {"ok": False, "app": app, "error": stderr or f'Failed to quit "{app}"'}
                    return {"ok": True, "app": app}
                elif IS_WIN:
                    result = subprocess.run(["taskkill", "/IM", app, "/F"], capture_output=True, text=True)
                    if result.returncode != 0:
                        return {"ok": False, "app": app, "error": result.stderr.strip() or f'Failed to quit "{app}"'}
                    return {"ok": True, "app": app}
                return {"ok": False, "app": app, "error": "close_app not supported on this platform"}

            elif action in ("get_clipboard", "clipboard_read"):
                # Rueckgabewert pruefen: schlug pbpaste/PowerShell fehl, kam
                # vorher ein leerer Text zurueck — "Zwischenablage ist leer"
                # und "Lesen fehlgeschlagen" sind aber zwei verschiedene
                # Antworten, und der Agent baut auf der falschen auf.
                if IS_MAC:
                    import subprocess
                    result = subprocess.run(["pbpaste"], capture_output=True, text=True)
                    if result.returncode != 0:
                        return {"ok": False,
                                "error": result.stderr.strip() or "pbpaste fehlgeschlagen"}
                    return {"text": result.stdout}
                elif IS_WIN:
                    import subprocess
                    result = subprocess.run(
                        ["powershell", "-NoProfile", "-NonInteractive",
                         "-Command", "Get-Clipboard"],
                        capture_output=True, text=True,
                    )
                    if result.returncode != 0:
                        return {"ok": False,
                                "error": result.stderr.strip() or "Get-Clipboard fehlgeschlagen"}
                    return {"text": result.stdout.strip()}
                return {"error": "Clipboard read not supported on this platform"}

            elif action in ("set_clipboard", "clipboard_write"):
                text = params["text"]
                if IS_MAC:
                    import subprocess
                    subprocess.run(["pbcopy"], input=text.encode(), check=True)
                    return {"ok": True}
                elif IS_WIN:
                    import subprocess
                    # Der Text geht ueber stdin, NICHT in die Befehlszeile.
                    #
                    # Vorher stand hier f"Set-Clipboard '{text}'" — der Text kommt
                    # aus dem Netz, und ein einzelner Apostroph darin beendet das
                    # PowerShell-Literal: aus  x'; Start-Process calc; '  wurde ein
                    # zweiter, frei waehlbarer Befehl mit den Rechten des
                    # angemeldeten Nutzers. Der macOS-Zweig direkt darueber macht
                    # es seit jeher richtig (pbcopy ueber stdin); nur der
                    # Windows-Zweig interpolierte.
                    subprocess.run(
                        ["powershell", "-NoProfile", "-NonInteractive", "-Command",
                         "$in = [Console]::In.ReadToEnd(); Set-Clipboard -Value $in"],
                        input=text, text=True, check=True,
                    )
                    return {"ok": True}
                return {"error": "Clipboard write not supported on this platform"}

            elif action == "find_element":
                query = params.get("query", "")
                role = params.get("role", "")
                app = params.get("app")
                tree = get_ax_tree(app, max_depth=8)
                if isinstance(tree, dict) and tree.get("error"):
                    return {"found": False, "error": tree["error"], "query": query}
                result = search_tree(tree, query, role)
                return self._element_to_image_space(result) or {
                    "found": False, "query": query, "role": role}

            elif action == "wait_for_element":
                query = params.get("query", "")
                role = params.get("role", "")
                app = params.get("app")
                timeout = min(params.get("timeout", 10), 30)  # max 30s
                interval = params.get("interval", 0.5)

                deadline = time.time() + timeout
                last_error = ""
                while time.time() < deadline:
                    tree = get_ax_tree(app, max_depth=8)
                    if isinstance(tree, dict) and tree.get("error"):
                        # Fehlt der Baum ganz (z.B. Paket nicht installiert), hilft
                        # Warten nicht — dann lieber sofort ehrlich sein.
                        return {"found": False, "error": tree["error"], "query": query}
                    found = search_tree(tree, query, role)
                    if found:
                        return self._element_to_image_space(found)
                    time.sleep(interval)

                return {"found": False, "timeout": True, "query": query, "error": last_error}

            elif action == "ping":
                return {"pong": True, "ts": time.time()}

            else:
                return {"error": f"Unknown action: {action}"}

        except KeyError as e:
            return {"error": f"Missing parameter: {e}"}
        except Exception as e:
            return {"error": str(e)}


# ── WebSocket Bridge ──────────────────────────────────────────────────────────

class Bridge:
    def __init__(
        self,
        ws_url: str,
        token: str,
        session_id: str | None = None,
        extra_headers: dict[str, str] | None = None,
        on_state=None,
        on_voice_state=None,
    ):
        self.ws_url = ws_url
        self.token = token
        self.session_id = session_id
        # Extra request headers to send on the WebSocket handshake, e.g. an
        # identity-aware proxy's service-token headers (Cloudflare Access,
        # Google IAP, oauth2-proxy, Authelia). See issue #374. Values are
        # credentials and are never logged — only the header names are.
        self.extra_headers: dict[str, str] = dict(extra_headers or {})
        # Optional GUI callback: on_state("connected"|"reconnecting"|"rejected", detail)
        self.on_state = on_state
        # Optional GUI callback: on_voice_state(True|False) — mic capture on/off
        # (issue #478 phase 3, tray mic-status indicator).
        self.on_voice_state = on_voice_state
        self.dispatcher: CommandDispatcher | None = None
        self._running = False
        # Human-capture events are produced on pynput's own threads, so they
        # land in a thread-safe queue and are drained by an asyncio task that
        # owns the WebSocket (never send from the listener thread directly).
        self._input_events: queue.Queue = queue.Queue(maxsize=1000)
        # Same pattern for microphone chunks — sounddevice calls back on its
        # own audio thread, never from the asyncio loop.
        self._voice_events: queue.Queue = queue.Queue(maxsize=1000)

    def _ensure_dispatcher(self) -> CommandDispatcher:
        if self.dispatcher is None:
            log.info("Initializing desktop control")
            self.dispatcher = CommandDispatcher()
            self.dispatcher.input_recorder = InputRecorder(self._queue_input_event)
            self.dispatcher.voice_capture = VoiceCapture(
                self._queue_voice_event, on_active_change=self.on_voice_state)
            log.info("Desktop control ready")
        return self.dispatcher

    def _queue_input_event(self, event: dict) -> None:
        """Called from the pynput listener thread — must not block or send."""
        try:
            self._input_events.put_nowait(event)
        except queue.Full:
            log.warning("Input-capture queue full — dropping event")

    def _queue_voice_event(self, event: dict) -> None:
        """Called from the sounddevice audio thread — must not block or send."""
        try:
            self._voice_events.put_nowait(event)
        except queue.Full:
            log.warning("Voice-capture queue full — dropping chunk")

    async def _drain_input_events(self, ws) -> None:
        """Forward queued human-capture events to the orchestrator."""
        while self._running:
            try:
                event = await asyncio.get_event_loop().run_in_executor(
                    None, self._input_events.get, True, 0.5
                )
            except queue.Empty:
                continue
            except Exception:  # noqa: BLE001 — loop shutting down
                return
            try:
                await ws.send(json.dumps({"type": "input_event", "event": event}))
            except Exception:  # noqa: BLE001 — connection gone; reconnect loop handles it
                return

    async def _drain_voice_events(self, ws) -> None:
        """Forward queued microphone chunks to the orchestrator."""
        while self._running:
            try:
                event = await asyncio.get_event_loop().run_in_executor(
                    None, self._voice_events.get, True, 0.5
                )
            except queue.Empty:
                continue
            except Exception:  # noqa: BLE001 — loop shutting down
                return
            try:
                await ws.send(json.dumps({"type": "voice_chunk", "event": event}))
            except Exception:  # noqa: BLE001 — connection gone; reconnect loop handles it
                return

    async def connect(self) -> None:
        if not self.session_id:
            raise ValueError(
                "session_id is required.\n"
                "Go to the web UI → agent → Computer Use tab → create a session first,\n"
                "then pass the session ID with --session <id>."
            )

        # The JWT is sent as an Authorization header only, never in the URL:
        # query strings land in reverse-proxy access logs, referrer headers and
        # log rotation. The server reads the header when no query token is
        # present. See issue #373.
        query = urllib.parse.urlencode({"session_id": self.session_id})
        url = f"{self.ws_url}/ws/computer-use/bridge?{query}"
        # Merge any configured proxy headers in first, then set Authorization so
        # our bearer token can never be shadowed by a stray extra header.
        headers = {**self.extra_headers, "Authorization": f"Bearer {self.token}"}
        # Verifizierter Kontext (System-CA oder gepinntes Zertifikat) — der
        # Token verlaesst die Maschine erst NACH bestandenem Handshake.
        try:
            ssl_context = ssl_context_for(url)
        except TlsTrustError as e:
            log.error(str(e))
            self._emit_state("rejected", str(e))
            return
        # Log without the query string so no credential (now or in future) leaks
        # into the client log file.
        log.info(f"Connecting to {self.ws_url}/ws/computer-use/bridge")
        if self.extra_headers:
            # Names only — the values are service-token credentials.
            log.info(f"Extra request headers: {', '.join(sorted(self.extra_headers))}")

        async for ws in websockets.connect(
            url,
            additional_headers=headers,
            ping_interval=30,
            open_timeout=15,
            ssl=ssl_context,
        ):
            self._running = True
            try:
                log.info("WebSocket connected")
                # Announce capabilities
                caps = {
                    "type": "hello",
                    "platform": platform.system(),
                    "bridge_version": BRIDGE_VERSION,
                    # Nur melden, was diese Maschine WIRKLICH kann. Der
                    # Bedienungshilfen-Baum gibt es auf macOS immer, auf Windows nur
                    # mit installiertem `uiautomation` — ein Agent, der sich auf eine
                    # falsche Zusage verlaesst, verspricht dem Nutzer etwas.
                    "capabilities": BASE_ACTIONS
                                    + (AX_ACTIONS if ax_tree_available() else []),
                    "ax_tree_available": ax_tree_available(),
                    "ego_browser_available": ego_browser_available(),
                }
                await ws.send(json.dumps(caps))
                log.info(f"Connected. Waiting for commands... (platform: {platform.system()})")
                self._emit_state("connected", "")
                try:
                    self._ensure_dispatcher()
                except Exception as e:
                    log.exception("Desktop control initialization failed")
                    await ws.send(json.dumps({
                        "type": "bridge_error",
                        "error": f"desktop_control_unavailable: {e}",
                    }))

                drain_task = asyncio.create_task(self._drain_input_events(ws))
                voice_drain_task = asyncio.create_task(self._drain_voice_events(ws))
                try:
                    async for raw in ws:
                        await self._handle_message(ws, raw)
                finally:
                    drain_task.cancel()
                    voice_drain_task.cancel()
                    if self.dispatcher and self.dispatcher.input_recorder:
                        # Never leave a keylogger running past the connection.
                        self.dispatcher.input_recorder.stop()
                    if self.dispatcher and self.dispatcher.voice_capture:
                        # Never leave the microphone open past the connection.
                        self.dispatcher.voice_capture.stop()

            except websockets.ConnectionClosed as e:
                # 1008 = the server REJECTED us (session expired/unknown, wrong
                # user, another bridge already attached). Retrying that forever
                # can never succeed — genau das tat die Schleife aber: sie
                # meldete "rejected" und waehlte trotzdem alle 5s neu, das
                # Tray-Symbol blieb auf "verbunden" stehen. Eine endgueltige
                # Ablehnung beendet die Schleife jetzt wirklich; neu verbinden
                # heisst ab hier: der Mensch klickt Verbinden/Anmelden.
                reason = (getattr(e, "reason", "") or str(e)).strip()
                if getattr(getattr(e, "rcvd", None), "code", None) == 1008:
                    log.error(f"Rejected by server: {reason}")
                    self._emit_state("rejected", reason)
                    return
                log.warning(f"Connection closed: {e}. Reconnecting in 5s...")
                self._emit_state("reconnecting", reason)
                await asyncio.sleep(5)
            except Exception as e:
                log.error(f"Error: {e}. Reconnecting in 5s...")
                self._emit_state("reconnecting", str(e).strip() or e.__class__.__name__)
                await asyncio.sleep(5)
            finally:
                self._running = False

    def _emit_state(self, state: str, detail: str = "") -> None:
        """Tell the GUI (if any) what the connection is doing. Never fatal."""
        if not self.on_state:
            return
        try:
            self.on_state(state, detail)
        except Exception:  # noqa: BLE001 — a broken callback must not kill the bridge
            log.debug("on_state callback failed", exc_info=True)

    async def _handle_message(self, ws, raw: str) -> None:
        try:
            msg = json.loads(raw)
        except json.JSONDecodeError:
            return

        msg_type = msg.get("type", "")

        if msg_type == "command":
            cmd_id = msg.get("id", "")
            command = msg.get("command", {})
            log.info(f"Command [{cmd_id}]: {command.get('action')} {command.get('params', {})}")

            result = await asyncio.get_event_loop().run_in_executor(
                None, self._ensure_dispatcher().dispatch, command
            )
            response = {"type": "result", "id": cmd_id, "result": result}
            await ws.send(json.dumps(response))

        elif msg_type == "ping":
            await ws.send(json.dumps({"type": "pong"}))

        elif msg_type == "session_info":
            self.session_id = msg.get("session_id")
            log.info(f"Session ID: {self.session_id}")


# ── Extra headers (identity-aware proxy support, issue #374) ──────────────────

BRIDGE_CONFIG_PATH = os.path.expanduser("~/.ai_employee_bridge.json")


def _parse_header_arg(raw: str) -> tuple[str, str]:
    """Parse a ``--header "Name: value"`` string into a (name, value) pair."""
    name, sep, value = raw.partition(":")
    name = name.strip()
    value = value.strip()
    if not sep or not name:
        raise ValueError(f"Invalid --header (expected 'Name: value'): {raw!r}")
    return name, value


def collect_extra_headers(header_args: list[str] | None) -> dict[str, str]:
    """Gather extra WS handshake headers from three sources.

    Precedence, lowest to highest (later wins):
      1. Cloudflare service-token env shortcuts
         (CF_ACCESS_CLIENT_ID / CF_ACCESS_CLIENT_SECRET).
      2. ``extra_headers`` object in ~/.ai_employee_bridge.json.
      3. Repeatable ``--header "Name: value"`` command-line flags.

    Header values are credentials — this function never logs them.
    """
    headers: dict[str, str] = {}

    cf_id = os.environ.get("CF_ACCESS_CLIENT_ID")
    cf_secret = os.environ.get("CF_ACCESS_CLIENT_SECRET")
    if cf_id:
        headers["CF-Access-Client-Id"] = cf_id
    if cf_secret:
        headers["CF-Access-Client-Secret"] = cf_secret

    try:
        with open(BRIDGE_CONFIG_PATH, encoding="utf-8") as fh:
            cfg = json.load(fh)
        cfg_headers = cfg.get("extra_headers")
        if isinstance(cfg_headers, dict):
            for name, value in cfg_headers.items():
                if isinstance(name, str) and name.strip():
                    headers[name.strip()] = str(value)
    except FileNotFoundError:
        pass
    except (OSError, ValueError) as e:
        log.warning(f"Could not read {BRIDGE_CONFIG_PATH}: {e}")

    for raw in header_args or []:
        try:
            name, value = _parse_header_arg(raw)
        except ValueError as e:
            log.warning(str(e))
            continue
        headers[name] = value

    return headers


# ── Entry Point ───────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="AI-Employee Computer-Use Bridge")
    parser.add_argument("--url", default=os.environ.get("AI_EMPLOYEE_URL", ""),
                        help="AI-Employee WebSocket URL (e.g. wss://myserver.com)")
    parser.add_argument("--token", default=os.environ.get("AI_EMPLOYEE_TOKEN", ""),
                        help="JWT auth token from AI-Employee web UI")
    parser.add_argument("--session", default=os.environ.get("AI_EMPLOYEE_SESSION", ""),
                        help="Optional: specific session ID to connect to")
    parser.add_argument("--header", action="append", default=[], metavar="'Name: value'",
                        help="Extra request header for the WebSocket handshake "
                             "(repeatable). Use to authenticate through an "
                             "identity-aware proxy, e.g. "
                             "--header 'CF-Access-Client-Id: <id>'. Values are "
                             "credentials and are never logged.")
    args = parser.parse_args()

    if not args.url:
        print("ERROR: --url or AI_EMPLOYEE_URL required")
        sys.exit(1)
    if not args.token:
        print("ERROR: --token or AI_EMPLOYEE_TOKEN required")
        sys.exit(1)
    if not args.session:
        print("ERROR: --session or AI_EMPLOYEE_SESSION required")
        print("  Create a session first: web UI → agent → Computer Use tab → New Session")
        print("  Then copy the session ID and pass it here.")
        sys.exit(1)

    # Check dependencies
    missing = _check_deps()
    if missing:
        print(f"ERROR: Missing packages: {', '.join(missing)}")
        print("Run: pip install -r requirements.txt")
        sys.exit(1)

    # macOS accessibility permission check
    if IS_MAC:
        try:
            import ApplicationServices as AS  # type: ignore
            if not AS.AXIsProcessTrusted():
                print("WARNING: Accessibility permissions not granted.")
                print("Go to: System Settings → Privacy & Security → Accessibility")
                print("Add Terminal (or your Python app) to the allowed list.")
                print("AX Tree features will be unavailable until permission is granted.\n")
        except ImportError:
            print("WARNING: pyobjc not installed — AX Tree unavailable. Run: pip install pyobjc-framework-ApplicationServices")

    ws_url = args.url.rstrip("/").replace("http://", "ws://").replace("https://", "wss://")
    extra_headers = collect_extra_headers(args.header)
    bridge = Bridge(ws_url, args.token, args.session or None, extra_headers=extra_headers)

    print(f"AI-Employee Computer-Use Bridge")
    print(f"  Platform: {platform.system()}")
    print(f"  Server:   {ws_url}")
    print(f"  Press Ctrl+C to stop")
    print()

    try:
        asyncio.run(bridge.connect())
    except KeyboardInterrupt:
        print("\nBridge stopped.")


async def run(
    url: str,
    token: str,
    session_id: str,
    stop_event: threading.Event | None = None,
    extra_headers: dict[str, str] | None = None,
    on_state=None,
    on_voice_state=None,
) -> None:
    """Async entry point for use as a library (e.g. from tray_app).

    ``on_state(state, detail)`` is called on connect/disconnect so a GUI can show
    the real state. Without it the tray had no way to learn that the handshake
    succeeded — connect() never returns while the bridge is up — and therefore
    displayed "Verbinde…" forever, even on a perfectly healthy connection.

    ``on_voice_state(active)`` mirrors that for the microphone (issue #478
    phase 3): called with True/False whenever capture starts/stops, so a tray
    icon can show a mic indicator without polling.
    """
    ws_url = url.rstrip("/").replace("http://", "ws://").replace("https://", "wss://")
    if extra_headers is None:
        extra_headers = collect_extra_headers(None)
    bridge = Bridge(ws_url, token, session_id, extra_headers=extra_headers,
                     on_state=on_state, on_voice_state=on_voice_state)
    if stop_event:
        async def _watch_stop():
            while not stop_event.is_set():
                await asyncio.sleep(0.5)
            bridge.running = False
        asyncio.create_task(_watch_stop())
    await bridge.connect()


if __name__ == "__main__":
    main()
