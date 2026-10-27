#!/usr/bin/env python3
"""
AI-Employee Computer-Use Bridge — System Tray App

First launch: shows setup dialog (URL + email + password).
Bridge logs in automatically and fetches a session.
Config saved to ~/.ai_employee_bridge.json (no passwords stored — only token).

Tray menu:
  • Status        → connection state + capabilities
  • Berechtigungen… → toggle what the agent may do on this machine + folder access
  • Einstellungen… → server URL / re-login
  • AI-Employee öffnen → open web UI
  • Verbinden / Trennen
  • Beenden
"""
from __future__ import annotations

import json
import logging
import os
import platform
import sys
import threading
import asyncio
import base64
import subprocess
import tempfile
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from pathlib import Path

IS_MAC = platform.system() == "Darwin"
IS_WIN = platform.system() == "Windows"

CONFIG_FILE = Path.home() / ".ai_employee_bridge.json"

_bridge_mod = None


def _bridge_module():
    """Das bridge-Modul laden — im PyInstaller-Bundle liegt es neben der App.

    Dieselbe Suche stand vorher nur im Verbindungs-Thread. Sie steht jetzt
    hier, weil auch die TLS-Schicht (ssl_context_for, repin_server) daraus
    kommt: EINE Wahrheit darueber, welchem Server-Zertifikat vertraut wird,
    fuer die HTTP-Aufrufe der Tray-App UND den WebSocket der Bridge.
    """
    global _bridge_mod
    if _bridge_mod is None:
        if getattr(sys, "frozen", False):
            bundle_contents = Path(sys.executable).parent.parent
            for candidate in ["Frameworks", "Resources", "MacOS"]:
                d = bundle_contents / candidate
                if (d / "bridge.py").exists():
                    bridge_dir = d
                    break
            else:
                bridge_dir = Path(sys.executable).parent
        else:
            bridge_dir = Path(__file__).parent
        if str(bridge_dir) not in sys.path:
            sys.path.insert(0, str(bridge_dir))
        import bridge as bridge_module
        _bridge_mod = bridge_module
    return _bridge_mod


def _tls_context(base_url: str, allow_pin_new: bool = True):
    """Verifizierter SSL-Kontext fuer diese Adresse (System-CA oder Pin).

    Vorher stand hier ein globaler Kontext mit ``CERT_NONE`` — Login samt
    Passwort und Token waren gegen einen Mitleser ungeschuetzt. Die
    Vertrauens-Logik (TOFU-Pinning wie bei SSH) lebt in bridge.py; hier wird
    sie nur benutzt. Wirft ``bridge.TlsTrustError`` bei geaendertem Zertifikat.
    """
    if not str(base_url or "").lower().startswith(("https://", "wss://")):
        return None
    return _bridge_module().ssl_context_for(base_url, allow_pin_new=allow_pin_new)

try:
    from _version import BRIDGE_VERSION
except ImportError:
    BRIDGE_VERSION = "dev"

def _setup_app_logging() -> logging.Logger:
    logger = logging.getLogger(__name__)
    logger.setLevel(logging.INFO)
    try:
        log_dir = os.path.expanduser("~/Library/Logs/ai-employee")
        if sys.platform != "darwin":
            log_dir = os.path.join(os.path.expanduser("~"), ".ai-employee", "logs")
        os.makedirs(log_dir, exist_ok=True)
        handler = logging.FileHandler(os.path.join(log_dir, "bridge.log"))
        handler.setFormatter(logging.Formatter("[%(asctime)s] [Tray] %(message)s"))
        logger.addHandler(handler)
    except Exception:
        pass
    return logger


app_log = _setup_app_logging()

# ── Capability metadata ────────────────────────────────────────────────────────

# ACHTUNG: Diese Liste muss zu CAPABILITY_GROUPS in
# orchestrator/app/api/computer_use.py passen. Eine Gruppe, die hier fehlt, kann
# der Nutzer nicht einschalten — genau so waren `input_capture` und
# `voice_capture` serverseitig vorhanden, aber ueber die Oberflaeche nie
# erreichbar.
CAPABILITY_META = [
    {"id": "screenshots",   "label": "Screenshots",           "desc": "Bildschirminhalt lesen",                  "risk": "gering"},
    {"id": "accessibility", "label": "Accessibility Tree",    "desc": "UI-Elemente lesen und finden",            "risk": "gering"},
    {"id": "mouse",         "label": "Maus-Steuerung",        "desc": "Cursor bewegen, klicken, scrollen",       "risk": "mittel"},
    {"id": "keyboard",      "label": "Tastatur-Eingabe",      "desc": "Text schreiben und Shortcuts senden",     "risk": "mittel"},
    {"id": "apps",          "label": "Apps öffnen / schließen","desc": "Anwendungen starten, beenden, fokussieren", "risk": "mittel"},
    {"id": "clipboard",     "label": "Zwischenablage",        "desc": "Zwischenablage lesen und schreiben",     "risk": "mittel"},
    {"id": "browser",       "label": "Browser-Steuerung",     "desc": "Eigenes Browser-Profil bedienen (Seiten lesen, Formulare)", "risk": "hoch"},
    {"id": "ego_browser",   "label": "ego lite",              "desc": "Deine ECHTE, eingeloggte Browsersitzung bedienen (ego-lite-App noetig)", "risk": "hoch"},
    {"id": "input_capture", "label": "Eingaben mitschneiden", "desc": "Deine Klicks und Tasten aufzeichnen (Replay)", "risk": "hoch"},
    {"id": "voice_capture", "label": "Mikrofon",              "desc": "Mikrofon mithören (nur zwischen Start und Stopp)", "risk": "hoch"},
    {"id": "shell",         "label": "Shell-Befehle",         "desc": "Terminal-Befehle in freigegebenen Ordnern (siehe Ordner-Zugriff)", "risk": "hoch"},
    {"id": "observe",       "label": "Mitsehen",              "desc": "Von sich aus hinsehen, Regeln prüfen, Hilfe anbieten (Overlay)", "risk": "hoch"},
]

DEFAULT_CAPABILITIES = {c["id"] for c in CAPABILITY_META if c["id"] in
                        {"screenshots", "accessibility", "mouse", "keyboard", "apps"}}

# ── Config ─────────────────────────────────────────────────────────────────────

def load_config() -> dict:
    if CONFIG_FILE.exists():
        try:
            cfg = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
            if "allowed_capabilities" not in cfg:
                cfg["allowed_capabilities"] = sorted(DEFAULT_CAPABILITIES)
            if "allowed_paths" not in cfg:
                cfg["allowed_paths"] = []
            return cfg
        except Exception:
            pass
    return {"url": "", "token": "", "session": "", "auto_connect": True,
            "allowed_capabilities": sorted(DEFAULT_CAPABILITIES), "allowed_paths": []}


def save_config(cfg: dict) -> str | None:
    """Persist the config. Returns None on success, else an error message.

    Must never raise: on Windows this is called from a daemon thread in the tray
    menu handler, where an exception dies silently — the settings then "work"
    until the next start and are gone afterwards (the reported symptom). Common
    causes are a OneDrive-redirected %USERPROFILE%, roaming profiles or missing
    write rights, so the error has to be surfaced to the user instead.
    Written atomically so a crash mid-write can't leave a truncated file.
    """
    try:
        CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
        tmp = CONFIG_FILE.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(cfg, indent=2), encoding="utf-8")
        try:
            # In der Datei steht das JWT des Nutzers — mit der umask-Vorgabe
            # (0644) konnte jeder andere lokale Account es lesen und sich
            # damit als dieser Nutzer ausgeben. Erst die Rechte setzen, DANN
            # an den endgueltigen Namen — so gibt es kein Fenster, in dem der
            # Token weltlesbar liegt. Windows kennt keine POSIX-Rechte; dort
            # schuetzt die ACL des Benutzerprofils.
            os.chmod(tmp, 0o600)
        except OSError:
            pass
        tmp.replace(CONFIG_FILE)
        return None
    except Exception as e:  # noqa: BLE001 — surfaced to the user, never fatal
        msg = f"Einstellungen konnten nicht gespeichert werden ({CONFIG_FILE}): {e}"
        try:
            print(msg, file=sys.stderr)
        except Exception:  # noqa: BLE001 — no console in a windowed build
            pass
        return msg


def _notify(message: str, title: str = "AI-Employee Bridge") -> None:
    """Show a message to the user. Falls back to stderr in headless builds.

    The tray app is windowed (no console), so a silent failure is invisible —
    anything the user must act on has to come up as a dialog.
    """
    try:
        import tkinter as tk
        from tkinter import messagebox
        root = tk.Tk()
        root.withdraw()
        root.attributes("-topmost", True)
        messagebox.showinfo(title, message)
        root.destroy()
        return
    except Exception:  # noqa: BLE001 — no display / no tk available
        pass
    try:
        print(f"{title}: {message}", file=sys.stderr)
    except Exception:  # noqa: BLE001
        pass


def normalize_bridge_url(value: str):
    """Accept either a server URL or the full bridge websocket URL from the UI."""
    raw = value.strip().rstrip("/")
    if not raw:
        return "", None

    parsed = urllib.parse.urlparse(raw)
    if not parsed.scheme or not parsed.netloc:
        return raw, None

    qs = urllib.parse.parse_qs(parsed.query)
    session_id = (qs.get("session_id") or [None])[0]

    scheme = parsed.scheme
    if scheme == "wss":
        scheme = "https"
    elif scheme == "ws":
        scheme = "http"

    return f"{scheme}://{parsed.netloc}", session_id


# ── HTTP helpers ───────────────────────────────────────────────────────────────

def _api(method, base_url, path, token, body=None):
    url = base_url.rstrip("/") + path
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method,
                                 headers={"Content-Type": "application/json",
                                          "Authorization": f"Bearer {token}",
                                          "User-Agent": f"AI-Employee-Bridge/{BRIDGE_VERSION}"})
    with urllib.request.urlopen(req, timeout=10, context=_tls_context(base_url)) as r:
        return json.loads(r.read())


def _erneuerung_aus_antwort(antwort) -> str:
    """Das Erneuerungstoken aus den Set-Cookie-Kopfzeilen der Server-Antwort.

    Der Server legt es bei Anmeldung und Erneuerung als Cookie ``refresh_token``
    ab (7 Tage gueltig) — die Web-App nutzt es von selbst, die Bridge hat es bis
    1.378.3 schlicht verworfen und stand nach 30 Minuten ohne gueltige Anmeldung da.
    """
    try:
        zeilen = antwort.headers.get_all("Set-Cookie") or []
    except Exception:  # noqa: BLE001
        zeilen = []
    for zeile in zeilen:
        kopf = zeile.split(";", 1)[0].strip()
        if kopf.startswith("refresh_token="):
            return kopf.split("=", 1)[1].strip().strip('"')
    return ""


def _token_laeuft_ab(token: str, puffer_s: int = 300) -> bool:
    """Laeuft das Zugangstoken in den naechsten ``puffer_s`` Sekunden ab?
    Liest nur den Ablauf aus dem JWT (ohne Pruefung — das macht der Server).
    Unlesbar zaehlt als abgelaufen."""
    import time as _time
    try:
        nutzlast = token.split(".")[1]
        nutzlast += "=" * (-len(nutzlast) % 4)
        ablauf = int(json.loads(base64.urlsafe_b64decode(nutzlast)).get("exp", 0))
    except Exception:  # noqa: BLE001
        return True
    return ablauf - _time.time() < puffer_s


def api_auffrischen(base_url: str, erneuerung: str) -> tuple[str, str]:
    """Neues Zugangstoken (und neues Erneuerungstoken) ueber /auth/refresh —
    derselbe Weg, den die iOS-App nach SSO nutzt (Bearer statt Cookie)."""
    url = base_url.rstrip("/") + "/api/v1/auth/refresh"
    req = urllib.request.Request(url, data=b"{}", method="POST",
                                 headers={"Content-Type": "application/json",
                                          "Authorization": f"Bearer {erneuerung}",
                                          "User-Agent": f"AI-Employee-Bridge/{BRIDGE_VERSION}"})
    with urllib.request.urlopen(req, timeout=10, context=_tls_context(base_url)) as r:
        antwort = json.loads(r.read())
        neu = _erneuerung_aus_antwort(r)
    return antwort["access_token"], (neu or erneuerung)


_erneuerung_sperre = threading.Lock()


def token_erneuern(cfg: dict) -> bool:
    """Zugangstoken mit dem gespeicherten Erneuerungstoken auffrischen und
    speichern. False = keins da oder vom Server abgelehnt (dann: neu anmelden)."""
    with _erneuerung_sperre:
        erneuerung = str(cfg.get("refresh") or "")
        if not erneuerung or not cfg.get("url"):
            return False
        try:
            zugang, neu = api_auffrischen(cfg["url"], erneuerung)
        except urllib.error.HTTPError as e:
            if e.code in (401, 403):
                # Abgelaufen oder widerrufen — ein Weiterprobieren hilft nicht.
                cfg["refresh"] = ""
                save_config(cfg)
            app_log.warning("Token-Erneuerung abgelehnt: HTTP %s", e.code)
            return False
        except Exception as e:  # noqa: BLE001 — Netz weg: spaeter erneut
            app_log.warning("Token-Erneuerung fehlgeschlagen: %s: %s", type(e).__name__, e)
            return False
        cfg["token"], cfg["refresh"] = zugang, neu
        save_config(cfg)
        app_log.info("Zugangstoken erneuert")
        return True


def token_frisch_halten(cfg: dict | None) -> None:
    """Kurz vor Ablauf still erneuern. Billig, solange nichts faellig ist:
    nur der Ablauf im Token wird gelesen, ins Netz geht es erst fuenf Minuten vorher."""
    if cfg and cfg.get("token") and cfg.get("refresh") and _token_laeuft_ab(cfg["token"]):
        token_erneuern(cfg)


def api_login(base_url, email, password):
    """Zugangstoken (fuer Aufrufer, die nur das brauchen)."""
    return api_login_tokens(base_url, email, password)[0]


def api_login_tokens(base_url, email, password) -> tuple[str, str]:
    """(Zugangstoken, Erneuerungstoken) — das zweite kommt als Cookie."""
    url = base_url.rstrip("/") + "/api/v1/auth/login"
    data = json.dumps({"email": email, "password": password}).encode()
    req = urllib.request.Request(url, data=data,
                                 headers={"Content-Type": "application/json",
                                          "User-Agent": f"AI-Employee-Bridge/{BRIDGE_VERSION}"})
    bridge = _bridge_module()
    try:
        ctx = _tls_context(base_url)
    except bridge.TlsTrustError:
        # Die Anmeldung ist die EINE Stelle, an der einem geaenderten
        # Zertifikat neu vertraut werden darf: der Mensch sitzt davor und
        # hat gerade ausdruecklich "Anmelden" geklickt. Der neue
        # Fingerabdruck wird gepinnt und laut protokolliert. Ueberall
        # sonst bleibt ein geaendertes Zertifikat ein harter Fehler.
        fp = bridge.repin_server(base_url)
        app_log.warning("Server-Zertifikat bei Neu-Anmeldung neu gepinnt: %s",
                        bridge.format_fingerprint(fp) if fp else "System-CA")
        ctx = _tls_context(base_url)
    with urllib.request.urlopen(req, timeout=10, context=ctx) as r:
        antwort = json.loads(r.read())
        erneuerung = _erneuerung_aus_antwort(r)
    if antwort.get("mfa_required"):
        # Zwei-Faktor (#915): die Bridge kann den Code-Schritt nicht. Statt mit
        # KeyError abzubrechen, klar sagen, was zu tun ist.
        raise ZweiFaktorNichtUnterstuetzt(
            "Dieses Konto nutzt die Zwei-Faktor-Anmeldung. Die Bridge kann sich damit "
            "noch nicht anmelden — bitte ein eigenes Konto ohne zweiten Faktor für die "
            "Bridge verwenden oder den Administrator fragen."
        )
    return antwort["access_token"], erneuerung


class ZweiFaktorNichtUnterstuetzt(RuntimeError):
    """Anmeldung verlangt einen zweiten Faktor, den die Bridge nicht abfragt."""


def api_ws_ticket(base_url, token) -> str:
    """Einmal-Ticket fuer eine WebSocket-Verbindung holen (30 s gueltig).

    Der Dauer-JWT darf NICHT in die WS-URL: Query-Parameter landen in
    Proxy- und Zugriffsprotokollen. Faellt das Ticket aus, ist das ein
    Verbindungsfehler — kein Grund, auf ``token=`` zurueckzufallen (#337).
    """
    return _api("POST", base_url, "/api/v1/ws/ticket", token)["ticket"]


def _scope_payload(cfg: dict | None) -> dict:
    """Freigabelisten fuer den Server — und zwar so, dass "keine Einschraenkung"
    auch wirklich ankommt.

    Eine leere Liste heisst serverseitig "nichts erlaubt", NICHT "alles". Wer im
    Dialog nichts eintraegt, meint aber "nicht einschraenken" — deshalb wird in
    dem Fall ausdruecklich `clear_*_scope` geschickt statt einer leeren Liste.
    """
    cfg = cfg or {}
    apps = [a for a in (cfg.get("allowed_apps") or []) if str(a).strip()]
    domains = [d for d in (cfg.get("allowed_domains") or []) if str(d).strip()]
    payload: dict = {}
    if apps:
        payload["allowed_apps"] = apps
    else:
        payload["clear_app_scope"] = True
    if domains:
        payload["allowed_domains"] = domains
    else:
        payload["clear_domain_scope"] = True
    # Mitdenken (#1019): Bilder an den Agenten. Nur mitschicken, wenn hier je
    # eingestellt — sonst bleibt, was in der Weboberflaeche steht.
    if "mitdenken" in cfg:
        payload["mitdenken"] = bool(cfg.get("mitdenken"))
    return payload


def api_create_session(base_url, token, caps, cfg=None):
    body = _api("POST", base_url, "/api/v1/computer-use/sessions", token, {})
    sid = body["session_id"]
    try:
        _api("PATCH", base_url, f"/api/v1/computer-use/sessions/{sid}/capabilities",
             token, {"allowed_capabilities": caps, **_scope_payload(cfg)})
    except Exception:
        pass
    return sid, caps


def api_update_capabilities(base_url, token, session_id, caps, cfg=None):
    _api("PATCH", base_url, f"/api/v1/computer-use/sessions/{session_id}/capabilities",
         token, {"allowed_capabilities": caps, **_scope_payload(cfg)})


def api_session_exists(base_url, token, session_id) -> bool:
    try:
        _api("GET", base_url, f"/api/v1/computer-use/sessions/{session_id}", token)
        return True
    except Exception:
        return False


def api_session_status(base_url, token, session_id) -> dict:
    return _api("GET", base_url, f"/api/v1/computer-use/sessions/{session_id}/status", token)


def api_list_sessions(base_url, token) -> list[dict]:
    body = _api("GET", base_url, "/api/v1/computer-use/sessions", token)
    sessions = body.get("sessions") if isinstance(body, dict) else None
    return sessions if isinstance(sessions, list) else []


def pick_waiting_session(base_url, token) -> str:
    sessions = api_list_sessions(base_url, token)
    waiting = [
        s for s in sessions
        if s.get("session_id") and s.get("status") == "waiting_for_bridge"
    ]
    if not waiting:
        return ""
    waiting.sort(key=lambda s: float(s.get("created_at") or 0), reverse=True)
    return str(waiting[0]["session_id"])


def api_list_agents(base_url, token) -> list[dict]:
    body = _api("GET", base_url, "/api/v1/agents/", token)
    agents = body.get("agents") if isinstance(body, dict) else None
    return agents if isinstance(agents, list) else []


def api_agent_zuordnen(base_url, token, session_id, agent_id) -> dict:
    return _api("PATCH", base_url, f"/api/v1/computer-use/sessions/{session_id}/agent", token,
                {"agent_id": agent_id})


def agent_zuordnen(cfg: dict) -> bool:
    """Den in der Bridge gewaehlten Agenten der Sitzung zuordnen — er sieht mit,
    hilft und darf auf diesem Rechner arbeiten. Nach jeder neuen Sitzung erneut,
    sonst waere die Wahl nach einem Neustart des Servers still verloren."""
    aid, url, token, sid = (cfg.get("agent_id"), cfg.get("url"), cfg.get("token"), cfg.get("session"))
    if not (aid and url and token and sid):
        return False
    try:
        ansicht = _api("GET", url, f"/api/v1/computer-use/sessions/{sid}", token)
        if str(ansicht.get("agent_id") or "") != str(aid):
            api_agent_zuordnen(url, token, sid, aid)
            # Das Zuordnen kuerzt auf den Agenten-Standard — die eigenen Freigaben
            # (z. B. Mitsehen) noch einmal anfragen; was der Server nicht erlaubt,
            # lehnt er ab.
            try:
                api_update_capabilities(url, token, sid,
                                        cfg.get("allowed_capabilities", sorted(DEFAULT_CAPABILITIES)), cfg)
            except Exception as e:  # noqa: BLE001
                app_log.info("Freigaben nach Zuordnung teils abgelehnt: %s", e)
        return True
    except Exception as e:  # noqa: BLE001
        app_log.warning("Agent nicht zuordenbar: %s: %s", type(e).__name__, e)
        return False


def agent_waehlen(cfg: dict, agent_id: str | None) -> bool:
    """Auswahl im Hauptfenster: merken und sofort zuordnen."""
    cfg["agent_id"] = agent_id or ""
    save_config(cfg)
    return agent_zuordnen(cfg) if agent_id else True


ENSURE_OK        = "ok"
ENSURE_NEEDS_LOGIN = "needs_login"
ENSURE_ERROR     = "error"

def ensure_session(cfg: dict) -> str:
    """Verify session is still alive; create new one if gone. Returns ENSURE_* constant."""
    token_frisch_halten(cfg)
    url, token = cfg.get("url", ""), cfg.get("token", "")
    if not url or not token:
        if token_erneuern(cfg):
            return ensure_session(cfg)
        return ENSURE_NEEDS_LOGIN
    sid = cfg.get("session", "")
    caps = cfg.get("allowed_capabilities", sorted(DEFAULT_CAPABILITIES))
    try:
        waiting_sid = pick_waiting_session(url, token)
        if waiting_sid and waiting_sid != sid:
            sid = waiting_sid
            cfg["session"] = sid
            save_config(cfg)
    except urllib.error.HTTPError as e:
        if e.code in (401, 403):
            # Erst erneuern — neu anmelden nur, wenn auch das nicht geht.
            cfg["token"] = ""
            if token_erneuern(cfg):
                return ensure_session(cfg)
            cfg["session"] = ""
            save_config(cfg)
            return ENSURE_NEEDS_LOGIN
        app_log.warning("pick_waiting_session HTTP %s: %s", e.code, e.reason)
    except Exception as e:  # noqa: BLE001 — every prior version of this catch
        # swallowed the real reason entirely (TLS error, timeout, DNS, JSON
        # decode) and left "server nicht erreichbar" with zero trace to
        # diagnose from — reported 2026-08-27, log showed nothing at all
        # across a full app restart + reconnect attempt.
        app_log.warning("pick_waiting_session failed: %s: %s", type(e).__name__, e)
    if sid and api_session_exists(url, token, sid):
        try:
            api_update_capabilities(url, token, sid, caps, cfg)
        except Exception as e:  # noqa: BLE001
            app_log.warning("api_update_capabilities failed (non-fatal): %s: %s", type(e).__name__, e)
        agent_zuordnen(cfg)
        return ENSURE_OK
    # Session gone — try to create a fresh one
    try:
        new_sid, _ = api_create_session(url, token, caps, cfg)
        cfg["session"] = new_sid
        save_config(cfg)
        agent_zuordnen(cfg)
        return ENSURE_OK
    except urllib.error.HTTPError as e:
        if e.code in (401, 403):
            cfg["token"] = ""
            cfg["session"] = ""
            save_config(cfg)
            return ENSURE_NEEDS_LOGIN
        app_log.warning("api_create_session HTTP %s: %s", e.code, e.reason)
        return ENSURE_ERROR
    except Exception as e:  # noqa: BLE001 — see note above
        app_log.warning("api_create_session failed: %s: %s", type(e).__name__, e)
        return ENSURE_ERROR


def login_and_prepare(base_url, email, password, caps, requested_session_id=None, cfg=None):
    token, erneuerung = api_login_tokens(base_url, email, password)
    if cfg is not None:
        # Fuer die automatische Erneuerung (token_erneuern) — ohne das stuende
        # die Bridge nach 30 Minuten wieder ohne gueltige Anmeldung da.
        cfg["refresh"] = erneuerung
    if requested_session_id and api_session_exists(base_url, token, requested_session_id):
        session_id = requested_session_id
        try:
            api_update_capabilities(base_url, token, session_id, caps, cfg)
        except Exception:
            pass
    else:
        session_id, _ = api_create_session(base_url, token, caps, cfg)
    return token, session_id


# ── Bridge thread ──────────────────────────────────────────────────────────────

_bridge_thread = None
_bridge_stop   = threading.Event()
_bridge_lock   = threading.Lock()
_status        = "disconnected"


_bridge_cfg: dict | None = None


def start_bridge(cfg):
    global _bridge_thread, _status, _bridge_cfg
    with _bridge_lock:
        if _bridge_thread and _bridge_thread.is_alive():
            return True
        # Dieselbe Konfiguration, die die App haelt — die Erneuerung schreibt das
        # neue Token hinein, und alle API-Aufrufe der App sehen es sofort.
        _bridge_cfg = cfg
        token_frisch_halten(cfg)
        if not cfg.get("url") or not cfg.get("token") or not cfg.get("session"):
            _status = "error: nicht eingerichtet"
            return False
        _bridge_stop.clear()
        _bridge_thread = threading.Thread(
            target=_run_bridge_thread,
            args=(cfg["url"], cfg["token"], cfg["session"]),
            daemon=True)
        _bridge_thread.start()
        _status = "connecting"
        return True


def stop_bridge():
    global _status
    _bridge_stop.set()
    _status = "disconnected"


def is_running():
    return _bridge_thread is not None and _bridge_thread.is_alive()


def _run_bridge_thread(url, token, session_id):
    global _status
    import asyncio
    try:
        app_log.info("Bridge thread starting session=%s url=%s", session_id, url)
        bridge_module = _bridge_module()
        _status = "connecting"
        rejected_notified = [False]

        def _on_state(state: str, detail: str = "") -> None:
            # bridge.run() never returns while the connection is up, so the tray
            # can only learn the real state through this callback. Without it the
            # status window sat on "Verbinde…" forever.
            global _status
            if state == "connected":
                _status = "connected"
            elif state == "rejected":
                low = (detail or "").lower()
                if "session" in low:
                    _status = "error: session abgelaufen"
                elif "unauthor" in low:
                    _status = "error: neu anmelden"
                else:
                    _status = f"error: {detail or 'vom Server abgewiesen'}"
                # Endgueltige Ablehnung: die Bridge beendet ihre Schleife jetzt
                # wirklich (kein stilles Weiterwaehlen mehr). Unter Windows dem
                # Nutzer aktiv Bescheid geben — das Tray-Symbol allein sagt nur
                # "grau", nicht warum.
                if not IS_MAC and not rejected_notified[0]:
                    rejected_notified[0] = True
                    _notify("Verbindung vom Server abgelehnt: "
                            f"{detail or 'Session abgelaufen'}.\n\n"
                            "Bitte im Tray-Menü neu verbinden oder in den "
                            "Einstellungen neu anmelden.")
            else:
                _status = "connecting"

        versuche = 0
        while True:
            abgewiesen = {"unauth": False}
            _vorher = _on_state

            def _beobachten(state: str, detail: str = "", _v=_vorher, _a=abgewiesen) -> None:
                if state == "rejected" and "unauthor" in (detail or "").lower():
                    _a["unauth"] = True
                    if _bridge_cfg is not None and _bridge_cfg.get("refresh"):
                        # Wird gleich still erneuert — keine Meldung „neu anmelden“.
                        return
                _v(state, detail)

            asyncio.run(bridge_module.run(url=url, token=token,
                                          session_id=session_id, stop_event=_bridge_stop,
                                          on_state=_beobachten, on_overlay=_on_overlay,
                                          on_bridge=_on_bridge))
            # Abgelaufenes Zugangstoken (z. B. nach einem Server-Neustart): einmal
            # still erneuern und neu verbinden, statt den Menschen zum Anmelden zu
            # schicken. Mehr als zwei Versuche in Folge heisst: wirklich abgemeldet.
            if (abgewiesen["unauth"] and not _bridge_stop.is_set() and versuche < 2
                    and _bridge_cfg is not None and token_erneuern(_bridge_cfg)):
                versuche += 1
                token = _bridge_cfg["token"]
                _status = "connecting"
                app_log.info("Bridge verbindet mit erneuertem Token neu")
                continue
            break
    except urllib.error.HTTPError as e:
        if e.code in (401, 403):
            _status = "error: neu anmelden"
        else:
            _status = f"error: HTTP {e.code}"
    except Exception as e:
        msg = str(e).strip() or e.__class__.__name__
        if "Unauthorized" in msg or "1008" in msg:
            _status = "error: neu anmelden"
        elif "Session not found" in msg:
            _status = "error: session abgelaufen"
        else:
            _status = f"error: {msg}"
        import traceback
        app_log.error("Bridge error:\n%s", traceback.format_exc())
    finally:
        app_log.info("Bridge thread stopped status=%s", _status)
        if _bridge_stop.is_set():
            _status = "disconnected"


# ── Module-level AppKit handler classes (ObjC class names must be unique) ─────

# State dicts are filled by each dialog before showing the modal.
_setup_state: dict = {}
_perms_state: dict = {}
_status_state: dict = {}


def _appkit_handlers_init():
    """Register ObjC handler classes once at module level."""
    if getattr(_appkit_handlers_init, "_done", False):
        return
    _appkit_handlers_init._done = True

    try:
        from AppKit import NSObject, NSApp, NSOpenPanel
        import urllib.error

        class _SetupHandler(NSObject):
            def cancel_(self, _s):
                NSApp.stopModal()

            def save_(self, _s):
                st = _setup_state
                url_input = st["url_f"].stringValue()
                url, requested_session = normalize_bridge_url(url_input)
                email = st["em_f"].stringValue().strip()
                pw    = st["pw_f"].stringValue()
                if not url or not email or not pw:
                    st["status_lbl"].setStringValue_("⚠  Bitte alle Felder ausfüllen.")
                    return
                st["status_lbl"].setStringValue_("Verbinde…")
                cfg = st["cfg"]

                def _do():
                    try:
                        caps = cfg.get("allowed_capabilities", sorted(DEFAULT_CAPABILITIES))
                        token, sid = login_and_prepare(url, email, pw, caps, requested_session, cfg)
                        st["result_box"][0] = {
                            "url": url, "token": token, "session": sid,
                            "refresh": cfg.get("refresh", ""),
                            # Adresse merken, damit das Feld beim naechsten Mal
                            # vorbelegt ist. Das Passwort NICHT — es wird
                            # nirgends gespeichert.
                            "email": email,
                            "auto_connect": bool(st["auto_chk"].state()),
                            "allowed_capabilities": caps,
                            "allowed_paths": cfg.get("allowed_paths", []),
                        }
                        NSApp.performSelectorOnMainThread_withObject_waitUntilDone_(
                            "stopModal", None, False)
                    except urllib.error.HTTPError:
                        st["status_lbl"].performSelectorOnMainThread_withObject_waitUntilDone_(
                            "setStringValue:", "⚠  Falsche E-Mail oder Passwort.", True)
                    except Exception as e:
                        st["status_lbl"].performSelectorOnMainThread_withObject_waitUntilDone_(
                            "setStringValue:", f"⚠  {e}", True)
                threading.Thread(target=_do, daemon=True).start()

        class _PermsHandler(NSObject):
            def cancel_(self, _s):
                NSApp.stopModal()

            def addPath_(self, _s):
                st = _perms_state
                op = NSOpenPanel.openPanel()
                op.setCanChooseFiles_(False)
                op.setCanChooseDirectories_(True)
                op.setAllowsMultipleSelection_(False)
                op.setPrompt_("Ordner wählen")
                if op.runModal() == 1:
                    p = str(op.URL().path())
                    if p not in st["paths"]:
                        st["paths"].append(p)
                        st["tv"].setString_("\n".join(st["paths"]))

            def delPath_(self, _s):
                st = _perms_state
                lines = st["tv"].string().split("\n")
                if lines:
                    lines.pop()
                    st["paths"][:] = [l for l in lines if l]
                    st["tv"].setString_("\n".join(st["paths"]) if st["paths"] else "(keine Ordner definiert)")

            def save_(self, _s):
                st = _perms_state
                new_caps = [cid for cid, chk in st["cap_checks"].items() if chk.state()]
                cfg = st["cfg"]
                cfg["allowed_capabilities"] = new_caps
                cfg["allowed_paths"] = st["paths"]

                def _lines(view):
                    if view is None:
                        return []
                    return [ln.strip() for ln in str(view.string()).splitlines() if ln.strip()]

                cfg["allowed_apps"] = _lines(st.get("apps_tv"))
                cfg["allowed_domains"] = _lines(st.get("dom_tv"))
                hc = st.get("headless_chk")
                if hc is not None:
                    cfg["browser_headless"] = bool(hc.state())
                save_config(cfg)
                if cfg.get("token") and cfg.get("session") and cfg.get("url") and is_running():
                    st["status_lbl"].setStringValue_("Übertrage an Server…")
                    def _push():
                        try:
                            api_update_capabilities(cfg["url"], cfg["token"], cfg["session"], new_caps, cfg)
                            st["status_lbl"].performSelectorOnMainThread_withObject_waitUntilDone_(
                                "setStringValue:", "✓ Gespeichert", True)
                        except Exception as e:
                            st["status_lbl"].performSelectorOnMainThread_withObject_waitUntilDone_(
                                "setStringValue:", f"Lokal gespeichert (Server: {e})", True)
                        NSApp.performSelectorOnMainThread_withObject_waitUntilDone_(
                            "stopModal", None, False)
                    threading.Thread(target=_push, daemon=True).start()
                else:
                    st["status_lbl"].setStringValue_("✓ Gespeichert")
                    NSApp.stopModal()

        class _StatusHandler(NSObject):
            def close_(self, _s):
                NSApp.stopModal()

        _setup_state["_handler"]  = _SetupHandler.alloc().init()
        _perms_state["_handler"]  = _PermsHandler.alloc().init()
        _status_state["_handler"] = _StatusHandler.alloc().init()
    except Exception:
        pass


# ── Native AppKit dialog helpers ───────────────────────────────────────────────

def _appkit_available():
    try:
        import AppKit  # noqa
        return True
    except ImportError:
        return False


def _make_panel(title, w, h):
    from AppKit import (NSPanel, NSWindowStyleMaskTitled, NSWindowStyleMaskClosable,
                        NSBackingStoreBuffered, NSMakeRect)
    p = NSPanel.alloc().initWithContentRect_styleMask_backing_defer_(
        NSMakeRect(0, 0, w, h),
        NSWindowStyleMaskTitled | NSWindowStyleMaskClosable,
        NSBackingStoreBuffered, False)
    p.setTitle_(title)
    p.center()
    p.setReleasedWhenClosed_(False)
    return p


def _label(cv, text, x, y, w, h, size=13, bold=False, muted=False, color=None):
    from AppKit import NSTextField, NSFont, NSColor
    lbl = NSTextField.labelWithString_(text)
    lbl.setFont_(NSFont.boldSystemFontOfSize_(size) if bold else NSFont.systemFontOfSize_(size))
    lbl.setTextColor_(color or (NSColor.secondaryLabelColor() if muted else NSColor.labelColor()))
    lbl.setFrame_(((x, y), (w, h)))
    lbl.setLineBreakMode_(0)  # NSLineBreakByWordWrapping
    cv.addSubview_(lbl)
    return lbl


def _input(cv, x, y, w, placeholder="", secure=False, value=""):
    from AppKit import NSTextField, NSSecureTextField, NSFont
    cls = NSSecureTextField if secure else NSTextField
    # Runde Felder (NSTextFieldRoundedBezel) mit etwas Luft — die flachen
    # 26-Punkt-Felder waren der zweite Grund, warum die Dialoge „alt“ wirkten.
    f = cls.alloc().initWithFrame_(((x, y - 3), (w, 30)))
    f.setBezelStyle_(1)
    f.cell().setPlaceholderString_(placeholder)
    f.setFont_(NSFont.systemFontOfSize_(14))
    if value:
        f.setStringValue_(value)
    cv.addSubview_(f)
    return f


def _install_edit_menu() -> None:
    """Install a minimal hidden Edit menu so Cmd+V/C/X/A/Z work in modal dialogs.

    An LSUIElement app has no menu bar, so NSApp never translates Cmd+key into
    responder-chain actions. A main menu that's invisible-to-the-user but present
    in NSApplication fixes this for all modal NSWindow dialogs at once.

    Note: this runs BEFORE rumps starts its run loop, so the shared NSApplication
    may not exist yet — the bare `NSApp` global would still be nil here and the
    menu would silently never install. We call `NSApplication.sharedApplication()`
    to obtain (creating if needed) the singleton rumps will reuse, so the menu
    actually sticks.
    """
    if not IS_MAC:
        return
    try:
        from AppKit import NSApplication, NSMenu, NSMenuItem
        app = NSApplication.sharedApplication()
        main_menu = NSMenu.alloc().init()
        app_slot = NSMenuItem.alloc().init()
        main_menu.addItem_(app_slot)

        edit_menu = NSMenu.alloc().initWithTitle_("Edit")
        for title, sel, key in (
            ("Undo",       "undo:",       "z"),
            ("Redo",       "redo:",       "Z"),   # Cmd+Shift+Z
            ("Cut",        "cut:",        "x"),
            ("Copy",       "copy:",       "c"),
            ("Paste",      "paste:",      "v"),
            ("Select All", "selectAll:",  "a"),
        ):
            # target=None -> action travels down the responder chain to the focused field
            edit_menu.addItemWithTitle_action_keyEquivalent_(title, sel, key)
        app_slot.setSubmenu_(edit_menu)
        app.setMainMenu_(main_menu)
    except Exception as e:
        # AppKit unavailable or menu install failed. Don't crash the tray app,
        # but make it traceable — a silent no-op here is what re-breaks Cmd+V.
        print(f"[edit-menu] could not install Edit menu: {e}")


def _button(cv, title, x, y, w=120, h=28, key="", style=1):
    """Knopf der Dialoge — als Pille: die Eingabetaste-Aktion in Markenfarbe,
    alles andere dezent. Ersetzt die grauen System-Knoepfe an allen Stellen."""
    b = _pill_button(cv, title, x, y - 4, w, h=max(h, 34), primary=(key == "\r"))
    if key:
        b.setKeyEquivalent_(key)
    return b


# Die Akzentfarbe der App — genau das Blau des Speech-Buttons, von dem wir aus
# den Screenshots WISSEN, dass es sauber rendert. Ein Ton fuer alle
# Primaeraktionen, damit die Oberflaeche eine erkennbare Farbe hat statt lauter
# grauer System-Knoepfe (der Hauptgrund, warum die App „alt" wirkte).
_ACCENT_RGB = (0.01, 0.47, 0.78)   # Blau des App-Symbols


def _dunkel() -> bool:
    """Dunkles Erscheinungsbild? (Pillen-Fuellungen brauchen je eine eigene Farbe.)"""
    try:
        from AppKit import NSApp
        return "Dark" in str(NSApp.effectiveAppearance().name())
    except Exception:  # noqa: BLE001
        return True


def _app_symbol_bild(groesse: float = 56):
    """Das App-Symbol als NSImage — oder None, wenn es nicht im Bundle liegt."""
    pfad = _asset_pfad("app-symbol-1024.png")
    if not pfad:
        return None
    from AppKit import NSImage
    bild = NSImage.alloc().initWithContentsOfFile_(pfad)
    if bild is not None:
        bild.setSize_((groesse, groesse))
    return bild


def _pill_button(cv, title, x, y, w, h=36, primary=False, danger=False):
    """Moderner, gefuellter Button mit runden Ecken — kein grauer Bezel.

    Baut auf demselben Muster wie der Speech-Button der Interaction Bar
    (borderless + eigene Layer-Fuellung), das nachweislich rendert. ``primary``
    fuellt in Akzentblau mit weisser Schrift; sonst dezent gefuellt mit feiner
    Kante. So entsteht eine klare Hierarchie: EINE Hauptaktion je Flaeche.
    """
    from AppKit import NSButton, NSColor, NSFont, NSMakeRect
    b = NSButton.alloc().initWithFrame_(NSMakeRect(x, y, w, h))
    b.setTitle_(title)
    b.setBordered_(False)
    b.setWantsLayer_(True)
    b.layer().setCornerRadius_(10)
    if primary or danger:
        r, g, bl = (0.90, 0.29, 0.26) if danger else _ACCENT_RGB
        b.layer().setBackgroundColor_(
            NSColor.colorWithSRGBRed_green_blue_alpha_(r, g, bl, 1.0).CGColor())
        b.setContentTintColor_(NSColor.whiteColor())
    else:
        ton = 1.0 if _dunkel() else 0.0
        b.layer().setBackgroundColor_(
            NSColor.colorWithSRGBRed_green_blue_alpha_(ton, ton, ton, 0.09 if ton else 0.05).CGColor())
        b.layer().setBorderWidth_(1)
        b.layer().setBorderColor_(
            NSColor.colorWithSRGBRed_green_blue_alpha_(ton, ton, ton, 0.16 if ton else 0.12).CGColor())
        b.setContentTintColor_(NSColor.labelColor())
    b.setFont_(NSFont.systemFontOfSize_weight_(13, 0.23 if (primary or danger) else 0.0))
    cv.addSubview_(b)
    return b


def _checkbox(cv, title, x, y, w, checked=False):
    from AppKit import NSButton, NSButtonTypeSwitch
    b = NSButton.alloc().initWithFrame_(((x, y), (w, 20)))
    b.setTitle_(title)
    b.setButtonType_(NSButtonTypeSwitch)
    b.setState_(1 if checked else 0)
    cv.addSubview_(b)
    return b


def _separator(cv, x, y, w):
    from AppKit import NSBox, NSBoxSeparator
    box = NSBox.alloc().initWithFrame_(((x, y), (w, 1)))
    box.setBoxType_(NSBoxSeparator)
    cv.addSubview_(box)


def _card(cv, x, y, w, h, radius=16):
    from AppKit import NSColor, NSMakeRect, NSVisualEffectView
    card = NSVisualEffectView.alloc().initWithFrame_(NSMakeRect(x, y, w, h))
    card.setMaterial_(3)
    card.setState_(1)
    card.setWantsLayer_(True)
    card.layer().setCornerRadius_(radius)
    card.layer().setBorderWidth_(1)
    card.layer().setBorderColor_(NSColor.separatorColor().CGColor())
    cv.addSubview_(card)
    return card


def _header(cv, title, subtitle, symbol, x, y, w):
    from AppKit import NSColor, NSImage, NSImageView, NSMakeRect, NSVisualEffectView
    bild = _app_symbol_bild(56)
    if bild is not None:
        # Das echte App-Symbol statt eines Systemsymbols in blauer Kachel.
        ansicht = NSImageView.alloc().initWithFrame_(NSMakeRect(x, y - 6, 56, 56))
        ansicht.setImage_(bild)
        cv.addSubview_(ansicht)
        _label(cv, title, x + 70, y + 24, w - 70, 28, size=22, bold=True)
        _label(cv, subtitle, x + 70, y + 4, w - 70, 18, size=12, muted=True)
        return
    icon_bg = NSVisualEffectView.alloc().initWithFrame_(NSMakeRect(x, y - 4, 52, 52))
    icon_bg.setMaterial_(3)
    icon_bg.setState_(1)
    icon_bg.setWantsLayer_(True)
    icon_bg.layer().setCornerRadius_(14)
    icon_bg.layer().setBackgroundColor_(NSColor.colorWithSRGBRed_green_blue_alpha_(0.08, 0.34, 0.78, 0.18).CGColor())
    cv.addSubview_(icon_bg)

    icon = NSImageView.alloc().initWithFrame_(NSMakeRect(x + 12, y + 8, 28, 28))
    icon.setImage_(NSImage.imageWithSystemSymbolName_accessibilityDescription_(symbol, title))
    icon.setContentTintColor_(NSColor.systemBlueColor())
    cv.addSubview_(icon)

    _label(cv, title, x + 68, y + 26, w - 68, 28, size=22, bold=True)
    _label(cv, subtitle, x + 68, y + 6, w - 68, 18, size=12, muted=True)


def _badge(cv, text, x, y, color_name="blue"):
    from AppKit import NSTextField, NSFont, NSColor
    colors = {
        "green": (0.13, 0.76, 0.37, 1),
        "amber": (1.0, 0.62, 0.04, 1),
        "red": (1.0, 0.27, 0.23, 1),
        "blue": (0.14, 0.48, 1.0, 1),
        "gray": (0.55, 0.55, 0.58, 1),
    }
    r, g, b, a = colors.get(color_name, colors["blue"])
    lbl = NSTextField.labelWithString_(text)
    lbl.setFont_(NSFont.systemFontOfSize_(12))
    lbl.setTextColor_(NSColor.colorWithSRGBRed_green_blue_alpha_(r, g, b, a))
    lbl.setFrame_(((x, y), (220, 20)))
    cv.addSubview_(lbl)
    return lbl


def _risk_badge(cv, risk, x, y):
    from AppKit import NSTextField, NSFont, NSColor
    COLORS = {"gering": (0.13, 0.76, 0.37, 1), "mittel": (1.0, 0.62, 0.04, 1), "hoch": (1.0, 0.27, 0.23, 1)}
    r, g, b, a = COLORS.get(risk, (0.5, 0.5, 0.5, 1))
    lbl = NSTextField.labelWithString_(f"● {risk}")
    lbl.setFont_(NSFont.systemFontOfSize_(10))
    lbl.setTextColor_(NSColor.colorWithSRGBRed_green_blue_alpha_(r, g, b, a))
    lbl.sizeToFit()
    fr = lbl.frame()
    lbl.setFrameOrigin_((x - fr[1][0], y))
    cv.addSubview_(lbl)
    return lbl


# ── Settings Dialog ────────────────────────────────────────────────────────────

def show_setup_dialog(cfg: dict) -> dict | None:
    if not _appkit_available():
        return _show_setup_tkinter(cfg)

    _appkit_handlers_init()
    from AppKit import NSApp, NSMakeRect, NSVisualEffectView

    W, H = 560, 470
    panel = _make_panel("AI Employee", W, H)
    cv = panel.contentView()
    PAD = 32

    try:
        panel.setTitlebarAppearsTransparent_(True)
    except Exception:
        pass

    backdrop = NSVisualEffectView.alloc().initWithFrame_(NSMakeRect(0, 0, W, H))
    backdrop.setBlendingMode_(0)
    backdrop.setMaterial_(3)
    backdrop.setState_(1)
    cv.addSubview_positioned_relativeTo_(backdrop, -1, None)

    _header(
        cv,
        "Bei AI Employee anmelden",
        "Dein Rechner, sicher verbunden mit deinen Agenten.",
        "cpu",
        PAD,
        H - 92,
        W - 2 * PAD,
    )

    card_x, card_y = PAD, 72
    card_w, card_h = W - 2 * PAD, 290
    _card(cv, card_x, card_y, card_w, card_h)

    inner_x = card_x + 22
    input_w = card_w - 44
    _label(cv, "Server-Adresse", inner_x, card_y + card_h - 50, input_w, 16, size=11, bold=True, muted=True)
    url_f  = _input(cv, inner_x, card_y + card_h - 82, input_w, "wss://agents.example.com/ws/computer-use/bridge?session_id=...", value=cfg.get("url",""))

    _label(cv, "E-Mail", inner_x, card_y + card_h - 124, input_w, 16, size=11, bold=True, muted=True)
    # Vorbelegt aus der Konfiguration — die Adresse aendert sich praktisch nie,
    # sie bei jeder Anmeldung neu zu tippen ist reine Schikane. Das Passwort
    # bleibt bewusst leer und wird NICHT gespeichert.
    em_f   = _input(cv, inner_x, card_y + card_h - 156, input_w, "name@example.com",
                    value=cfg.get("email", ""))

    _label(cv, "Passwort", inner_x, card_y + card_h - 198, input_w, 16, size=11, bold=True, muted=True)
    pw_f   = _input(cv, inner_x, card_y + card_h - 230, input_w, "Passwort", secure=True)

    auto_chk = _checkbox(cv, "Beim Start automatisch verbinden", inner_x, card_y + 22, input_w, cfg.get("auto_connect", True))
    status_lbl = _label(cv, "", PAD, 46, W-2*PAD, 18, size=12, muted=True)
    cancel_btn = _button(cv, "Abbrechen", PAD, 20, 110, key="\x1b")
    save_btn = _button(cv, "Anmelden & Verbinden", W-PAD-190, 20, 190, key="\r")

    result_box = [None]
    _setup_state.update(dict(url_f=url_f, em_f=em_f, pw_f=pw_f, auto_chk=auto_chk,
                             status_lbl=status_lbl, result_box=result_box, cfg=cfg))

    h = _setup_state["_handler"]
    cancel_btn.setTarget_(h); cancel_btn.setAction_("cancel:")
    save_btn.setTarget_(h);   save_btn.setAction_("save:")

    # Ein Fenster statt zwei uebereinander: das Hauptfenster tritt zurueck, solange
    # die Anmeldung offen ist, und kommt danach wieder.
    haupt = _main_state.get("panel")
    haupt_sichtbar = bool(haupt is not None and haupt.isVisible())
    if haupt_sichtbar:
        haupt.orderOut_(None)
    NSApp.activateIgnoringOtherApps_(True)
    NSApp.runModalForWindow_(panel)
    panel.close()
    if haupt_sichtbar:
        haupt.makeKeyAndOrderFront_(None)
    return result_box[0]


# ── Permissions Dialog ─────────────────────────────────────────────────────────

def show_permissions_dialog(cfg: dict) -> None:
    if not _appkit_available():
        return _show_permissions_tkinter(cfg)

    _appkit_handlers_init()
    from AppKit import (NSApp, NSScrollView, NSTextView, NSMakeRect, NSFont, NSVisualEffectView)

    # Hoehe folgt der Anzahl der Faehigkeiten (49 px je Zeile) plus dem Block
    # fuer die Freigabelisten. Vorher stand hier eine feste 720 fuer sieben
    # Eintraege — mit zehn waere die unterste Zeile aus dem Fenster gelaufen.
    CAP_ROW_H = 49
    SCOPE_CARD_H = 132
    # +38: eine Extra-Zeile oben in der Faehigkeiten-Karte fuer den
    # Browser-Hintergrund-Schalter.
    HEADLESS_ROW_H = 38
    caps_h = CAP_ROW_H * len(CAPABILITY_META) + 30 + HEADLESS_ROW_H
    W = 600
    H = 340 + caps_h + SCOPE_CARD_H
    panel = _make_panel("AI Employee Berechtigungen", W, H)
    cv = panel.contentView()
    PAD = 32

    try:
        panel.setTitlebarAppearsTransparent_(True)
    except Exception:
        pass

    backdrop = NSVisualEffectView.alloc().initWithFrame_(NSMakeRect(0, 0, W, H))
    backdrop.setBlendingMode_(0)
    backdrop.setMaterial_(3)
    backdrop.setState_(1)
    cv.addSubview_positioned_relativeTo_(backdrop, -1, None)

    _header(
        cv,
        "Berechtigungen",
        "Lege fest, was Agents auf diesem Mac tun duerfen.",
        "hand.raised",
        PAD,
        H - 92,
        W - 2 * PAD,
    )

    cap_checks = {}
    current_caps = set(cfg.get("allowed_capabilities", sorted(DEFAULT_CAPABILITIES)))
    # Von unten aufgebaut: Knoepfe (20) · Status (52) · Ordner-Karte (92+124)
    # · Freigabe-Karte · Faehigkeiten-Karte.
    scope_card_x, scope_card_y = PAD, 232
    scope_card_w = W - 2 * PAD
    caps_x, caps_y, caps_w = PAD, scope_card_y + SCOPE_CARD_H + 14, W - 2 * PAD
    _card(cv, caps_x, caps_y, caps_w, caps_h)
    y = caps_y + caps_h - 18
    for cap in CAPABILITY_META:
        y -= CAP_ROW_H
        chk = _checkbox(cv, cap["label"], caps_x + 20, y+22, 250, cap["id"] in current_caps)
        chk.setFont_(NSFont.boldSystemFontOfSize_(13))
        cap_checks[cap["id"]] = chk
        _label(cv, cap["desc"], caps_x + 40, y+5, 340, 16, size=11, muted=True)
        _risk_badge(cv, cap["risk"], caps_x + caps_w - 20, y+22)

    # Browser im Hintergrund (headless) — wie der ego-lite-Browser: der Agent
    # steuert die Seite ueber den DOM, ohne dass ein Fenster den Vordergrund
    # kapert. Opt-in; greift beim naechsten Browser-Start.
    y -= HEADLESS_ROW_H
    headless_chk = _checkbox(cv, "Browser unsichtbar im Hintergrund betreiben",
                             caps_x + 20, y + 14, caps_w - 40,
                             bool(cfg.get("browser_headless", False)))
    _label(cv, "Ohne sichtbares Fenster (nur mit Browser-Steuerung sinnvoll).",
           caps_x + 40, y - 1, caps_w - 60, 14, size=10, muted=True)

    # Freigabelisten — anders als die Ordnerliste darunter werden diese beiden
    # serverseitig durchgesetzt (computer_use.py:_scope_violation). Leer heisst
    # ausdruecklich "nicht einschraenken".
    _card(cv, scope_card_x, scope_card_y, scope_card_w, SCOPE_CARD_H)
    _label(cv, "Freigaben", scope_card_x + 20, scope_card_y + SCOPE_CARD_H - 30, 140, 18, size=13, bold=True)
    _label(cv, "Leer = keine Einschraenkung. Eine Zeile je Eintrag.",
           scope_card_x + 110, scope_card_y + SCOPE_CARD_H - 28, scope_card_w - 130, 16, size=11, muted=True)

    _field_w = (scope_card_w - 60) // 2
    _label(cv, "Anwendungen", scope_card_x + 20, scope_card_y + SCOPE_CARD_H - 54, _field_w, 14, size=10, muted=True)
    apps_scroll = NSScrollView.alloc().initWithFrame_(
        NSMakeRect(scope_card_x + 20, scope_card_y + 16, _field_w, 56))
    apps_scroll.setHasVerticalScroller_(True)
    apps_scroll.setBorderType_(2)
    apps_tv = NSTextView.alloc().initWithFrame_(NSMakeRect(0, 0, _field_w - 4, 56))
    apps_tv.setFont_(NSFont.userFixedPitchFontOfSize_(11))
    apps_tv.setString_("\n".join(cfg.get("allowed_apps") or []))
    apps_scroll.setDocumentView_(apps_tv)
    cv.addSubview_(apps_scroll)

    _label(cv, "Adressen", scope_card_x + 40 + _field_w, scope_card_y + SCOPE_CARD_H - 54, _field_w, 14, size=10, muted=True)
    dom_scroll = NSScrollView.alloc().initWithFrame_(
        NSMakeRect(scope_card_x + 40 + _field_w, scope_card_y + 16, _field_w, 56))
    dom_scroll.setHasVerticalScroller_(True)
    dom_scroll.setBorderType_(2)
    dom_tv = NSTextView.alloc().initWithFrame_(NSMakeRect(0, 0, _field_w - 4, 56))
    dom_tv.setFont_(NSFont.userFixedPitchFontOfSize_(11))
    dom_tv.setString_("\n".join(cfg.get("allowed_domains") or []))
    dom_scroll.setDocumentView_(dom_tv)
    cv.addSubview_(dom_scroll)

    folder_card_x, folder_card_y = PAD, 92
    folder_card_w, folder_card_h = W - 2 * PAD, 124
    _card(cv, folder_card_x, folder_card_y, folder_card_w, folder_card_h)
    _label(cv, "Ordner-Zugriff", folder_card_x + 20, folder_card_y + folder_card_h - 32, 180, 18, size=13, bold=True)
    # Ehrlich benennen, was durchgesetzt wird: ohne Eintrag sind Shell-Befehle
    # GESPERRT (fail-closed in bridge.py:shell_run), mit Eintrag muss das
    # Arbeitsverzeichnis darin liegen. "Beschraenkt auf" stand hier vorher —
    # eine Zusage, hinter der gar keine Implementierung lag.
    _label(cv, "Startordner für Shell-Befehle. Ohne Eintrag sind Shell-Befehle gesperrt.",
           folder_card_x + 150, folder_card_y + folder_card_h - 30, folder_card_w - 170, 16, size=11, muted=True)

    paths = list(cfg.get("allowed_paths", []))
    list_x, list_y, list_w, list_h = folder_card_x + 20, folder_card_y + 18, folder_card_w - 170, 62

    scroll = NSScrollView.alloc().initWithFrame_(NSMakeRect(list_x, list_y, list_w, list_h))
    scroll.setHasVerticalScroller_(True)
    scroll.setBorderType_(2)
    tv = NSTextView.alloc().initWithFrame_(NSMakeRect(0, 0, list_w - 4, list_h))
    tv.setEditable_(False)
    tv.setFont_(NSFont.userFixedPitchFontOfSize_(12))
    tv.setString_("\n".join(paths) if paths else "(keine Ordner definiert)")
    scroll.setDocumentView_(tv)
    cv.addSubview_(scroll)

    add_btn = _button(cv, "+ Hinzufügen", folder_card_x + folder_card_w - 132, folder_card_y + 56, 112)
    del_btn = _button(cv, "Entfernen", folder_card_x + folder_card_w - 132, folder_card_y + 22, 112)

    status_lbl = _label(cv, "", PAD, 52, W-2*PAD-140, 16, size=11, muted=True)
    cancel_btn = _button(cv, "Abbrechen", PAD, 20, 110, key="\x1b")
    save_btn   = _button(cv, "Speichern", W-PAD-120, 20, 120, key="\r")

    _perms_state.update(dict(cap_checks=cap_checks, paths=paths, tv=tv,
                             apps_tv=apps_tv, dom_tv=dom_tv, headless_chk=headless_chk,
                             status_lbl=status_lbl, cfg=cfg))

    h = _perms_state["_handler"]
    cancel_btn.setTarget_(h); cancel_btn.setAction_("cancel:")
    add_btn.setTarget_(h);    add_btn.setAction_("addPath:")
    del_btn.setTarget_(h);    del_btn.setAction_("delPath:")
    save_btn.setTarget_(h);   save_btn.setAction_("save:")

    NSApp.activateIgnoringOtherApps_(True)
    NSApp.runModalForWindow_(panel)
    panel.close()


# ── Status Window ──────────────────────────────────────────────────────────────

def show_status_window(cfg: dict) -> None:
    if not _appkit_available():
        return _show_status_tkinter(cfg)

    _appkit_handlers_init()
    from AppKit import NSApp, NSColor, NSMakeRect, NSVisualEffectView

    W = 620
    # Hoeher (470 -> 560): mit allen Faehigkeiten + Ordnerpfad lief die unterste
    # Zeile aus der Karte heraus (im Screenshot abgeschnitten).
    H = 560
    PAD = 34
    COL = 126
    VAL_X = PAD + COL + 14
    VAL_W = W - VAL_X - PAD

    def _short_path(p: str, keep: int = 46) -> str:
        """Lange Pfade in der Mitte kuerzen, damit sie in die Zeile passen
        statt am Kartenrand abgeschnitten zu werden."""
        p = str(p)
        if len(p) <= keep:
            return p
        head = keep // 2 - 2
        return p[:head] + "…" + p[-(keep - head - 1):]

    server_state = {}
    server_error = ""
    try:
        if cfg.get("url") and cfg.get("token") and cfg.get("session"):
            server_state = api_session_status(cfg["url"], cfg["token"], cfg["session"])
    except urllib.error.HTTPError as e:
        if e.code in (401, 403):
            server_error = "Neu anmelden"
        else:
            server_error = f"Serverfehler HTTP {e.code}"
    except Exception as e:
        server_error = str(e).strip() or e.__class__.__name__
        server_state = {}

    state = _status
    server_connected = bool(server_state.get("bridge_connected"))
    if server_connected:
        dot_color, state_text, badge_color = (0.13, 0.76, 0.37, 1), "Verbunden", "green"
    elif state == "connecting":
        dot_color, state_text, badge_color = (1.0, 0.62, 0.04, 1), "Verbinde mit Server", "amber"
    elif server_error:
        dot_color, state_text, badge_color = (0.94, 0.27, 0.27, 1), server_error, "red"
    else:
        text = state.replace("error: ", "")
        color = (0.94, 0.27, 0.27, 1) if state.startswith("error:") else (0.6, 0.6, 0.6, 1)
        dot_color, state_text, badge_color = color, text, "red" if state.startswith("error:") else "gray"
    dot_col = NSColor.colorWithSRGBRed_green_blue_alpha_(*dot_color)

    caps = cfg.get("allowed_capabilities", [])
    cap_map = {c["id"]: c["label"] for c in CAPABILITY_META}
    caps_str = ", ".join(cap_map.get(c, c) for c in caps) if caps else "Keine"
    paths = cfg.get("allowed_paths", [])

    panel = _make_panel("AI Employee Status", W, H)
    cv = panel.contentView()
    try:
        panel.setTitlebarAppearsTransparent_(True)
    except Exception:
        pass

    backdrop = NSVisualEffectView.alloc().initWithFrame_(NSMakeRect(0, 0, W, H))
    backdrop.setBlendingMode_(0)
    backdrop.setMaterial_(3)
    backdrop.setState_(1)
    cv.addSubview_positioned_relativeTo_(backdrop, -1, None)

    _header(
        cv,
        "Bridge Status",
        "Live-Verbindung zwischen diesem Mac und AI Employee.",
        "network",
        PAD,
        H - 94,
        W - 2 * PAD,
    )

    card_x, card_y = PAD, 76
    card_w, card_h = W - 2 * PAD, 380
    _card(cv, card_x, card_y, card_w, card_h)

    _badge(cv, f"● {state_text}", card_x + 22, card_y + card_h - 42, badge_color)

    y = card_y + card_h - 66

    def row(lbl, val, h=34, val_color=None):
        nonlocal y
        y -= h
        _label(cv, lbl, card_x + 24, y + 7, COL, 18, size=12, muted=True)
        _label(cv, val, VAL_X, y + 7, VAL_W, max(18, h - 8), size=12, color=val_color)

    row("Verbindung", state_text, val_color=dot_col)
    row("Version",    f"Bridge v{BRIDGE_VERSION}")
    row("Server",     cfg.get("url") or "—")
    row("Session",    (cfg.get("session") or "—")[:16])
    row("Erlaubt",    caps_str, h=58)

    if paths:
        y -= 4
        _separator(cv, card_x + 20, y, card_w - 40)
        row("Ordner", "\n".join(_short_path(p) for p in paths),
            h=min(90, 18 * len(paths) + 20))

    close_btn = _pill_button(cv, "Schliessen", W - PAD - 120, 20, 120, primary=True)

    h = _status_state["_handler"]
    close_btn.setTarget_(h)
    close_btn.setAction_("close:")

    NSApp.activateIgnoringOtherApps_(True)
    NSApp.runModalForWindow_(panel)
    panel.close()


# ── Hauptfenster (die Bridge ist eine App, kein Tray-Anhaengsel) ──────────────
#
# Bis v1.239.0 bestand die Oberflaeche aus einem Tray-Menue und vier einzelnen
# Modal-Dialogen — zum Einrichten ok, zum ARBEITEN nicht: kein Ort, an dem man
# Status, Freigaben und Voice zusammen sieht. Das Hauptfenster ist dieser Ort;
# das Tray bleibt fuer den Hintergrundbetrieb.

_main_state: dict = {}


def _main_status_parts() -> tuple[str, str]:
    """(Text, Farbname) fuer den Verbindungs-Badge — EINE Ableitung fuer beide
    Plattformen, damit Hauptfenster und Tray nie zweierlei behaupten."""
    if is_running() and _status == "connected":
        return "Verbunden", "green"
    if is_running() or _status == "connecting":
        return "Verbinde…", "amber"
    if _status.startswith("error:"):
        return _status.replace("error: ", ""), "red"
    return "Nicht verbunden", "gray"


def hauptfenster_inhalt(cfg: dict, status: tuple[str, str], beschaeftigt: bool,
                        sieht_mit: bool, regeln: int | None = None) -> dict:
    """Was das Hauptfenster zeigt — fuer Mac und Windows gleich, ohne Fenster testbar.

    ``status`` ist ``_main_status_parts()``, ``beschaeftigt`` = verbunden oder
    beim Verbinden (dann heisst der Hauptknopf „Trennen“)."""
    if not cfg.get("token"):
        # Ohne Anmeldung ist der Rechner fuer den Server gar nicht da — das muss
        # man sofort sehen, nicht als unauffaelliges „Nicht verbunden“.
        status, beschaeftigt = ("Nicht angemeldet", "amber"), False
    url = str(cfg.get("url") or "")
    host = urllib.parse.urlparse(url).hostname or url or "kein Server eingetragen"
    teile = [host]
    if cfg.get("session"):
        teile.append(f"Sitzung {str(cfg['session'])[:8]}")
    teile.append(f"Bridge {BRIDGE_VERSION}")
    caps = list(cfg.get("allowed_capabilities") or [])
    darf_sehen = "observe" in caps
    if not darf_sehen:
        sehen = "Aus — unter Berechtigungen „Mitsehen“ einschalten."
    elif sieht_mit:
        sehen = "Sieht mit" + (f" · {regeln} Regel{'n' if regeln != 1 else ''}" if regeln is not None else "")
        sehen += " · Mitdenken " + ("an" if cfg.get("mitdenken") else "aus")
    else:
        sehen = "Freigegeben — wirkt, sobald die Bridge verbunden ist."
    return {
        "status": status[0],
        "farbe": status[1],
        "meta": " · ".join(teile),
        "aktion": ("Anmelden" if not cfg.get("token") else "Trennen" if beschaeftigt else "Verbinden"),
        "sehen": sehen,
        "sieht_mit": bool(darf_sehen and sieht_mit),
        "mitdenken_knopf": "Mitdenken ausschalten" if cfg.get("mitdenken") else "Mitdenken einschalten",
        "freigaben": f"{len(caps)} Freigabe{'n' if len(caps) != 1 else ''}",
    }


def _regel_anzahl() -> int | None:
    b = _overlay.get("bridge")
    observer = getattr(b, "observer", None)
    return len(observer.regeln) if observer is not None and observer.beobachten else None


_STATUS_FARBEN = {"green": (0.13, 0.76, 0.37), "amber": (1.0, 0.62, 0.04),
                  "red": (1.0, 0.27, 0.23), "gray": (0.55, 0.55, 0.58)}
_AIEMainHandler = None


def show_main_window(cfg: dict) -> None:
    """Das macOS-Hauptfenster — nicht-modal, einmalig, live aktualisiert."""
    if not _appkit_available():
        return _show_main_window_ctk(cfg)

    from AppKit import NSApp, NSFont, NSImage, NSMakeRect, NSObject, NSVisualEffectView

    existing = _main_state.get("panel")
    if existing is not None:
        try:
            NSApp.activateIgnoringOtherApps_(True)
            existing.orderFrontRegardless()
            existing.makeKeyAndOrderFront_(None)
            _main_window_refresh(cfg)
            return
        except Exception:  # noqa: BLE001 — Fenster kaputt: neu bauen
            _main_state.clear()

    _appkit_handlers_init()
    W, H = 620, 480
    PAD = 28
    panel = _make_panel("AI Employee Bridge", W, H)
    cv = panel.contentView()
    try:
        panel.setTitlebarAppearsTransparent_(True)
    except Exception:  # noqa: BLE001
        pass

    backdrop = NSVisualEffectView.alloc().initWithFrame_(NSMakeRect(0, 0, W, H))
    backdrop.setBlendingMode_(0)
    backdrop.setMaterial_(3)
    backdrop.setState_(1)
    cv.addSubview_positioned_relativeTo_(backdrop, -1, None)

    _header(cv, "AI Employee Bridge", "Dein Rechner, sicher verbunden mit deinen Agenten.",
            "cpu", PAD, H - 96, W - 2 * PAD)

    card_x, card_w = PAD, W - 2 * PAD
    # ── Verbindung: grosser Status, eine Hauptaktion ─────────────────────
    conn_h = 112
    conn_y = H - 128 - conn_h
    _card(cv, card_x, conn_y, card_w, conn_h)
    _label(cv, "VERBINDUNG", card_x + 22, conn_y + conn_h - 30, 200, 14, size=10, bold=True, muted=True)
    status_lbl = _label(cv, "", card_x + 22, conn_y + conn_h - 62, card_w - 220, 26, size=19, bold=True)
    meta_lbl = _label(cv, "", card_x + 22, conn_y + 20, card_w - 220, 18, size=12, muted=True)
    aktion_btn = _pill_button(cv, "Verbinden", card_x + card_w - 172, conn_y + (conn_h - 38) / 2, 150, h=38,
                              primary=True)

    # ── Mitsehen ─────────────────────────────────────────────────────────
    seh_h = 118
    seh_y = conn_y - 16 - seh_h
    _card(cv, card_x, seh_y, card_w, seh_h)
    _label(cv, "MITSEHEN", card_x + 22, seh_y + seh_h - 30, 200, 14, size=10, bold=True, muted=True)
    sehen_lbl = _label(cv, "", card_x + 22, seh_y + seh_h - 58, card_w - 44, 20, size=13)
    # WELCHER Agent mitsieht, hilft und auf diesem Rechner arbeiten darf.
    from AppKit import NSPopUpButton
    agent_popup = NSPopUpButton.alloc().initWithFrame_pullsDown_(NSMakeRect(card_x + 18, seh_y + 18, 190, 30), False)
    agent_popup.addItemWithTitle_("Agent wählen …")
    cv.addSubview_(agent_popup)
    mitdenken_btn = _pill_button(cv, "Mitdenken einschalten", card_x + 220, seh_y + 16, 180)
    perms_btn = _pill_button(cv, "Berechtigungen…", card_x + 410, seh_y + 16, 134)
    freigaben_lbl = _label(cv, "", 0, 0, 1, 1, size=1, muted=True)
    freigaben_lbl.setHidden_(True)

    # ── Weitere Aktionen ─────────────────────────────────────────────────
    zeile_y = seh_y - 16 - 34
    knoepfe = []
    x = card_x
    for titel, symbol, breite in (("Sprechen", "mic.fill", 130), ("Einstellungen", "gearshape", 150),
                                  ("Im Browser öffnen", "safari", 180)):
        b = _pill_button(cv, titel, x, zeile_y, breite)
        try:
            b.setImage_(NSImage.imageWithSystemSymbolName_accessibilityDescription_(symbol, titel))
            b.setImagePosition_(2)          # Bild links vom Text
            b.setImageHugsTitle_(True)
        except Exception:  # noqa: BLE001 — aeltere macOS-Versionen: nur Text
            pass
        knoepfe.append(b)
        x += breite + 12

    _label(cv, "Die Bridge läuft im Hintergrund weiter, wenn du dieses Fenster schließt.",
           PAD, 18, W - 2 * PAD, 16, size=11, muted=True)

    global _AIEMainHandler
    if _AIEMainHandler is None:
        class AIEMainHandler(NSObject):
            def doaktion_(self, _s):
                c = _main_state.get("cfg") or {}
                if not c.get("token"):
                    cb = _main_state.get("settings")
                    if cb:
                        cb()
                    return
                if is_running() or _status == "connecting":
                    stop_bridge()
                else:
                    cb = _main_state.get("connect")
                    if cb:
                        cb()
                _main_window_refresh(_main_state.get("cfg") or {})

            def dowahl_(self, sender):
                c = _main_state.get("cfg") or {}
                agenten = _main_state.get("agenten") or []
                idx = int(sender.indexOfSelectedItem()) - 1     # Eintrag 0 = „Agent wählen …“
                aid = agenten[idx][0] if 0 <= idx < len(agenten) else ""
                threading.Thread(target=agent_waehlen, args=(c, aid), daemon=True).start()

            def domitdenken_(self, _s):
                c = _main_state.get("cfg") or {}
                mitdenken_umschalten(c)
                _main_window_refresh(c)

            def doperms_(self, _s):
                c = _main_state.get("cfg") or {}
                show_permissions_dialog(c)
                c.update(load_config())
                _main_window_refresh(c)

            def dovoice_(self, _s):
                show_interaction_bar(_main_state.get("cfg") or {})

            def dosettings_(self, _s):
                cb = _main_state.get("settings")
                if cb:
                    cb()

            def doweb_(self, _s):
                url = (_main_state.get("cfg") or {}).get("url")
                if url:
                    webbrowser.open(url)

        _AIEMainHandler = AIEMainHandler
    handler = _AIEMainHandler.alloc().init()
    agent_popup.setTarget_(handler)
    agent_popup.setAction_("dowahl:")
    for btn, action in ((aktion_btn, "doaktion:"), (mitdenken_btn, "domitdenken:"), (perms_btn, "doperms:"),
                        (knoepfe[0], "dovoice:"), (knoepfe[1], "dosettings:"), (knoepfe[2], "doweb:")):
        btn.setTarget_(handler)
        btn.setAction_(action)

    _main_state.update({
        "panel": panel, "handler": handler, "cfg": cfg, "status_lbl": status_lbl, "meta_lbl": meta_lbl,
        "aktion_btn": aktion_btn, "sehen_lbl": sehen_lbl, "mitdenken_btn": mitdenken_btn,
        "freigaben_lbl": freigaben_lbl, "agent_popup": agent_popup,
    })
    NSApp.activateIgnoringOtherApps_(True)
    panel.orderFrontRegardless()
    panel.makeKeyAndOrderFront_(None)
    # Erst NACH dem Anzeigen fuellen — die Aktualisierung arbeitet nur an
    # sichtbaren Fenstern; sonst stuende bis zum naechsten Takt alles leer.
    _main_window_refresh(cfg)


_agenten_zuletzt = [0.0]


def agenten_laden(cfg: dict) -> list[tuple[str, str]]:
    """(id, Name) der eigenen Agenten — fuer die Auswahl im Hauptfenster.
    Hoechstens alle 30 s vom Server; dazwischen der gemerkte Stand."""
    import time as _time
    if not (cfg.get("url") and cfg.get("token")):
        return []
    if _time.time() - _agenten_zuletzt[0] < 30 and _main_state.get("agenten") is not None:
        return _main_state["agenten"]
    _agenten_zuletzt[0] = _time.time()
    try:
        liste = [(str(a.get("id") or ""), str(a.get("name") or a.get("id") or "")[:40])
                 for a in api_list_agents(cfg["url"], cfg["token"])]
    except Exception as e:  # noqa: BLE001
        app_log.info("Agentenliste nicht ladbar: %s", e)
        return _main_state.get("agenten") or []
    _main_state["agenten"] = [x for x in liste if x[0]]
    return _main_state["agenten"]


def agenten_titel(agenten: list[tuple[str, str]]) -> list[str]:
    """Eindeutige Anzeigenamen — Auswahllisten verschlucken doppelte Titel still,
    dann zeigte die Liste 3 von 4 Agenten und die Zuordnung verrutschte."""
    titel: list[str] = []
    for aid, name in agenten:
        t = name or aid[:8]
        if t in titel:
            t = f"{t} ({aid[:6]})"
        titel.append(t)
    return titel


def _agenten_im_popup(cfg: dict) -> None:
    """Auswahlliste des Mac-Fensters nachziehen (Laden im Hintergrund)."""
    popup = _main_state.get("agent_popup")
    if popup is None:
        return
    agenten = _main_state.get("agenten")
    if not agenten:
        # Laedt im Hintergrund (gedrosselt) — z. B. sobald nach der Anmeldung ein Token da ist.
        threading.Thread(target=agenten_laden, args=(cfg,), daemon=True).start()
    agenten = agenten or []
    titel = ["Agent wählen …"] + agenten_titel(agenten)
    if [str(popup.itemTitleAtIndex_(i)) for i in range(popup.numberOfItems())] != titel:
        popup.removeAllItems()
        popup.addItemsWithTitles_(titel)
    gewaehlt = str(cfg.get("agent_id") or "")
    ids = [aid for aid, _ in agenten]
    popup.selectItemAtIndex_(ids.index(gewaehlt) + 1 if gewaehlt in ids else 0)


def _main_window_refresh(cfg: dict) -> None:
    """Hauptfenster an den echten Zustand angleichen. Vom rumps-Timer alle 3 s
    aufgerufen — nur wenn das Fenster offen ist."""
    panel = _main_state.get("panel")
    if panel is None:
        return
    try:
        if not panel.isVisible():
            return
        from AppKit import NSColor
        _main_state["cfg"] = cfg
        inhalt = hauptfenster_inhalt(cfg, _main_status_parts(), is_running() or _status == "connecting",
                                     _sieht_mit(), _regel_anzahl())
        r, g, b = _STATUS_FARBEN.get(inhalt["farbe"], _STATUS_FARBEN["gray"])
        lbl = _main_state["status_lbl"]
        lbl.setStringValue_(f"● {inhalt['status']}")
        lbl.setTextColor_(NSColor.colorWithSRGBRed_green_blue_alpha_(r, g, b, 1))
        _main_state["meta_lbl"].setStringValue_(inhalt["meta"])
        _agenten_im_popup(cfg)
        knopf = _main_state["aktion_btn"]
        if knopf.title() != inhalt["aktion"]:
            knopf.setTitle_(inhalt["aktion"])
            # Verbinden/Anmelden = Hauptaktion in Markenfarbe, Trennen = dezent.
            if inhalt["aktion"] in ("Verbinden", "Anmelden"):
                knopf.layer().setBackgroundColor_(NSColor.colorWithSRGBRed_green_blue_alpha_(*_ACCENT_RGB, 1).CGColor())
                knopf.setContentTintColor_(NSColor.whiteColor())
                knopf.layer().setBorderWidth_(0)
            else:
                ton = 1.0 if _dunkel() else 0.0
                knopf.layer().setBackgroundColor_(NSColor.colorWithSRGBRed_green_blue_alpha_(ton, ton, ton, 0.09).CGColor())
                knopf.layer().setBorderWidth_(1)
                knopf.layer().setBorderColor_(NSColor.colorWithSRGBRed_green_blue_alpha_(ton, ton, ton, 0.16).CGColor())
                knopf.setContentTintColor_(NSColor.labelColor())
        sehen = _main_state["sehen_lbl"]
        sehen.setStringValue_(inhalt["sehen"])
        sehen.setTextColor_(NSColor.colorWithSRGBRed_green_blue_alpha_(0.31, 0.9, 0.9, 1)
                            if inhalt["sieht_mit"] else NSColor.labelColor())
        _main_state["mitdenken_btn"].setTitle_(inhalt["mitdenken_knopf"])
        _main_state["freigaben_lbl"].setStringValue_(inhalt["freigaben"])
    except Exception:  # noqa: BLE001 — ein kaputtes Fenster darf das Tray nicht mitreissen
        app_log.debug("Hauptfenster nicht aktualisierbar", exc_info=True)
        # Nur das Fenster vergessen — die Rueckrufe connect/settings gehoeren der App.
        _main_state.pop("panel", None)


_CTK_MARKE = "#0378c7"          # Blau des App-Symbols
_CTK_KARTE = ("#f3f4f6", "#1d2129")
_CTK_STATUS = {"green": "#22c55e", "amber": "#f59e0b", "red": "#ef4444", "gray": "#8b919c"}


def _ctk_fenster_symbol(root) -> None:
    """Fenster- und Taskleistensymbol unter Windows."""
    pfad = _asset_pfad("app-symbol.ico")
    if pfad and IS_WIN:
        try:
            root.iconbitmap(pfad)
        except Exception:  # noqa: BLE001
            pass


def _ctk_kopf(ctk, eltern, titel: str, unterzeile: str):
    """Kopf mit echtem App-Symbol — dasselbe Bild wie im Mac-Fenster."""
    kopf = ctk.CTkFrame(eltern, fg_color="transparent")
    pfad = _asset_pfad("app-symbol-1024.png")
    if pfad:
        try:
            from PIL import Image
            bild = ctk.CTkImage(light_image=Image.open(pfad), dark_image=Image.open(pfad), size=(56, 56))
            ctk.CTkLabel(kopf, image=bild, text="").pack(side="left", padx=(0, 14))
        except Exception:  # noqa: BLE001
            pass
    texte = ctk.CTkFrame(kopf, fg_color="transparent")
    texte.pack(side="left")
    ctk.CTkLabel(texte, text=titel, font=ctk.CTkFont(size=22, weight="bold"), anchor="w").pack(anchor="w")
    ctk.CTkLabel(texte, text=unterzeile, text_color="gray55", font=ctk.CTkFont(size=12), anchor="w").pack(anchor="w")
    return kopf


def _show_main_window_ctk(cfg: dict) -> None:
    """Das Windows-Hauptfenster — derselbe Aufbau wie auf dem Mac
    (``hauptfenster_inhalt`` liefert fuer beide den Inhalt)."""
    if not _ctk_available():
        return _show_status_plain_tkinter(cfg)
    ctk = _ctk_setup()

    root = ctk.CTk()
    root.title("AI Employee Bridge")
    root.geometry("640x520")
    root.minsize(600, 500)
    _ctk_fenster_symbol(root)

    _ctk_kopf(ctk, root, "AI Employee Bridge", "Dein Rechner, sicher verbunden mit deinen Agenten.").pack(
        fill="x", padx=28, pady=(24, 16))

    def karte(titel):
        k = ctk.CTkFrame(root, fg_color=_CTK_KARTE, corner_radius=16)
        k.pack(fill="x", padx=28, pady=(0, 14))
        ctk.CTkLabel(k, text=titel, text_color="gray55", font=ctk.CTkFont(size=10, weight="bold")).pack(
            anchor="w", padx=20, pady=(14, 0))
        return k

    # ── Verbindung ───────────────────────────────────────────────────────
    k1 = karte("VERBINDUNG")
    zeile = ctk.CTkFrame(k1, fg_color="transparent")
    zeile.pack(fill="x", padx=20, pady=(2, 16))
    links = ctk.CTkFrame(zeile, fg_color="transparent")
    links.pack(side="left", fill="x", expand=True)
    status_lbl = ctk.CTkLabel(links, text="", font=ctk.CTkFont(size=19, weight="bold"), anchor="w")
    status_lbl.pack(anchor="w")
    meta_lbl = ctk.CTkLabel(links, text="", text_color="gray55", font=ctk.CTkFont(size=12), anchor="w")
    meta_lbl.pack(anchor="w")

    def _aktion():
        if not cfg.get("token"):
            cb = _main_state.get("settings")
            if cb:
                cb()
            return
        if is_running() or _status == "connecting":
            stop_bridge()
        else:
            cb = _main_state.get("connect")
            if cb:
                cb()

    aktion_btn = ctk.CTkButton(zeile, text="Verbinden", width=150, height=38, corner_radius=12,
                               fg_color=_CTK_MARKE, font=ctk.CTkFont(size=13, weight="bold"), command=_aktion)
    aktion_btn.pack(side="right")

    # ── Mitsehen ─────────────────────────────────────────────────────────
    k2 = karte("MITSEHEN")
    sehen_lbl = ctk.CTkLabel(k2, text="", font=ctk.CTkFont(size=13), anchor="w")
    sehen_lbl.pack(anchor="w", padx=20, pady=(2, 8))
    reihe = ctk.CTkFrame(k2, fg_color="transparent")
    reihe.pack(fill="x", padx=20, pady=(0, 16))
    zweit = dict(height=34, corner_radius=12, fg_color=("gray85", "#2a2f39"), hover_color=("gray78", "#353b47"),
                 text_color=("gray10", "gray90"), border_width=1, border_color=("gray75", "#3a404c"))

    def _mitdenken():
        mitdenken_umschalten(cfg)
        _tick(einmal=True)

    # WELCHER Agent mitsieht, hilft und auf diesem Rechner arbeiten darf.
    agent_wahl = {"titel": [], "ids": []}

    def _gewaehlt(titel):
        if titel in agent_wahl["titel"]:
            aid = agent_wahl["ids"][agent_wahl["titel"].index(titel)]
            threading.Thread(target=agent_waehlen, args=(cfg, aid), daemon=True).start()

    agent_menue = ctk.CTkOptionMenu(reihe, values=["Agent wählen …"], width=180, height=34, corner_radius=12,
                                    command=_gewaehlt, fg_color=("gray85", "#2a2f39"),
                                    button_color=("gray78", "#353b47"), text_color=("gray10", "gray90"))
    agent_menue.pack(side="left", padx=(0, 10))
    mitdenken_btn = ctk.CTkButton(reihe, text="Mitdenken einschalten", width=180, command=_mitdenken, **zweit)
    mitdenken_btn.pack(side="left")
    ctk.CTkButton(reihe, text="Berechtigungen…", width=150, command=lambda: show_permissions_dialog(cfg),
                  **zweit).pack(side="left", padx=10)
    freigaben_lbl = ctk.CTkLabel(reihe, text="", text_color="gray55", font=ctk.CTkFont(size=12))
    freigaben_lbl.pack(side="left", padx=6)

    # ── Weitere Aktionen ─────────────────────────────────────────────────
    unten = ctk.CTkFrame(root, fg_color="transparent")
    unten.pack(fill="x", padx=28, pady=(2, 0))

    def _einstellungen():
        cb = _main_state.get("settings")
        if cb:
            cb()

    for titel, befehl, breite in (("Sprechen", lambda: show_interaction_bar(cfg), 130),
                                  ("Einstellungen", _einstellungen, 150),
                                  ("Im Browser öffnen", lambda: cfg.get("url") and webbrowser.open(cfg["url"]), 170)):
        ctk.CTkButton(unten, text=titel, width=breite, command=befehl, **zweit).pack(side="left", padx=(0, 10))

    ctk.CTkLabel(root, text="Die Bridge läuft im Hintergrund weiter, wenn du dieses Fenster schließt.",
                 text_color="gray50", font=ctk.CTkFont(size=11)).pack(anchor="w", padx=28, pady=(14, 0))

    def _tick(einmal: bool = False):
        inhalt = hauptfenster_inhalt(cfg, _main_status_parts(), is_running() or _status == "connecting",
                                     _sieht_mit(), _regel_anzahl())
        status_lbl.configure(text=f"● {inhalt['status']}", text_color=_CTK_STATUS.get(inhalt["farbe"], "#8b919c"))
        meta_lbl.configure(text=inhalt["meta"])
        if inhalt["aktion"] in ("Verbinden", "Anmelden"):
            aktion_btn.configure(text=inhalt["aktion"], fg_color=_CTK_MARKE, text_color="white", border_width=0)
        else:
            aktion_btn.configure(text="Trennen", fg_color=("gray85", "#2a2f39"), text_color=("gray10", "gray90"),
                                 border_width=1)
        sehen_lbl.configure(text=inhalt["sehen"], text_color="#4ee6e6" if inhalt["sieht_mit"] else ("gray10", "gray90"))
        mitdenken_btn.configure(text=inhalt["mitdenken_knopf"])
        freigaben_lbl.configure(text="")
        agenten = _main_state.get("agenten") or []
        if not agenten:
            threading.Thread(target=agenten_laden, args=(cfg,), daemon=True).start()
        titel = agenten_titel(agenten)
        if titel != agent_wahl["titel"]:
            agent_wahl["titel"], agent_wahl["ids"] = titel, [a for a, _ in agenten]
            agent_menue.configure(values=titel or ["Agent wählen …"])
        ids = agent_wahl["ids"]
        gewaehlt = str(cfg.get("agent_id") or "")
        agent_menue.set(titel[ids.index(gewaehlt)] if gewaehlt in ids else "Agent wählen …")
        if not einmal:
            root.after(2000, _tick)

    _tick()
    root.mainloop()


# ── Interaction Bar / Voice Mode ─────────────────────────────────────────────

_voice_state: dict = {}


def _voice_ws_url(base_url: str, agent_id: str, ticket: str) -> str:
    ws_base = base_url.rstrip("/").replace("https://", "wss://").replace("http://", "ws://")
    q = urllib.parse.urlencode({"ticket": ticket})
    return f"{ws_base}/api/v1/ws/agents/{agent_id}/voice?{q}"


_MCI_ALIAS = "aiemp_voice"


def _play_media_file(path: str):
    """Eine Audiodatei abspielen — ohne sichtbares Fenster, auf BEIDEN Systemen.

    macOS: ``afplay``. Windows: MCI aus winmm (Bordmittel, spielt auch MP3) —
    ``os.startfile`` wuerde bei jeder Antwort den Standard-Player aufreissen.
    Vorher war hier ``afplay`` fest verdrahtet: die gesamte Sprachausgabe der
    Interaction Bar existierte nur auf dem Mac.
    """
    if IS_MAC:
        return subprocess.Popen(["afplay", path])
    if IS_WIN:
        try:
            import ctypes
            winmm = ctypes.windll.winmm
            winmm.mciSendStringW(f"close {_MCI_ALIAS}", None, 0, None)
            winmm.mciSendStringW(
                f'open "{path}" type mpegvideo alias {_MCI_ALIAS}', None, 0, None)
            winmm.mciSendStringW(f"play {_MCI_ALIAS}", None, 0, None)
        except Exception:  # noqa: BLE001 — kein Ton ist kein Absturzgrund
            pass
    return None


def _play_audio_b64(b64: str, suffix: str = ".mp3") -> None:
    try:
        raw = base64.b64decode(b64)
        fd, path = tempfile.mkstemp(prefix="aiemp-voice-", suffix=suffix)
        with os.fdopen(fd, "wb") as f:
            f.write(raw)
        _voice_state["player"] = _play_media_file(path)
    except Exception:
        pass


def _stop_voice_playback() -> None:
    proc = _voice_state.get("player")
    if proc:
        try:
            proc.terminate()
        except Exception:
            pass
    if IS_WIN:
        try:
            import ctypes
            ctypes.windll.winmm.mciSendStringW(f"close {_MCI_ALIAS}", None, 0, None)
        except Exception:  # noqa: BLE001
            pass
    _voice_state["player"] = None


def _start_voice_ws(cfg: dict, agent_id: str, on_event) -> None:
    async def _runner():
        import websockets

        ticket = await asyncio.get_running_loop().run_in_executor(
            None, api_ws_ticket, cfg["url"], cfg["token"])
        url = _voice_ws_url(cfg["url"], agent_id, ticket)
        async with websockets.connect(
            url, ping_interval=20, ssl=_tls_context(cfg["url"]),
        ) as ws:
            _voice_state["ws"] = ws
            _voice_state["loop"] = asyncio.get_running_loop()
            on_event("ready", "Bereit")
            async for raw in ws:
                try:
                    evt = json.loads(raw)
                except Exception:
                    continue
                typ = evt.get("type")
                data = evt.get("data") or {}
                if typ == "ready":
                    on_event("ready", "Bereit")
                elif typ == "transcript":
                    on_event("processing", f"Du: {data.get('text', '')}")
                elif typ == "response":
                    on_event("response", str(data.get("text", "")))
                elif typ == "tts_start":
                    on_event("speaking", "Antwort wird gesprochen")
                elif typ == "audio_chunk":
                    if _voice_state.get("local_edge_tts"):
                        continue
                    mime = str(data.get("mime") or "audio/mpeg")
                    if "pcm" in mime:
                        # Realtime-Front (Nova Sonic): rohes PCM, sofort in den
                        # Streaming-Player — DAS ist die direkte Interaktion
                        # mit dem Voice Layer, kein lokales Nach-Vorlesen.
                        player = _voice_state.get("pcm_player")
                        if player is None:
                            player = _PcmPlayer()
                            _voice_state["pcm_player"] = player
                        try:
                            player.feed(base64.b64decode(str(data.get("b64") or "")),
                                        int(data.get("rate") or 24000))
                        except Exception:  # noqa: BLE001
                            pass
                        continue
                    suffix = ".mp3" if "mpeg" in mime or "mp3" in mime else ".audio"
                    _play_audio_b64(str(data.get("b64") or ""), suffix=suffix)
                elif typ == "done":
                    on_event("ready", "Bereit")
                elif typ == "error":
                    on_event("error", str(data.get("message") or "Voice-Fehler"))

    def _thread():
        try:
            asyncio.run(_runner())
        except Exception as e:
            on_event("error", str(e).strip() or e.__class__.__name__)

    _voice_state["thread"] = threading.Thread(target=_thread, daemon=True)
    _voice_state["thread"].start()


def _voice_send_interrupt() -> None:
    ws = _voice_state.get("ws")
    loop = _voice_state.get("loop")
    _stop_voice_playback()
    if not ws or not loop:
        return

    async def _send():
        await ws.send(json.dumps({"type": "interrupt"}))

    try:
        asyncio.run_coroutine_threadsafe(_send(), loop)
    except Exception:
        pass


def show_interaction_bar(cfg: dict) -> None:
    """Sprach-Bedienleiste — auf macOS UND Windows.

    Die Bar gab es zunaechst nur mit AppKit; unter Windows brach der Aufruf
    still ab (``return``) — dieselbe Faehigkeit fehlte auf der Haelfte der
    Flotte. Jetzt verzweigt der Einstieg nach Plattform, der Voice-WebSocket
    darunter ist ohnehin plattformneutral.
    """
    if not cfg.get("url") or not cfg.get("token"):
        show_setup_dialog(cfg)
        return
    if not _appkit_available():
        return _show_interaction_bar_ctk(cfg)

    _appkit_handlers_init()
    from AppKit import NSColor, NSMakeRect, NSObject, NSPanel, NSWindowStyleMaskBorderless
    from AppKit import NSBackingStoreBuffered, NSButton, NSView, NSVisualEffectView

    def color(r, g, b, a=1.0):
        return NSColor.colorWithSRGBRed_green_blue_alpha_(r, g, b, a)

    def pill(parent, x, y, w, h, bg, border=None, radius=18):
        view = NSView.alloc().initWithFrame_(NSMakeRect(x, y, w, h))
        view.setWantsLayer_(True)
        view.layer().setCornerRadius_(radius)
        view.layer().setBackgroundColor_(bg.CGColor())
        if border:
            view.layer().setBorderWidth_(1)
            view.layer().setBorderColor_(border.CGColor())
        parent.addSubview_(view)
        return view

    def flat_button(parent, title, x, y, w, h, bg, fg=None, radius=16):
        btn = NSButton.alloc().initWithFrame_(NSMakeRect(x, y, w, h))
        btn.setTitle_(title)
        btn.setBordered_(False)
        btn.setWantsLayer_(True)
        btn.layer().setCornerRadius_(radius)
        btn.layer().setBackgroundColor_(bg.CGColor())
        if fg:
            btn.setContentTintColor_(fg)
        parent.addSubview_(btn)
        return btn

    # Breiter (760 -> 880): der Statustext lief vorher unter die Buttons.
    W, H = 880, 86
    panel = NSPanel.alloc().initWithContentRect_styleMask_backing_defer_(
        NSMakeRect(0, 0, W, H), NSWindowStyleMaskBorderless, NSBackingStoreBuffered, False)
    panel.setReleasedWhenClosed_(False)
    panel.setLevel_(24)
    try:
        frame = panel.screen().visibleFrame()
        panel.setFrameOrigin_((frame.origin.x + (frame.size.width - W) / 2, frame.origin.y + 24))
    except Exception:
        panel.center()

    cv = panel.contentView()
    backdrop = NSVisualEffectView.alloc().initWithFrame_(NSMakeRect(0, 0, W, H))
    backdrop.setMaterial_(3)
    backdrop.setState_(1)
    backdrop.setWantsLayer_(True)
    backdrop.layer().setCornerRadius_(28)
    backdrop.layer().setBorderWidth_(1)
    backdrop.layer().setBorderColor_(color(1, 1, 1, 0.16).CGColor())
    cv.addSubview_(backdrop)

    # Agenten zum AUSWAEHLEN, nicht zum Abtippen: niemand kennt seine
    # Agenten-IDs auswendig — das leere Pflichtfeld war der Grund, warum die
    # Bar fuer den Nutzer schlicht "nicht funktionierte" ("Agent ID fehlt").
    agents = []
    agents_error = ""
    try:
        agents = api_list_agents(cfg["url"], cfg["token"])
    except Exception as e:  # noqa: BLE001 — abgelaufener Token, Server weg, ...
        agents_error = str(e).strip() or e.__class__.__name__
    agent_ids = [str(a.get("id") or "") for a in agents]
    agent_titles = []
    for a in agents:
        title = str(a.get("name") or "").strip() or str(a.get("id") or "")[:8]
        # NSPopUpButton verschluckt doppelte Titel stillschweigend — dann
        # zeigte die Liste 3 von 4 Agenten und keiner wuesste warum.
        if title in agent_titles:
            title = f"{title} ({str(a.get('id') or '')[:6]})"
        agent_titles.append(title)

    pill(cv, 16, 16, W - 32, H - 32, color(0.04, 0.05, 0.07, 0.40), color(1, 1, 1, 0.10), radius=22)
    _label(cv, "AI Employee", 32, 50, 110, 18, size=12, bold=True, color=color(1, 1, 1, 0.92))
    _label(cv, "Voice Layer", 32, 29, 110, 16, size=11, color=color(1, 1, 1, 0.52))

    from AppKit import NSPopUpButton
    agent_popup = NSPopUpButton.alloc().initWithFrame_pullsDown_(
        NSMakeRect(148, 29, 150, 26), False)
    for title in agent_titles:
        agent_popup.menu().addItemWithTitle_action_keyEquivalent_(title, None, "")
    stored_agent = str(cfg.get("voice_agent_id") or "")
    if stored_agent in agent_ids:
        agent_popup.selectItemAtIndex_(agent_ids.index(stored_agent))
    cv.addSubview_(agent_popup)

    status_text = "Bereit"
    if agents_error:
        status_text = f"Agenten nicht ladbar: {agents_error}"[:60]
    elif not agents:
        status_text = "Keine Agenten gefunden — Anmeldung prüfen"
    # Breite so, dass der Text VOR dem Connect-Button endet (W-304=576) —
    # 312..556, danach 20px Luft bis zum Button. Keine Ueberlappung mehr.
    status = _label(cv, status_text, 312, 51, 244, 16, size=11, color=color(0.63, 0.70, 0.80, 1))
    transcript = _label(cv, "Drücke Speech und sprich.", 312, 28, 244, 18, size=12, color=color(1, 1, 1, 0.86))
    response = _label(cv, "", 0, 0, 1, 1, size=1, muted=True)
    connect_btn = flat_button(cv, "Connect", W - 304, 27, 82, 32, color(1, 1, 1, 0.14), color(1, 1, 1, 0.90))
    record_btn = flat_button(cv, "Speech", W - 214, 22, 112, 42, color(0.06, 0.46, 0.98, 1), color(1, 1, 1, 1), radius=21)
    close_btn = flat_button(cv, "×", W - 86, 28, 32, 32, color(1, 1, 1, 0.12), color(1, 1, 1, 0.72))

    # DIREKT mit dem Voice Layer des Agenten sprechen: Mikrofon streamt live
    # in die Sitzung, die Antwort kommt als Audio-Strom zurueck. KEIN lokales
    # Edge-TTS mehr — das las nur den Antworttext nach und war der Grund,
    # warum sich die Bar wie Diktiergeraet + Vorleser anfuehlte statt wie ein
    # Gespraech.
    state = {"mic": _MicStreamer(), "connected": False}
    _voice_state["local_edge_tts"] = False

    class VoiceHandler(NSObject):
        def connect_(self, _sender):
            idx = agent_popup.indexOfSelectedItem()
            agent_id = agent_ids[idx] if 0 <= idx < len(agent_ids) else ""
            if not agent_id:
                status.setStringValue_("Kein Agent auswählbar — in der Web-UI einen anlegen")
                return
            cfg["voice_agent_id"] = agent_id
            save_config(cfg)
            status.setStringValue_("Verbinde Voice...")

            def on_event(kind, text):
                try:
                    if kind in {"ready", "speaking", "processing", "error"}:
                        status.performSelectorOnMainThread_withObject_waitUntilDone_(
                            "setStringValue:", text[:120], False)
                    elif kind == "response":
                        response.performSelectorOnMainThread_withObject_waitUntilDone_(
                            "setStringValue:", text[:220], False)
                        transcript.performSelectorOnMainThread_withObject_waitUntilDone_(
                            "setStringValue:", text[:220], False)
                except Exception:  # noqa: BLE001
                    pass

            _start_voice_ws(cfg, agent_id, on_event)
            state["connected"] = True

        def record_(self, sender):
            if not state["connected"]:
                self.connect_(sender)
            if not state["mic"].active:
                _voice_send_interrupt()
                err = state["mic"].start(_voice_send_chunk)
                if err:
                    status.setStringValue_(err[:120])
                    return
                sender.setTitle_("Stop")
                status.setStringValue_("Live — sprich einfach")
                transcript.setStringValue_("Ich höre.")
            else:
                state["mic"].stop()
                _voice_send_commit("de")
                sender.setTitle_("Speech")
                status.setStringValue_("Mikrofon aus")

        def close_(self, _sender):
            state["mic"].stop()
            try:
                ws = _voice_state.get("ws")
                loop = _voice_state.get("loop")
                if ws and loop:
                    asyncio.run_coroutine_threadsafe(ws.close(), loop)
            except Exception:
                pass
            player = _voice_state.get("pcm_player")
            if player:
                player.stop()
            _stop_voice_playback()
            panel.close()

    handler = VoiceHandler.alloc().init()
    _voice_state["handler"] = handler
    _voice_state["panel"] = panel
    connect_btn.setTarget_(handler); connect_btn.setAction_("connect:")
    record_btn.setTarget_(handler); record_btn.setAction_("record:")
    close_btn.setTarget_(handler); close_btn.setAction_("close:")
    # Die Tray-App ist eine Hintergrund-App (LSUIElement) — ohne Aktivierung
    # zeichnet macOS das Fenster schlicht nicht. Genau deshalb "passierte
    # nichts" beim Klick auf Interaction Bar, und die Bar tauchte erst auf,
    # sobald irgendein modaler Dialog (Einstellungen) die App aktivierte.
    from AppKit import NSApp
    NSApp.activateIgnoringOtherApps_(True)
    panel.orderFrontRegardless()
    panel.makeKeyAndOrderFront_(None)


class _MicStreamer:
    """Mikrofon LIVE in die Sprachsitzung streamen — 16 kHz/16-bit/mono PCM.

    Genau das Format, das die Realtime-Front (Nova Sonic) erwartet
    (``push_audio_chunk``). Vorher nahm die Bar eine komplette Datei auf und
    schickte sie am Stueck — das ist STT-Batchbetrieb, keine Interaktion mit
    dem Voice Layer. Jetzt fliesst jeder 100-ms-Block sofort raus; die
    Sprachengine macht ihre eigene Satzerkennung und kann unterbrechen.
    """

    def __init__(self) -> None:
        self._stream = None

    @property
    def active(self) -> bool:
        return self._stream is not None

    def start(self, send_chunk) -> str:
        """``send_chunk(bytes)`` bekommt rohe PCM16-Bloecke. '' = ok, sonst Fehlertext."""
        if self._stream is not None:
            return ""
        try:
            import sounddevice as sd
        except ImportError:
            return "sounddevice fehlt — Mikrofon nicht nutzbar"

        def _cb(indata, _frames, _time, _status):
            try:
                send_chunk(bytes(indata))
            except Exception:  # noqa: BLE001 — Verbindung weg; Stopp kommt von aussen
                pass

        try:
            stream = sd.InputStream(
                samplerate=16000, channels=1, dtype="int16",
                blocksize=1600,  # 100 ms
                callback=_cb,
            )
            stream.start()
        except Exception as e:  # noqa: BLE001 — kein Mikro / belegt / Freigabe fehlt
            return f"Mikrofon ließ sich nicht öffnen: {e}"
        self._stream = stream
        return ""

    def stop(self) -> None:
        if self._stream is None:
            return
        try:
            self._stream.stop()
            self._stream.close()
        except Exception:  # noqa: BLE001
            pass
        self._stream = None


class _PcmPlayer:
    """Antwort-Audio der Sprachsitzung abspielen, WAEHREND es eintrifft.

    Die Realtime-Front streamt rohes PCM (24 kHz) in kleinen Bloecken. Ein
    eigener Abspiel-Thread zieht sie aus einer Queue — der WebSocket-Thread
    darf nicht blockieren, sonst stauen sich Pings und Folge-Ereignisse
    hinter der Soundkarte.
    """

    def __init__(self) -> None:
        import queue as _q
        self._queue: "_q.Queue[tuple[bytes, int] | None]" = _q.Queue(maxsize=256)
        self._thread: threading.Thread | None = None

    def feed(self, pcm: bytes, rate: int) -> None:
        if self._thread is None or not self._thread.is_alive():
            self._thread = threading.Thread(target=self._run, daemon=True)
            self._thread.start()
        try:
            self._queue.put_nowait((pcm, rate))
        except Exception:  # noqa: BLE001 — voller Puffer: lieber knappsen als haengen
            pass

    def _run(self) -> None:
        try:
            import numpy as np
            import sounddevice as sd
        except ImportError:
            return
        stream = None
        current_rate = 0
        while True:
            item = self._queue.get()
            if item is None:
                break
            pcm, rate = item
            try:
                if stream is None or rate != current_rate:
                    if stream is not None:
                        stream.stop(); stream.close()
                    stream = sd.OutputStream(samplerate=rate, channels=1, dtype="int16")
                    stream.start()
                    current_rate = rate
                stream.write(np.frombuffer(pcm, dtype=np.int16).reshape(-1, 1))
            except Exception:  # noqa: BLE001 — Audiogeraet weg: leise aufgeben
                break
        if stream is not None:
            try:
                stream.stop(); stream.close()
            except Exception:  # noqa: BLE001
                pass

    def stop(self) -> None:
        if self._thread is None:
            return
        try:
            while not self._queue.empty():
                self._queue.get_nowait()
        except Exception:  # noqa: BLE001
            pass
        self._queue.put(None)
        self._thread = None


def _voice_send_chunk(pcm: bytes) -> None:
    """Einen Mikrofon-Block in die laufende Sprachsitzung schicken."""
    ws = _voice_state.get("ws")
    loop = _voice_state.get("loop")
    if not ws or not loop:
        return
    payload = json.dumps({"type": "audio_chunk",
                          "data": {"b64": base64.b64encode(pcm).decode("ascii")}})

    async def _send():
        await ws.send(payload)

    try:
        asyncio.run_coroutine_threadsafe(_send(), loop)
    except Exception:  # noqa: BLE001
        pass


def _voice_send_commit(language: str = "de") -> None:
    """Satzende signalisieren — die klassische Pipeline braucht es, die
    Realtime-Front ignoriert es (sie erkennt Sprechpausen selbst)."""
    ws = _voice_state.get("ws")
    loop = _voice_state.get("loop")
    if not ws or not loop:
        return

    async def _send():
        await ws.send(json.dumps({"type": "commit", "data": {"language": language}}))

    try:
        asyncio.run_coroutine_threadsafe(_send(), loop)
    except Exception:  # noqa: BLE001
        pass


def _show_interaction_bar_ctk(cfg: dict) -> None:
    """Die Interaction Bar fuer Windows — gleiche Faehigkeit wie auf dem Mac:
    Agent aus der Liste waehlen, Speech druecken, sprechen, Antwort hoeren."""
    if not _ctk_available():
        _notify("Interaction Bar benötigt customtkinter (fehlt in dieser Installation).")
        return
    ctk = _ctk_setup()

    agents = []
    agents_error = ""
    try:
        agents = api_list_agents(cfg["url"], cfg["token"])
    except Exception as e:  # noqa: BLE001
        agents_error = str(e).strip() or e.__class__.__name__
    agent_ids = [str(a.get("id") or "") for a in agents]
    titles = []
    for a in agents:
        t = str(a.get("name") or "").strip() or str(a.get("id") or "")[:8]
        if t in titles:
            t = f"{t} ({str(a.get('id') or '')[:6]})"
        titles.append(t)

    root = ctk.CTk()
    root.title("AI Employee — Voice")
    root.geometry("640x220")
    root.resizable(False, False)
    root.attributes("-topmost", True)

    ctk.CTkLabel(root, text="AI Employee Voice", font=ctk.CTkFont(size=16, weight="bold")).pack(anchor="w", padx=20, pady=(16, 0))

    row = ctk.CTkFrame(root, fg_color="transparent")
    row.pack(fill="x", padx=20, pady=(10, 0))
    ctk.CTkLabel(row, text="Agent", text_color="gray60", font=ctk.CTkFont(size=11)).pack(side="left")
    stored = str(cfg.get("voice_agent_id") or "")
    start_title = titles[agent_ids.index(stored)] if stored in agent_ids else (titles[0] if titles else "—")
    agent_box = ctk.CTkComboBox(row, values=titles or ["—"], width=260, state="readonly")
    agent_box.set(start_title)
    agent_box.pack(side="left", padx=(10, 0))

    status_lbl = ctk.CTkLabel(root, text=(
        f"Agenten nicht ladbar: {agents_error}" if agents_error else
        ("Keine Agenten gefunden — Anmeldung prüfen" if not titles else "Bereit")
    ), text_color="gray60", font=ctk.CTkFont(size=11))
    status_lbl.pack(anchor="w", padx=20, pady=(8, 0))
    transcript_lbl = ctk.CTkLabel(root, text="Drücke Speech und sprich.", font=ctk.CTkFont(size=12))
    transcript_lbl.pack(anchor="w", padx=20)

    # UI-Updates kommen vom WebSocket-Thread — tkinter darf nur der eigene
    # Thread anfassen, deshalb Queue + Polling statt Direktzugriff.
    import queue as _queue
    events: "_queue.Queue[tuple[str, str]]" = _queue.Queue()

    def on_event(kind, text):
        events.put((kind, text))

    def _poll():
        try:
            while True:
                kind, text = events.get_nowait()
                if kind in {"ready", "speaking", "processing", "error"}:
                    status_lbl.configure(text=text[:120])
                elif kind == "response":
                    transcript_lbl.configure(text=text[:200])
        except _queue.Empty:
            pass
        root.after(200, _poll)

    # Direkt mit dem Voice Layer sprechen — wie auf dem Mac: live streamen,
    # Antwort-Audio kommt vom Server, kein lokales Nach-Vorlesen.
    state = {"mic": _MicStreamer(), "connected": False}
    _voice_state["local_edge_tts"] = False

    def _selected_agent() -> str:
        title = agent_box.get()
        return agent_ids[titles.index(title)] if title in titles else ""

    def on_speech():
        agent_id = _selected_agent()
        if not agent_id:
            status_lbl.configure(text="Kein Agent auswählbar — in der Web-UI einen anlegen")
            return
        if not state["connected"]:
            cfg["voice_agent_id"] = agent_id
            save_config(cfg)
            status_lbl.configure(text="Verbinde Voice...")
            _start_voice_ws(cfg, agent_id, on_event)
            state["connected"] = True
        if not state["mic"].active:
            _voice_send_interrupt()
            err = state["mic"].start(_voice_send_chunk)
            if err:
                status_lbl.configure(text=err[:120])
                return
            speech_btn.configure(text="Stop")
            status_lbl.configure(text="Live — sprich einfach")
            transcript_lbl.configure(text="Ich höre.")
        else:
            state["mic"].stop()
            _voice_send_commit("de")
            speech_btn.configure(text="Speech")
            status_lbl.configure(text="Mikrofon aus")

    def on_close():
        state["mic"].stop()
        try:
            ws = _voice_state.get("ws")
            loop = _voice_state.get("loop")
            if ws and loop:
                asyncio.run_coroutine_threadsafe(ws.close(), loop)
        except Exception:  # noqa: BLE001
            pass
        player = _voice_state.get("pcm_player")
        if player:
            player.stop()
        _stop_voice_playback()
        root.destroy()

    btns = ctk.CTkFrame(root, fg_color="transparent")
    btns.pack(fill="x", padx=20, pady=14)
    speech_btn = ctk.CTkButton(btns, text="Speech", width=140, command=on_speech)
    speech_btn.pack(side="left")
    ctk.CTkButton(btns, text="Schließen", width=100, fg_color="transparent", border_width=1,
                  text_color="gray60", border_color="#444", command=on_close).pack(side="right")

    root.protocol("WM_DELETE_WINDOW", on_close)
    root.after(200, _poll)
    root.mainloop()


# ── Windows/Linux dialogs (customtkinter — dark, modern) ──────────────────────

def _ctk_available():
    try:
        import customtkinter  # noqa
        return True
    except ImportError:
        return False


def _ctk_setup():
    import customtkinter as ctk
    ctk.set_appearance_mode("dark")
    ctk.set_default_color_theme("blue")
    return ctk


RISK_COLORS = {"gering": "#22c55e", "mittel": "#f59e0b", "hoch": "#ef4444"}


def _show_setup_tkinter(cfg):
    if not _ctk_available():
        return _show_setup_plain_tkinter(cfg)

    ctk = _ctk_setup()
    result = {}

    root = ctk.CTk()
    root.title("AI-Employee Bridge — Einstellungen")
    # 400px was too short on Windows (DPI/font scaling) → the "Anmelden" button
    # was clipped off the bottom. Taller default + vertical resize + minsize so
    # the button is always reachable regardless of display scaling.
    root.geometry("480x580")
    root.minsize(480, 500)
    root.resizable(False, True)
    _ctk_fenster_symbol(root)

    # Kopf mit App-Symbol — wie auf dem Mac
    _ctk_kopf(ctk, root, "AI Employee Bridge", "Dein Rechner, sicher verbunden mit deinen Agenten.").pack(
        fill="x", padx=24, pady=(24, 8))
    ctk.CTkFrame(root, height=1, fg_color=("gray80", "#333")).pack(fill="x", padx=24, pady=12)

    def field(label, placeholder, secure=False, value=""):
        ctk.CTkLabel(root, text=label, font=ctk.CTkFont(size=10, weight="bold"), text_color="gray50").pack(anchor="w", padx=24, pady=(4,1))
        e = ctk.CTkEntry(root, placeholder_text=placeholder, show="●" if secure else "", width=432, height=36)
        if value: e.insert(0, value)
        e.pack(padx=24)
        return e

    url_f = field("SERVER-ADRESSE", "wss://agents.example.com/ws/computer-use/bridge?session_id=...", value=cfg.get("url",""))
    em_f  = field("E-MAIL",    "name@example.com", value=cfg.get("email", ""))
    pw_f  = field("PASSWORT",  "••••••••", secure=True)

    ctk.CTkFrame(root, height=1, fg_color="#333").pack(fill="x", padx=24, pady=12)

    auto_var = ctk.BooleanVar(value=cfg.get("auto_connect", True))
    ctk.CTkCheckBox(root, text="Beim Start automatisch verbinden", variable=auto_var).pack(anchor="w", padx=24)

    status_lbl = ctk.CTkLabel(root, text="", text_color="gray50", font=ctk.CTkFont(size=11))
    status_lbl.pack(anchor="w", padx=24, pady=(6,0))

    # Anchor the action buttons to the bottom edge so they stay visible even if
    # the content above needs more vertical space than expected.
    btn_frame = ctk.CTkFrame(root, fg_color="transparent")
    btn_frame.pack(side="bottom", fill="x", padx=24, pady=16)

    def on_cancel(): root.destroy()

    def on_save():
        url_input = url_f.get()
        url, requested_session = normalize_bridge_url(url_input)
        email = em_f.get().strip()
        pw    = pw_f.get()
        if not url or not email or not pw:
            status_lbl.configure(text="⚠  Bitte alle Felder ausfüllen.", text_color="#f59e0b"); return
        status_lbl.configure(text="Verbinde…", text_color="gray50")
        caps = cfg.get("allowed_capabilities", sorted(DEFAULT_CAPABILITIES))
        def _do():
            try:
                token, sid = login_and_prepare(url, email, pw, caps, requested_session, cfg)
                result.update({"url":url,"token":token,"session":sid,"email":email,
                               "refresh":cfg.get("refresh",""),
                               "auto_connect":bool(auto_var.get()),
                               "allowed_capabilities":caps,"allowed_paths":cfg.get("allowed_paths",[])})
                root.after(0, root.destroy)
            except urllib.error.HTTPError:
                root.after(0, lambda: status_lbl.configure(text="⚠  Falsche E-Mail oder Passwort.", text_color="#ef4444"))
            except Exception as e:
                root.after(0, lambda e=e: status_lbl.configure(text=f"⚠  {e}", text_color="#ef4444"))
        threading.Thread(target=_do, daemon=True).start()

    ctk.CTkButton(btn_frame, text="Abbrechen", fg_color="transparent", border_width=1,
                  text_color="gray60", border_color="#444", width=100, command=on_cancel).pack(side="right", padx=(8,0))
    ctk.CTkButton(btn_frame, text="Anmelden & verbinden", width=190, height=38, corner_radius=12,
                  fg_color=_CTK_MARKE, font=ctk.CTkFont(size=13, weight="bold"), command=on_save).pack(side="right")

    root.mainloop()
    return result if result else None


def _show_permissions_tkinter(cfg):
    if not _ctk_available():
        return _show_permissions_plain_tkinter(cfg)

    ctk = _ctk_setup()
    current = set(cfg.get("allowed_capabilities", sorted(DEFAULT_CAPABILITIES)))
    paths   = list(cfg.get("allowed_paths", []))
    cap_vars = {}

    root = ctk.CTk()
    root.title("AI-Employee Bridge — Berechtigungen")
    root.geometry("520x660")
    root.resizable(False, False)

    ctk.CTkLabel(root, text="Berechtigungen", font=ctk.CTkFont(size=20, weight="bold")).pack(anchor="w", padx=24, pady=(24,2))
    ctk.CTkLabel(root, text="Was darf der Agent auf diesem PC tun?", text_color="gray60", font=ctk.CTkFont(size=12)).pack(anchor="w", padx=24)
    ctk.CTkFrame(root, height=1, fg_color="#333").pack(fill="x", padx=24, pady=10)

    scroll = ctk.CTkScrollableFrame(root, height=300, fg_color="transparent")
    scroll.pack(fill="x", padx=24)

    for cap in CAPABILITY_META:
        row = ctk.CTkFrame(scroll, fg_color="#1e1e2e", corner_radius=8)
        row.pack(fill="x", pady=3, ipady=6)
        v = ctk.BooleanVar(value=cap["id"] in current)
        cap_vars[cap["id"]] = v
        left = ctk.CTkFrame(row, fg_color="transparent")
        left.pack(side="left", fill="both", expand=True, padx=10)
        ctk.CTkCheckBox(left, text=cap["label"], variable=v, font=ctk.CTkFont(size=13, weight="bold")).pack(anchor="w")
        ctk.CTkLabel(left, text=cap["desc"], text_color="gray50", font=ctk.CTkFont(size=11)).pack(anchor="w", padx=22)
        risk_col = RISK_COLORS.get(cap["risk"], "gray")
        ctk.CTkLabel(row, text=f"● {cap['risk']}", text_color=risk_col, font=ctk.CTkFont(size=10)).pack(side="right", padx=12)

    ctk.CTkFrame(root, height=1, fg_color="#333").pack(fill="x", padx=24, pady=10)

    # Browser im Hintergrund (headless) — wie ego lite: steuert die Seite über
    # den DOM, ohne sichtbares Fenster. Opt-in, greift beim nächsten Start.
    headless_var = ctk.BooleanVar(value=bool(cfg.get("browser_headless", False)))
    ctk.CTkCheckBox(root, text="Browser unsichtbar im Hintergrund betreiben (nur mit Browser-Steuerung)",
                    variable=headless_var).pack(anchor="w", padx=24, pady=(0, 8))

    ctk.CTkLabel(root, text="ORDNER-ZUGRIFF — Startordner für Shell-Befehle (ohne Eintrag: gesperrt)",
                 font=ctk.CTkFont(size=10, weight="bold"), text_color="gray50").pack(anchor="w", padx=24, pady=(0,4))

    path_box = ctk.CTkTextbox(root, height=72, font=ctk.CTkFont(family="Courier", size=11))
    path_box.pack(fill="x", padx=24)
    path_box.insert("1.0", "\n".join(paths) if paths else "")
    path_box.configure(state="disabled")

    def refresh_paths():
        path_box.configure(state="normal")
        path_box.delete("1.0", "end")
        path_box.insert("1.0", "\n".join(paths))
        path_box.configure(state="disabled")

    pb = ctk.CTkFrame(root, fg_color="transparent")
    pb.pack(fill="x", padx=24, pady=(4,0))

    def add_path():
        import tkinter.filedialog as fd
        p = fd.askdirectory(title="Ordner wählen")
        if p and p not in paths:
            paths.append(p); refresh_paths()

    def del_path():
        if paths: paths.pop(); refresh_paths()

    ctk.CTkButton(pb, text="+ Hinzufügen", width=120, command=add_path).pack(side="left")
    ctk.CTkButton(pb, text="– Entfernen",  width=110, fg_color="#333", hover_color="#444",
                  text_color="gray70", command=del_path).pack(side="left", padx=(8,0))

    ctk.CTkFrame(root, height=1, fg_color="#333").pack(fill="x", padx=24, pady=10)

    # Freigabelisten: WELCHE Anwendung, WELCHE Adresse. Leer = nicht
    # eingeschraenkt. Anders als die Ordnerliste darueber werden diese beiden
    # serverseitig durchgesetzt, nicht nur angezeigt.
    ctk.CTkLabel(root, text="ERLAUBTE ANWENDUNGEN (leer = alle)",
                 font=ctk.CTkFont(size=10, weight="bold"), text_color="gray50").pack(anchor="w", padx=24, pady=(0, 4))
    apps_box = ctk.CTkTextbox(root, height=52, font=ctk.CTkFont(family="Courier", size=11))
    apps_box.pack(fill="x", padx=24)
    apps_box.insert("1.0", "\n".join(cfg.get("allowed_apps") or []))

    ctk.CTkLabel(root, text="ERLAUBTE ADRESSEN (leer = alle) — z. B. intranet.example",
                 font=ctk.CTkFont(size=10, weight="bold"), text_color="gray50").pack(anchor="w", padx=24, pady=(10, 4))
    domains_box = ctk.CTkTextbox(root, height=52, font=ctk.CTkFont(family="Courier", size=11))
    domains_box.pack(fill="x", padx=24)
    domains_box.insert("1.0", "\n".join(cfg.get("allowed_domains") or []))

    ctk.CTkFrame(root, height=1, fg_color="#333").pack(fill="x", padx=24, pady=10)

    status_lbl = ctk.CTkLabel(root, text="", text_color="gray50", font=ctk.CTkFont(size=11))
    status_lbl.pack(anchor="w", padx=24)

    bf = ctk.CTkFrame(root, fg_color="transparent")
    bf.pack(fill="x", padx=24, pady=(4,16))

    def on_cancel(): root.destroy()

    def _lines(box) -> list[str]:
        return [ln.strip() for ln in box.get("1.0", "end").splitlines() if ln.strip()]

    def on_save():
        cfg["allowed_capabilities"] = [cid for cid, v in cap_vars.items() if v.get()]
        cfg["allowed_paths"] = paths
        cfg["allowed_apps"] = _lines(apps_box)
        cfg["allowed_domains"] = _lines(domains_box)
        cfg["browser_headless"] = bool(headless_var.get())
        save_config(cfg)
        if is_running():
            status_lbl.configure(text="Übertrage an Server…")
            def _p():
                try:
                    api_update_capabilities(cfg["url"],cfg["token"],cfg["session"],cfg["allowed_capabilities"], cfg)
                    root.after(0, lambda: status_lbl.configure(text="✓ Gespeichert", text_color="#22c55e"))
                except Exception as e:
                    root.after(0, lambda e=e: status_lbl.configure(text=f"Lokal gespeichert ({e})", text_color="#f59e0b"))
                root.after(800, root.destroy)
            threading.Thread(target=_p, daemon=True).start()
        else:
            root.destroy()

    ctk.CTkButton(bf, text="Abbrechen", fg_color="transparent", border_width=1,
                  text_color="gray60", border_color="#444", width=100, command=on_cancel).pack(side="right", padx=(8,0))
    ctk.CTkButton(bf, text="Speichern", width=110, command=on_save).pack(side="right")

    root.mainloop()


def _show_status_tkinter(cfg):
    if not _ctk_available():
        return _show_status_plain_tkinter(cfg)

    ctk = _ctk_setup()
    state = _status
    # Ask the SERVER whether it actually sees this bridge, like the macOS window
    # does. The local flag alone was the reason this dialog showed "Verbinde…"
    # indefinitely; the server's view is the ground truth and also catches the
    # case where the bridge is fine but the local state got stuck.
    server_connected = False
    try:
        if cfg.get("url") and cfg.get("token") and cfg.get("session"):
            server_connected = bool(
                api_session_status(cfg["url"], cfg["token"], cfg["session"]).get("bridge_connected")
            )
    except Exception:  # noqa: BLE001 — offline/unreachable → fall back to local state
        pass

    if server_connected or state == "connected":
        dot, dot_col, state_text = "●", "#22c55e", "Verbunden"
    elif state == "connecting":
        dot, dot_col, state_text = "●", "#f59e0b", "Verbinde…"
    else:
        dot, dot_col, state_text = "●", "#6b7280", state.replace("error: ", "")

    root = ctk.CTk()
    root.title("AI-Employee Bridge — Status")
    root.geometry("420x340")
    root.resizable(False, False)

    ctk.CTkLabel(root, text="Bridge Status", font=ctk.CTkFont(size=20, weight="bold")).pack(anchor="w", padx=24, pady=(24,2))
    ctk.CTkFrame(root, height=1, fg_color="#333").pack(fill="x", padx=24, pady=10)

    def row(lbl, val, val_color=None):
        r = ctk.CTkFrame(root, fg_color="transparent")
        r.pack(fill="x", padx=24, pady=3)
        ctk.CTkLabel(r, text=lbl, text_color="gray50", width=100, anchor="w", font=ctk.CTkFont(size=12)).pack(side="left")
        ctk.CTkLabel(r, text=val, text_color=val_color or "white", anchor="w", font=ctk.CTkFont(size=12)).pack(side="left", fill="x", expand=True)

    row("Verbindung", f"{dot}  {state_text}", val_color=dot_col)
    row("Version",    f"Bridge v{BRIDGE_VERSION}")
    row("Server",     cfg.get("url") or "—")
    row("Session",    (cfg.get("session") or "—")[:16])

    cap_map = {c["id"]: c["label"] for c in CAPABILITY_META}
    caps_str = ", ".join(cap_map.get(c,c) for c in cfg.get("allowed_capabilities",[])) or "Keine"
    row("Erlaubt", caps_str)

    paths = cfg.get("allowed_paths", [])
    if paths:
        ctk.CTkFrame(root, height=1, fg_color="#333").pack(fill="x", padx=24, pady=8)
        row("Ordner", "\n".join(paths))

    ctk.CTkFrame(root, height=1, fg_color="#333").pack(fill="x", padx=24, pady=10)
    ctk.CTkButton(root, text="Schließen", width=100, command=root.destroy).pack(anchor="e", padx=24, pady=(0,16))

    root.mainloop()


# ── Plain tkinter last-resort fallbacks (no customtkinter) ────────────────────

def _show_setup_plain_tkinter(cfg):
    try:
        import tkinter as tk; from tkinter import ttk
    except ImportError:
        return None
    result = {}
    root = tk.Tk(); root.title("AI-Employee Bridge"); root.geometry("440x280")
    f = ttk.Frame(root, padding=16); f.pack(fill="both", expand=True)
    ttk.Label(f, text="Bridge URL / Server:").grid(row=0, column=0, sticky="w", pady=3)
    url_v = tk.StringVar(value=cfg.get("url","")); ttk.Entry(f, textvariable=url_v, width=36).grid(row=0, column=1)
    ttk.Label(f, text="E-Mail:").grid(row=1, column=0, sticky="w", pady=3)
    em_v = tk.StringVar(value=cfg.get("email","")); ttk.Entry(f, textvariable=em_v, width=36).grid(row=1, column=1)
    ttk.Label(f, text="Passwort:").grid(row=2, column=0, sticky="w", pady=3)
    pw_v = tk.StringVar(); ttk.Entry(f, textvariable=pw_v, show="*", width=36).grid(row=2, column=1)
    auto_v = tk.BooleanVar(value=cfg.get("auto_connect",True))
    ttk.Checkbutton(f, text="Automatisch verbinden", variable=auto_v).grid(row=3, column=0, columnspan=2, sticky="w")
    sv = tk.StringVar(); ttk.Label(f, textvariable=sv).grid(row=4, column=0, columnspan=2, sticky="w")
    def save():
        url, requested_session = normalize_bridge_url(url_v.get())
        em, pw = em_v.get().strip(), pw_v.get()
        if not url or not em or not pw: sv.set("Felder ausfüllen!"); return
        sv.set("Verbinde…"); root.update()
        def _do():
            try:
                t, s = login_and_prepare(url, em, pw, cfg.get("allowed_capabilities", sorted(DEFAULT_CAPABILITIES)), requested_session, cfg)
                result.update({"url":url,"token":t,"session":s,"email":em,"refresh":cfg.get("refresh",""),"auto_connect":auto_v.get(),"allowed_capabilities":cfg.get("allowed_capabilities",sorted(DEFAULT_CAPABILITIES)),"allowed_paths":cfg.get("allowed_paths",[])})
                root.after(0, root.destroy)
            except Exception as e:
                root.after(0, lambda e=e: sv.set(f"Fehler: {e}"))
        threading.Thread(target=_do, daemon=True).start()
    bf = ttk.Frame(f); bf.grid(row=5, column=0, columnspan=2, sticky="e", pady=8)
    ttk.Button(bf, text="Abbrechen", command=root.destroy).pack(side="right", padx=4)
    ttk.Button(bf, text="Anmelden", command=save).pack(side="right")
    root.mainloop(); return result if result else None


def _show_permissions_plain_tkinter(cfg):
    try:
        import tkinter as tk; from tkinter import ttk
    except ImportError:
        return
    current = set(cfg.get("allowed_capabilities", sorted(DEFAULT_CAPABILITIES)))
    cap_vars = {}
    root = tk.Tk(); root.title("Berechtigungen"); root.geometry("460x380")
    f = ttk.Frame(root, padding=16); f.pack(fill="both", expand=True)
    for cap in CAPABILITY_META:
        v = tk.BooleanVar(value=cap["id"] in current); cap_vars[cap["id"]] = v
        ttk.Checkbutton(f, text=f"{cap['label']} — {cap['desc']}", variable=v).pack(anchor="w", pady=2)
    def save():
        cfg["allowed_capabilities"] = [k for k,v in cap_vars.items() if v.get()]; save_config(cfg)
        if is_running():
            threading.Thread(target=lambda: api_update_capabilities(cfg["url"],cfg["token"],cfg["session"],cfg["allowed_capabilities"], cfg), daemon=True).start()
        root.destroy()
    ttk.Button(f, text="Speichern", command=save).pack(anchor="e", pady=8)
    root.mainloop()


def _show_status_plain_tkinter(cfg):
    try:
        import tkinter as tk; from tkinter import ttk
    except ImportError:
        return
    root = tk.Tk(); root.title("Bridge Status"); root.geometry("380x220")
    f = ttk.Frame(root, padding=16); f.pack(fill="both", expand=True)
    state = _status
    server_connected = False
    try:
        if cfg.get("url") and cfg.get("token") and cfg.get("session"):
            server_connected = bool(
                api_session_status(cfg["url"], cfg["token"], cfg["session"]).get("bridge_connected")
            )
    except Exception:  # noqa: BLE001 — fall back to the local state
        pass
    ttk.Label(f, text="● Verbunden" if (server_connected or state == "connected") else f"● {state}").pack(anchor="w")
    ttk.Label(f, text=f"Version: Bridge v{BRIDGE_VERSION}").pack(anchor="w")
    ttk.Label(f, text=f"Server: {cfg.get('url','—')}").pack(anchor="w")
    ttk.Label(f, text=f"Session: {cfg.get('session','—')}").pack(anchor="w")
    ttk.Button(f, text="Schließen", command=root.destroy).pack(anchor="e", pady=8)
    root.mainloop()


# ── Overlay (#1019): Angebote, Fragen, Schritte — oben in der Bildschirmmitte ──
#
# Die Bridge meldet ueber ``on_overlay`` drei Arten: ``observer_config``,
# ``vorschlag`` (der Agent bietet etwas an) und ``overlay_status`` (arbeitet,
# fertig, gestoppt, Hinweis). ``OverlayModell`` entscheidet, WAS zu sehen ist
# und was ein Klick an den Server schickt — fuer Mac und Windows gleich. Die
# beiden Oberflaechen darunter zeichnen nur.

_OVERLAY_TITEL = {
    "frage": "Angebot",
    "arbeitet": "Arbeitet",
    "ergebnis": "Fertig",
    "fehler": "Fehler",
    "gestoppt": "Gestoppt",
    "hinweis": "Hinweis",
}


#: Was der Agent gerade tut — in Worten statt Befehlsnamen.
_AKTION_TEXT = {
    "click": "Klickt …", "mouse_click": "Klickt …", "type": "Tippt …", "key": "Drückt Tasten …",
    "hotkey": "Drückt Tasten …", "scroll": "Scrollt …", "mouse_scroll": "Scrollt …",
    "move": "Bewegt die Maus …", "mouse_move": "Bewegt die Maus …", "drag": "Zieht …",
    "screenshot": "Schaut auf den Bildschirm …", "ax_tree": "Liest die Oberfläche …",
    "find_element": "Sucht ein Element …", "wait_for_element": "Wartet auf die Anwendung …",
    "open_app": "Öffnet ein Programm …", "close_app": "Schließt ein Programm …",
    "open_url": "Öffnet eine Seite …", "focus_window": "Wechselt das Fenster …",
    "list_windows": "Schaut, welche Fenster offen sind …", "shell_run": "Führt einen Befehl aus …",
}


class OverlayModell:
    """Zustand des Overlays. Reine Logik — testbar ohne Fenster."""

    def __init__(self) -> None:
        self.sichtbar = False
        self.art = ""
        self.text = ""
        self.schritte = 0
        self.vorschlag_id: str | None = None
        # Was der Agent nach einem Ja tut — steht mit im Overlay, damit das Ja
        # nicht einer freundlichen Frage gilt, hinter der etwas anderes steckt.
        self.auftrag = ""
        self.agent_id: str | None = None
        self.gestoppt = False
        # „Ausblenden“ gilt bis zum naechsten Angebot oder Stopp — Statusmeldungen
        # eines laufenden Auftrags holen das Fenster nicht zurueck.
        self.ausgeblendet = False
        # Der Agent prueft gerade einen Blick (Mitdenken) — zeigt die Menueleiste.
        self.denkt = False

    @property
    def titel(self) -> str:
        titel = _OVERLAY_TITEL.get(self.art, "")
        if self.art == "arbeitet" and self.schritte:
            titel += f" · {self.schritte} Schritt{'e' if self.schritte != 1 else ''}"
        return titel

    def knoepfe(self) -> list[tuple[str, str]]:
        if self.art == "frage":
            return [("ja", "Ja"), ("nein", "Nein"), ("stopp", "Stopp"), ("chat", "Chat"), ("aus", "Ausblenden")]
        if self.art == "arbeitet":
            return [("stopp", "Stopp"), ("chat", "Chat"), ("aus", "Ausblenden")]
        if self.art == "gestoppt":
            return [("weiter", "Freigeben"), ("chat", "Chat"), ("aus", "Ausblenden")]
        return [("chat", "Chat"), ("aus", "Schließen")]

    @property
    def mit_eingabe(self) -> bool:
        return self.art == "frage"

    def verarbeiten(self, nachricht: dict) -> bool:
        """Nachricht der Bridge einarbeiten. True = neu zeichnen."""
        art = nachricht.get("type")
        if art == "mitdenken":
            # Nur fuer das Menueleisten-Symbol („schaut hin“) — das Overlay bleibt zu.
            self.denkt = nachricht.get("zustand") == "laeuft"
            return False
        if art == "observer_config":
            self.agent_id = nachricht.get("agent_id") or self.agent_id
            if nachricht.get("gestoppt") and not self.gestoppt:
                self.gestoppt = True
                self._zeigen("gestoppt", "Gestoppt. Der Agent darf auf diesem Rechner nichts tun, bis du freigibst.")
                return True
            self.gestoppt = bool(nachricht.get("gestoppt"))
            return False
        if art == "vorschlag":
            self.vorschlag_id = str(nachricht.get("id") or "")
            self.ausgeblendet = False
            self._zeigen("frage", str(nachricht.get("text") or ""))
            # Ungekuerzt: das Ja gilt genau diesem Text (der Server begrenzt ihn).
            self.auftrag = str(nachricht.get("auftrag") or "")
            return True
        if art != "overlay_status":
            return False
        zustand = nachricht.get("zustand")
        text = str(nachricht.get("text") or "")
        if nachricht.get("schritte") is not None:
            self.schritte = int(nachricht.get("schritte") or 0)
        if zustand == "arbeitet":
            # Ein offenes Angebot bleibt stehen, bis der Mensch antwortet.
            if self.art == "frage" and self.sichtbar:
                return False
            aktion = str(nachricht.get("aktion") or "")
            self._zeigen("arbeitet", text or _AKTION_TEXT.get(aktion, "Arbeitet auf deinem Rechner …"))
            return not self.ausgeblendet
        if zustand in ("fertig", "fehler"):
            self._zeigen("ergebnis" if zustand == "fertig" else "fehler", text or "Fertig.")
            self.ausgeblendet = False
            return True
        if zustand == "gestoppt":
            self.gestoppt = True
            self.ausgeblendet = False
            self._zeigen("gestoppt", text or "Gestoppt.")
            return True
        if zustand == "bereit":
            self.gestoppt = False
            self.sichtbar = False
            return True
        if zustand == "hinweis":
            self._zeigen("hinweis", text)
            return not self.ausgeblendet
        return False

    @property
    def detail(self) -> str:
        if self.art == "frage" and self.auftrag:
            return f"Danach tut der Agent: {self.auftrag}"
        return ""

    def _zeigen(self, art: str, text: str) -> None:
        if art != "frage":
            self.auftrag = ""
        self.art = art
        self.text = text[:600]
        self.sichtbar = not self.ausgeblendet or art in ("frage", "gestoppt", "ergebnis", "fehler")

    def klick(self, knopf: str, eingabe: str = "") -> dict | None:
        """Was der Klick an den Server schickt (oder None). Aendert den Zustand."""
        if knopf == "ja" and self.vorschlag_id:
            nachricht = {"type": "vorschlag_antwort", "id": self.vorschlag_id, "antwort": "ja",
                         "text": " ".join(eingabe.split())[:1000]}
            self.vorschlag_id = None
            self.schritte = 0
            self._zeigen("arbeitet", "Agent übernimmt …")
            return nachricht
        if knopf == "nein" and self.vorschlag_id:
            nachricht = {"type": "vorschlag_antwort", "id": self.vorschlag_id, "antwort": "nein"}
            self.vorschlag_id = None
            self.sichtbar = False
            return nachricht
        if knopf == "stopp":
            self.gestoppt = True
            self.vorschlag_id = None
            self._zeigen("gestoppt", "Gestoppt. Der Agent darf auf diesem Rechner nichts tun, bis du freigibst.")
            return {"type": "overlay_stopp"}
        if knopf == "weiter":
            self.gestoppt = False
            self.sichtbar = False
            return {"type": "overlay_weiter"}
        if knopf == "aus":
            self.sichtbar = False
            self.ausgeblendet = self.art in ("arbeitet", "hinweis")
            return None
        return None


_overlay: dict = {"modell": OverlayModell(), "bridge": None, "ui": None}


def _overlay_chat_url(cfg: dict) -> str | None:
    base = str(cfg.get("url") or "").rstrip("/")
    if not base:
        return None
    agent_id = _overlay["modell"].agent_id
    return f"{base}/agents/{urllib.parse.quote(str(agent_id))}" if agent_id else base


def _overlay_klick(cfg: dict, knopf: str, eingabe: str = "") -> None:
    """Gemeinsamer Klick-Weg beider Oberflaechen."""
    if knopf == "chat":
        url = _overlay_chat_url(cfg)
        if url:
            webbrowser.open(url)
        return
    nachricht = _overlay["modell"].klick(knopf, eingabe)
    bridge_instanz = _overlay.get("bridge")
    if nachricht and bridge_instanz is not None:
        bridge_instanz.overlay_antwort(nachricht)
    ui = _overlay.get("ui")
    if ui is not None:
        ui.zeichnen()


def _on_bridge(bridge_instanz) -> None:
    _overlay["bridge"] = bridge_instanz


def _on_overlay(nachricht: dict) -> None:
    """Aus dem Bridge-Thread: Modell aktualisieren, Oberflaeche anstossen."""
    if not _overlay["modell"].verarbeiten(nachricht):
        return
    ui = _overlay.get("ui")
    if ui is None:
        cfg = load_config()
        ui = _MacOverlay(cfg) if _appkit_available() else _TkOverlay(cfg)
        _overlay["ui"] = ui
    ui.zeichnen()


class _MacOverlay:
    """Schwebende Leiste oben mittig, ueber allen Fenstern, ohne der aktiven
    Anwendung den Fokus zu nehmen. Dunkel und kompakt: Kopfzeile mit Symbol und
    Schritten, Text, Auftrag, Antwortfeld mit Hauptknoepfen, darunter kleine
    Symbolknoepfe (Stopp, Chat, Ausblenden). AppKit nur auf dem Hauptthread."""

    W = 560
    HAUPT = ("ja", "nein", "weiter")

    def __init__(self, cfg: dict) -> None:
        self.cfg = cfg
        self.panel = None

    def zeichnen(self) -> None:
        from PyObjCTools import AppHelper  # type: ignore
        AppHelper.callAfter(self._zeichnen_haupt)

    def _bauen(self) -> None:
        from AppKit import (NSAppearance, NSBackingStoreBuffered, NSButton, NSColor, NSFont, NSImage,
                            NSImageView, NSMakeRect, NSObject, NSPanel, NSTextField, NSView,
                            NSVisualEffectView, NSWindowStyleMaskBorderless)

        global _OverlayPanelKlasse, _OverlayHandlerKlasse
        if _OverlayPanelKlasse is None:
            class AIEOverlayPanel(NSPanel):
                # Randlose Panels werden sonst nie Schluesselfenster — das
                # Antwortfeld liesse sich nicht anklicken.
                def canBecomeKeyWindow(self):
                    return True

            class AIEOverlayHandler(NSObject):
                def klick_(self, sender):
                    ui = _overlay.get("ui")
                    if ui is None:
                        return
                    knopf = ui.tag_zu_knopf.get(int(sender.tag()), "")
                    eingabe = str(ui.eingabe.stringValue()) if ui.eingabe is not None else ""
                    _overlay_klick(ui.cfg, knopf, eingabe)

            _OverlayPanelKlasse, _OverlayHandlerKlasse = AIEOverlayPanel, AIEOverlayHandler

        def farbe(r, g, b, a=1.0):
            return NSColor.colorWithSRGBRed_green_blue_alpha_(r, g, b, a)

        self._farbe = farbe
        W, H = self.W, 160
        # 1<<7 = NSWindowStyleMaskNonactivatingPanel: erscheint, ohne der Anwendung,
        # in der der Mensch gerade arbeitet, den Fokus zu nehmen.
        panel = _OverlayPanelKlasse.alloc().initWithContentRect_styleMask_backing_defer_(
            NSMakeRect(0, 0, W, H), NSWindowStyleMaskBorderless | (1 << 7), NSBackingStoreBuffered, False)
        panel.setReleasedWhenClosed_(False)
        panel.setLevel_(25)
        panel.setOpaque_(False)
        panel.setBackgroundColor_(NSColor.clearColor())
        panel.setHasShadow_(True)
        panel.setAppearance_(NSAppearance.appearanceNamed_("NSAppearanceNameDarkAqua"))
        # NSWindowSharingNone: fehlt in jeder Bildschirmaufnahme (Agent und Mitdenken).
        panel.setSharingType_(0)
        # Auf jedem Schreibtisch (Spaces) und ueber Vollbild-Apps.
        panel.setCollectionBehavior_((1 << 0) | (1 << 8))
        cv = panel.contentView()

        hinten = NSVisualEffectView.alloc().initWithFrame_(NSMakeRect(0, 0, W, H))
        hinten.setMaterial_(13)              # HUD — dunkel, leicht durchscheinend
        hinten.setState_(1)
        hinten.setWantsLayer_(True)
        hinten.layer().setCornerRadius_(16)
        hinten.layer().setMasksToBounds_(True)
        hinten.layer().setBorderWidth_(1)
        hinten.layer().setBorderColor_(farbe(*_ACCENT_RGB, 0.75).CGColor())
        cv.addSubview_(hinten)
        toenung = NSView.alloc().initWithFrame_(NSMakeRect(0, 0, W, H))
        toenung.setWantsLayer_(True)
        toenung.layer().setBackgroundColor_(farbe(0.04, 0.06, 0.11, 0.72).CGColor())
        hinten.addSubview_(toenung)
        self.hinten, self.toenung = hinten, toenung

        def label(groesse, gewicht=0.0, alpha=0.95, zeilen=0):
            lbl = NSTextField.labelWithString_("")
            lbl.setFont_(NSFont.systemFontOfSize_weight_(groesse, gewicht))
            lbl.setTextColor_(farbe(1, 1, 1, alpha))
            lbl.setLineBreakMode_(0)
            lbl.setMaximumNumberOfLines_(zeilen)
            cv.addSubview_(lbl)
            return lbl

        self.symbol = NSImageView.alloc().initWithFrame_(NSMakeRect(16, 0, 18, 18))
        bild = _app_symbol_bild(18)
        if bild is not None:
            self.symbol.setImage_(bild)
        cv.addSubview_(self.symbol)
        self.kopf = label(12, 0.4, 0.95)
        self.schritte = label(11, 0.0, 0.5)
        self.text = label(14, 0.0, 0.96)
        self.detail = label(11.5, 0.0, 0.58)

        # Eigene runde Flaeche statt des System-Rahmens (der zeigte im Dunkeln
        # einen doppelten Rand).
        self.feld = NSView.alloc().initWithFrame_(NSMakeRect(16, 0, 200, 30))
        self.feld.setWantsLayer_(True)
        self.feld.layer().setCornerRadius_(15)
        self.feld.layer().setBackgroundColor_(farbe(1, 1, 1, 0.07).CGColor())
        self.feld.layer().setBorderWidth_(1)
        self.feld.layer().setBorderColor_(farbe(1, 1, 1, 0.12).CGColor())
        cv.addSubview_(self.feld)
        self.eingabe = NSTextField.alloc().initWithFrame_(NSMakeRect(12, 6, 176, 18))
        self.eingabe.setBezeled_(False)
        self.eingabe.setDrawsBackground_(False)
        self.eingabe.setFocusRingType_(1)   # kein blauer Rahmen um die runde Flaeche
        self.eingabe.cell().setPlaceholderString_("Antwort oder Hinweis (optional)")
        self.eingabe.setFont_(NSFont.systemFontOfSize_(12.5))
        self.eingabe.setTextColor_(farbe(1, 1, 1, 0.95))
        self.feld.addSubview_(self.eingabe)

        self.handler = _OverlayHandlerKlasse.alloc().init()

        def knopf(tag, chip=False):
            b = NSButton.alloc().initWithFrame_(NSMakeRect(0, 0, 80, 24 if chip else 30))
            b.setBordered_(False)
            b.setWantsLayer_(True)
            b.layer().setCornerRadius_(12 if chip else 15)
            b.setTag_(tag)
            b.setTarget_(self.handler)
            b.setAction_("klick:")
            b.setFont_(NSFont.systemFontOfSize_weight_(11 if chip else 13, 0.0 if chip else 0.3))
            cv.addSubview_(b)
            return b

        self.haupt_knoepfe = [knopf(i) for i in range(2)]
        self.chips = [knopf(10 + i, chip=True) for i in range(4)]
        self.marke = label(10.5, 0.0, 0.35)
        self.marke.setStringValue_("AI Employee")
        self.tag_zu_knopf: dict[int, str] = {}
        self.panel = panel

    def _knopf_stil(self, b, art: str) -> None:
        f = self._farbe
        if art == "haupt":
            b.layer().setBackgroundColor_(f(*_ACCENT_RGB, 1).CGColor())
            b.setContentTintColor_(f(1, 1, 1, 1))
            b.layer().setBorderWidth_(0)
        elif art == "warn":
            b.layer().setBackgroundColor_(f(0.86, 0.2, 0.2, 0.18).CGColor())
            b.setContentTintColor_(f(1, 0.55, 0.55, 1))
            b.layer().setBorderWidth_(0)
        else:
            b.layer().setBackgroundColor_(f(1, 1, 1, 0.08).CGColor())
            b.layer().setBorderWidth_(1)
            b.layer().setBorderColor_(f(1, 1, 1, 0.12).CGColor())
            b.setContentTintColor_(f(1, 1, 1, 0.85))

    def _zeichnen_haupt(self) -> None:
        try:
            from AppKit import NSImage
            if self.panel is None:
                self._bauen()
            m = _overlay["modell"]
            if not m.sichtbar:
                self.panel.orderOut_(None)
                _sperrzone(None)
                return
            self.kopf.setStringValue_(_OVERLAY_TITEL.get(m.art, ""))
            self.schritte.setStringValue_(
                f"{m.schritte} Schritt{'e' if m.schritte != 1 else ''}" if m.art == "arbeitet" and m.schritte else "")
            self.text.setStringValue_(m.text)
            self.detail.setStringValue_(m.detail)
            self.feld.setHidden_(not m.mit_eingabe)
            if m.mit_eingabe:
                self.eingabe.setStringValue_("")
            alle = m.knoepfe()
            haupt = [(k, t) for k, t in alle if k in self.HAUPT]
            chips = [(k, t) for k, t in alle if k not in self.HAUPT]
            self.tag_zu_knopf = {}
            symbole = {"stopp": "stop.fill", "chat": "bubble.left", "aus": "eye.slash"}
            for i, b in enumerate(self.haupt_knoepfe):
                if i < len(haupt):
                    k, t = haupt[i]
                    b.setTitle_(t)
                    self.tag_zu_knopf[i] = k
                    self._knopf_stil(b, "haupt" if k in ("ja", "weiter") else "neben")
                b.setHidden_(i >= len(haupt))
            for i, b in enumerate(self.chips):
                if i < len(chips):
                    k, t = chips[i]
                    b.setTitle_(t)
                    try:
                        from AppKit import NSImageSymbolConfiguration
                        sym = NSImage.imageWithSystemSymbolName_accessibilityDescription_(symbole.get(k, ""), t)
                        sym = sym.imageWithSymbolConfiguration_(
                            NSImageSymbolConfiguration.configurationWithPointSize_weight_(9.5, 0.0))
                        b.setImage_(sym)
                        b.setImagePosition_(2)
                        b.setImageHugsTitle_(True)
                    except Exception:  # noqa: BLE001
                        pass
                    self.tag_zu_knopf[10 + i] = k
                    self._knopf_stil(b, "warn" if k == "stopp" else "neben")
                b.setHidden_(i >= len(chips))
            self._anordnen(len(haupt), m.mit_eingabe)
            # Kein makeKeyWindow: Tasten, die der Agent gerade tippt, sollen nicht im
            # Overlay landen. Der Mensch klickt ins Feld, wenn er etwas schreiben will.
            self.panel.orderFrontRegardless()
            self._sperrzone_setzen()
        except Exception:  # noqa: BLE001 — ein Zeichenfehler darf die Bridge nicht stoeren
            app_log.warning("Overlay nicht zeichenbar", exc_info=True)

    def _anordnen(self, haupt_anzahl: int, mit_eingabe: bool) -> None:
        """Hoehe nach dem Inhalt, von unten nach oben — nichts wird abgeschnitten."""
        W, rand = self.W, 16
        breite = W - 2 * rand

        def hoehe(lbl) -> float:
            if not str(lbl.stringValue()):
                return 0.0
            return float(lbl.cell().cellSizeForBounds_(((0, 0), (breite, 10000))).height)

        y = 10
        # Fusszeile: Chips links, Marke rechts
        x = rand
        for b in self.chips:
            if b.isHidden():
                continue
            b.sizeToFit()
            w = b.frame().size.width + 18
            b.setFrame_(((x, y), (w, 24)))
            x += w + 6
        self.marke.setFrame_(((W - rand - 80, y + 4), (80, 14)))
        self.marke.setAlignment_(2)
        y += 24 + 10
        # Antwortzeile: Feld + Hauptknoepfe
        if haupt_anzahl or mit_eingabe:
            x_rechts = W - rand
            for b in reversed(self.haupt_knoepfe):
                if b.isHidden():
                    continue
                b.sizeToFit()
                w = max(64, b.frame().size.width + 24)
                x_rechts -= w
                b.setFrame_(((x_rechts, y), (w, 30)))
                x_rechts -= 8
            if mit_eingabe:
                self.feld.setFrame_(((rand, y), (x_rechts - rand, 30)))
                self.eingabe.setFrame_(((12, 6), (x_rechts - rand - 24, 18)))
            y += 30 + 12
        dh, th = hoehe(self.detail), hoehe(self.text)
        if dh:
            self.detail.setFrame_(((rand, y), (breite, dh)))
            y += dh + 6
        self.detail.setHidden_(not dh)
        self.text.setFrame_(((rand, y), (breite, th)))
        y += th + 10
        self.symbol.setFrame_(((rand, y), (18, 18)))
        self.kopf.setFrame_(((rand + 26, y + 1), (300, 16)))
        self.schritte.setFrame_(((W - rand - 120, y + 2), (120, 14)))
        self.schritte.setAlignment_(2)
        H = y + 18 + 12
        alt = self.panel.frame()
        try:
            f = self.panel.screen().visibleFrame() if self.panel.screen() else None
        except Exception:  # noqa: BLE001
            f = None
        if f is not None:
            ox, oben = f.origin.x + (f.size.width - W) / 2, f.origin.y + f.size.height - 8
        else:
            ox, oben = alt.origin.x, alt.origin.y + alt.size.height
        self.panel.setFrame_display_(((ox, oben - H), (W, H)), True)
        self.hinten.setFrame_(((0, 0), (W, H)))
        self.toenung.setFrame_(((0, 0), (W, H)))

    def _sperrzone_setzen(self) -> None:
        """Rahmen in Bridge-Koordinaten (logische Punkte, oben links) umrechnen."""
        from AppKit import NSScreen
        f = self.panel.frame()
        hoehe = NSScreen.screens()[0].frame().size.height
        _sperrzone((f.origin.x, hoehe - (f.origin.y + f.size.height), f.size.width, f.size.height))


_OverlayPanelKlasse = None
_OverlayHandlerKlasse = None


def _sperrzone(rahmen) -> None:
    """Dem Bridge-Dispatcher sagen, wohin der Agent nicht klicken darf."""
    try:
        if rahmen is not None:
            x, y, w, h = rahmen
            rahmen = (x - 8, y - 8, w + 16, h + 16)
        _bridge_module().OVERLAY_SPERRZONE = rahmen
    except Exception:  # noqa: BLE001
        app_log.debug("Sperrzone nicht gesetzt", exc_info=True)


class _TkOverlay:
    """Windows (und Linux): randloses tkinter-Fenster oben mittig, immer im
    Vordergrund. Laeuft in EINEM eigenen Thread mit eigener Schleife; die Bridge
    stoesst nur ueber eine Warteschlange an — tkinter fasst kein fremder Thread an."""

    W = 560

    def __init__(self, cfg: dict) -> None:
        import queue as _queue

        self.cfg = cfg
        self._auftraege: "_queue.Queue[str]" = _queue.Queue()
        self._thread = threading.Thread(target=self._schleife, name="overlay", daemon=True)
        self._thread.start()

    def zeichnen(self) -> None:
        self._auftraege.put("zeichnen")

    def _schleife(self) -> None:
        try:
            import tkinter as tk
        except ImportError:
            return
        root = tk.Tk()
        root.withdraw()
        root.overrideredirect(True)
        root.attributes("-topmost", True)
        try:
            root.attributes("-alpha", 0.97)
        except Exception:  # noqa: BLE001
            pass
        # Dieselbe Gestalt wie auf dem Mac: dunkle Leiste mit Markenrand, Kopfzeile
        # mit Symbol und Schritten, Text, Auftrag, Antwortzeile, kleine Knoepfe unten.
        hinten, vorne, gedimmt, akzent = "#141a24", "#f2f4f7", "#8f97a3", "#0378c7"
        rahmen = tk.Frame(root, bg=hinten, highlightthickness=1, highlightbackground=akzent)
        rahmen.pack(fill="both", expand=True)
        kopfzeile = tk.Frame(rahmen, bg=hinten)
        kopfzeile.pack(fill="x", padx=16, pady=(12, 0))
        self._symbol_bild = None
        pfad = _asset_pfad("app-symbol-1024.png")
        if pfad:
            try:
                from PIL import Image, ImageTk
                self._symbol_bild = ImageTk.PhotoImage(Image.open(pfad).resize((18, 18)), master=root)
                tk.Label(kopfzeile, image=self._symbol_bild, bg=hinten).pack(side="left", padx=(0, 8))
            except Exception:  # noqa: BLE001
                pass
        kopf = tk.Label(kopfzeile, text="", bg=hinten, fg=vorne, font=("Segoe UI Semibold", 10), anchor="w")
        kopf.pack(side="left")
        schritte = tk.Label(kopfzeile, text="", bg=hinten, fg=gedimmt, font=("Segoe UI", 9))
        schritte.pack(side="right")
        text = tk.Label(rahmen, text="", bg=hinten, fg=vorne, font=("Segoe UI", 11), anchor="w",
                        justify="left", wraplength=self.W - 32)
        text.pack(fill="x", padx=16, pady=(8, 2))
        detail = tk.Label(rahmen, text="", bg=hinten, fg=gedimmt, font=("Segoe UI", 9), anchor="w",
                          justify="left", wraplength=self.W - 32)
        detail.pack(fill="x", padx=16, pady=(0, 8))
        antwortzeile = tk.Frame(rahmen, bg=hinten)
        antwortzeile.pack(fill="x", padx=16, pady=(0, 8))
        eingabe = tk.Entry(antwortzeile, bg="#222a36", fg=vorne, insertbackground=vorne, relief="flat",
                           font=("Segoe UI", 10), highlightthickness=1, highlightbackground="#2f3846",
                           highlightcolor=akzent)
        haupt_rahmen = tk.Frame(antwortzeile, bg=hinten)
        haupt_rahmen.pack(side="right")
        fuss = tk.Frame(rahmen, bg=hinten)
        fuss.pack(fill="x", padx=16, pady=(0, 12))
        chip_rahmen = tk.Frame(fuss, bg=hinten)
        chip_rahmen.pack(side="left")
        tk.Label(fuss, text="AI Employee", bg=hinten, fg="#5c6470", font=("Segoe UI", 8)).pack(side="right")

        def klick(schluessel):
            _overlay_klick(self.cfg, schluessel, eingabe.get())

        def neu_zeichnen():
            m = _overlay["modell"]
            if not m.sichtbar:
                root.withdraw()
                _sperrzone(None)
                return
            kopf.configure(text=_OVERLAY_TITEL.get(m.art, ""))
            schritte.configure(text=(f"{m.schritte} Schritt{'e' if m.schritte != 1 else ''}"
                                     if m.art == "arbeitet" and m.schritte else ""))
            text.configure(text=m.text)
            detail.configure(text=m.detail)
            if m.detail:
                detail.pack(fill="x", padx=16, pady=(0, 8), after=text)
            else:
                detail.pack_forget()
            if m.mit_eingabe:
                eingabe.delete(0, "end")
                eingabe.pack(side="left", fill="x", expand=True, padx=(0, 10), ipady=5)
            else:
                eingabe.pack_forget()
            for kind in list(haupt_rahmen.winfo_children()) + list(chip_rahmen.winfo_children()):
                kind.destroy()
            haupt = [(k, b) for k, b in m.knoepfe() if k in ("ja", "nein", "weiter")]
            chips = [(k, b) for k, b in m.knoepfe() if k not in ("ja", "nein", "weiter")]
            for schluessel, beschriftung in haupt:
                farbe = akzent if schluessel in ("ja", "weiter") else "#2a3240"
                tk.Button(haupt_rahmen, text=beschriftung, bg=farbe, fg="white", relief="flat", bd=0,
                          activebackground=farbe, activeforeground="white", padx=16, pady=4, takefocus=0,
                          font=("Segoe UI Semibold", 10), command=lambda s=schluessel: klick(s)
                          ).pack(side="left", padx=(8, 0))
            if haupt:
                antwortzeile.pack(fill="x", padx=16, pady=(0, 8), before=fuss)
            elif not m.mit_eingabe:
                antwortzeile.pack_forget()
            for schluessel, beschriftung in chips:
                rot = schluessel == "stopp"
                tk.Button(chip_rahmen, text=("■  " if rot else "") + beschriftung,
                          bg="#3a1f24" if rot else "#222a36", fg="#ff8f8f" if rot else "#d6dbe2",
                          activebackground="#4a252b" if rot else "#2c3542", relief="flat", bd=0,
                          padx=10, pady=2, takefocus=0, font=("Segoe UI", 9),
                          command=lambda s=schluessel: klick(s)).pack(side="left", padx=(0, 6))
            root.update_idletasks()
            x = (root.winfo_screenwidth() - self.W) // 2
            root.geometry(f"{self.W}x{root.winfo_reqheight()}+{x}+8")
            root.deiconify()
            root.lift()
            root.attributes("-topmost", True)
            root.update_idletasks()
            _aus_aufnahmen_nehmen(root)
            _sperrzone((root.winfo_rootx(), root.winfo_rooty(), root.winfo_width(), root.winfo_height()))

        def abholen():
            import queue as _queue
            try:
                while True:
                    self._auftraege.get_nowait()
                    neu_zeichnen()
            except _queue.Empty:
                pass
            except Exception:  # noqa: BLE001
                app_log.warning("Overlay nicht zeichenbar", exc_info=True)
            root.after(150, abholen)

        root.after(150, abholen)
        root.mainloop()


def _aus_aufnahmen_nehmen(root) -> None:
    """Windows 10 (2004) und neuer: WDA_EXCLUDEFROMCAPTURE — das Overlay fehlt in
    jeder Bildschirmaufnahme, wie NSWindowSharingNone auf dem Mac."""
    if not IS_WIN:
        return
    try:
        import ctypes
        user32 = ctypes.windll.user32
        hwnd = user32.GetParent(root.winfo_id()) or root.winfo_id()
        ok = bool(user32.SetWindowDisplayAffinity(hwnd, 0x11))
    except Exception:  # noqa: BLE001
        ok = False
    if not ok:
        # Aeltere Windows-Version: die Bridge deckt die Flaeche im Bild selbst ab.
        app_log.info("Overlay nicht aus Aufnahmen nehmbar — wird im Bild abgedeckt")
    try:
        _bridge_module().OVERLAY_ABDECKEN = not ok
    except Exception:  # noqa: BLE001
        pass


# ── Symbole (Menueleiste / Tray) ──────────────────────────────────────────────

SYMBOL_ZUSTAENDE = ("getrennt", "verbindet", "verbunden", "sieht_mit", "denkt")
_TEXT_SYMBOL = {"getrennt": "○", "verbindet": "◐", "verbunden": "●", "sieht_mit": "◉", "denkt": "◉"}


def tray_zustand(status: str, verbindet: bool, sieht_mit: bool, denkt: bool = False) -> str:
    """Welches Symbol gerade gilt. „sieht_mit“ nur bei stehender Verbindung — der
    Mensch soll auf einen Blick sehen, dass die Bridge gerade selbst hinschaut;
    „denkt“, solange der Agent einen Blick prueft (Mitdenken)."""
    if status == "connected":
        if sieht_mit and denkt:
            return "denkt"
        return "sieht_mit" if sieht_mit else "verbunden"
    if verbindet or status == "connecting":
        return "verbindet"
    return "getrennt"


def _sieht_mit() -> bool:
    b = _overlay.get("bridge")
    return bool(b is not None and getattr(getattr(b, "observer", None), "beobachten", False))


def _asset_pfad(name: str) -> str | None:
    """Datei aus assets/ — im Bundle (PyInstaller) oder neben dem Quelltext."""
    kandidaten = []
    if getattr(sys, "frozen", False):
        if getattr(sys, "_MEIPASS", None):
            kandidaten.append(Path(sys._MEIPASS))
        inhalt = Path(sys.executable).parent.parent
        kandidaten += [inhalt / "Resources", inhalt / "Frameworks", Path(sys.executable).parent]
    kandidaten.append(Path(__file__).parent)
    for k in kandidaten:
        pfad = k / "assets" / name
        if pfad.exists():
            return str(pfad)
    return None


def mitdenken_umschalten(cfg: dict) -> bool:
    """Mitdenken an/aus (Tray-Menue). Speichert und gibt es sofort an den Server."""
    cfg["mitdenken"] = not bool(cfg.get("mitdenken"))
    save_config(cfg)
    if cfg.get("url") and cfg.get("token") and cfg.get("session"):
        def _senden():
            try:
                api_update_capabilities(cfg["url"], cfg["token"], cfg["session"],
                                        cfg.get("allowed_capabilities", sorted(DEFAULT_CAPABILITIES)), cfg)
            except Exception as e:  # noqa: BLE001
                _notify(f"Mitdenken nicht übertragen: {e}")
        threading.Thread(target=_senden, daemon=True).start()
    if cfg["mitdenken"] and "observe" not in (cfg.get("allowed_capabilities") or []):
        _notify("Mitdenken braucht die Freigabe „Mitsehen“ — unter Berechtigungen einschalten.")
    return cfg["mitdenken"]


# ── macOS menu bar (rumps) ─────────────────────────────────────────────────────

def run_macos(cfg: dict) -> None:
    try:
        import rumps
    except ImportError:
        print("Install rumps: pip install rumps"); sys.exit(1)

    class BridgeApp(rumps.App):
        def __init__(self):
            super().__init__("AI Employee", quit_button=None)
            self.cfg = load_config()
            self._needs_login = False
            self._connecting = False
            # Das Hauptfenster braucht Verbinden/Anmelden aus dem Tray-Kontext —
            # ueber diese Callbacks, damit es EINE Verbindungslogik gibt.
            _main_state["connect"] = lambda: threading.Thread(
                target=self._connect, daemon=True).start()
            _main_state["settings"] = lambda: self.on_settings(None)
            # Die Bridge ist eine App, kein Tray-Anhaengsel: beim Start zeigt
            # sie ihr Fenster. Nicht hier im __init__ (der Run-Loop laeuft noch
            # nicht) — der Timer unten holt es beim ersten Tick nach.
            self._main_shown = False
            self._update_icon()
            if self.cfg.get("auto_connect") and self.cfg.get("token") and self.cfg.get("session"):
                threading.Thread(target=self._connect, daemon=True).start()

        @rumps.clicked("Öffnen")
        def on_open_main(self, _):
            show_main_window(self.cfg)

        def _update_icon(self):
            zustand = tray_zustand(_status, self._connecting, _sieht_mit(), _overlay["modell"].denkt)
            pfad = _asset_pfad(f"menueleiste-{zustand}.png")
            if pfad:
                # Template: macOS faerbt das Symbol selbst fuer helle und dunkle Leisten.
                if getattr(self, "_symbol", None) != pfad:
                    self.template = True
                    self.icon = pfad
                    self.title = None
                    self._symbol = pfad
            else:
                self.title = _TEXT_SYMBOL[zustand]
            self._sync_menu()

        def _sync_menu(self):
            connected = is_running()
            connecting = _status == "connecting" or self._connecting
            configured = bool(self.cfg.get("url") and self.cfg.get("token") and self.cfg.get("session"))
            try:
                # EINE Ableitung mit dem Hauptfenster (_main_status_parts). Und den
                # TITEL setzen: rumps' Menu.__setitem__ tut bei vorhandenem Schluessel
                # nichts — die Zeile blieb so fuer immer beim Stand des App-Starts
                # stehen („nicht verbunden“, waehrend das Fenster „Verbunden“ zeigte).
                text = "Status: " + _main_status_parts()[0]
                if "Status:" in self.menu:
                    self.menu["Status:"].title = text
                else:
                    self.menu["Status:"] = text
            except Exception:
                app_log.debug("Statuszeile nicht aktualisierbar", exc_info=True)
            for title, enabled in {
                "Öffnen": True,
                "Verbinden": not connected,
                "Trennen": connected or connecting,
                "Berechtigungen…": True,
                "Interaction Bar": True,
                "Mitdenken": True,
                "Status": True,
                "Einstellungen…": True,
                "AI-Employee öffnen": True,
                "Beenden": True,
            }.items():
                try:
                    self.menu[title].enabled = enabled
                except Exception:
                    pass
            try:
                self.menu["Mitdenken"].state = 1 if self.cfg.get("mitdenken") else 0
            except Exception:
                pass

        def _connect(self):
            global _status
            self._connecting = True
            self._update_icon()
            result = ensure_session(self.cfg)
            if result == ENSURE_NEEDS_LOGIN:
                _status = "error: token abgelaufen — bitte neu anmelden"
                self._connecting = False
                self._update_icon()
                # Signal main thread to open settings
                self._needs_login = True
                return
            if result != ENSURE_OK:
                _status = "error: server nicht erreichbar"
                self._connecting = False
                self._update_icon()
                return
            start_bridge(self.cfg)
            self._connecting = False
            self._update_icon()

        @rumps.clicked("Verbinden")
        def on_connect(self, _):
            if not self.cfg.get("url") or not self.cfg.get("token"):
                self.on_settings(None); return
            threading.Thread(target=self._connect, daemon=True).start()

        @rumps.clicked("Trennen")
        def on_disconnect(self, _):
            stop_bridge(); self._update_icon()

        @rumps.clicked("Berechtigungen…")
        def on_permissions(self, _):
            show_permissions_dialog(self.cfg)
            self.cfg = load_config()

        @rumps.clicked("Interaction Bar")
        def on_interaction_bar(self, _):
            show_interaction_bar(self.cfg)

        @rumps.clicked("Mitdenken")
        def on_mitdenken(self, sender):
            # Haken = der Agent sieht bei deutlichen Bildschirmwechseln mit (#1019).
            sender.state = 1 if mitdenken_umschalten(self.cfg) else 0

        @rumps.clicked("Einstellungen…")
        def on_settings(self, _):
            updated = show_setup_dialog(self.cfg)
            if updated:
                # ZUSAMMENFUEHREN, nicht ersetzen. Der Dialog liefert nur die
                # Felder, die er selbst kennt — `self.cfg = updated` warf alles
                # andere weg: Freigabelisten fuer Anwendungen und Adressen, die
                # gewaehlte Sprach-Agenten-Kennung, die Stimme. Wer nur die
                # Server-Adresse aendern wollte, verlor damit seine
                # Einschraenkungen. Der Windows-Zweig macht es seit jeher
                # richtig (`cfg.update(u)`).
                self.cfg.update(updated)
                save_config(self.cfg)
                if self.cfg.get("auto_connect"):
                    threading.Thread(target=self._connect, daemon=True).start()
                else:
                    self._update_icon()

        @rumps.clicked("Status")
        def on_status(self, _):
            show_status_window(self.cfg)

        @rumps.clicked("AI-Employee öffnen")
        def on_open(self, _):
            url = self.cfg.get("url", "")
            if url: webbrowser.open(url)

        @rumps.clicked("Beenden")
        def on_quit(self, _):
            stop_bridge(); rumps.quit_application()

        @rumps.timer(3)
        def refresh(self, _):
            # Zugangstoken kurz vor Ablauf still erneuern (sonst 401 nach 30 Minuten
            # bei allen Aufrufen der App: Berechtigungen, Mitdenken, Sitzung).
            threading.Thread(target=token_frisch_halten, args=(self.cfg,), daemon=True).start()
            self._update_icon()
            if not self._main_shown:
                self._main_shown = True
                show_main_window(self.cfg)
            _main_window_refresh(self.cfg)
            if self._needs_login:
                self._needs_login = False
                self.on_settings(None)

    BridgeApp().run()


# ── Windows / Linux (pystray) ──────────────────────────────────────────────────

def run_tray(cfg: dict) -> None:
    try:
        import pystray; from PIL import Image, ImageDraw
    except ImportError:
        sys.exit(1)

    def make_icon(zustand):
        pfad = _asset_pfad(f"tray-{zustand}.png")
        if pfad:
            return Image.open(pfad).convert("RGBA")
        img = Image.new("RGBA", (64, 64), (0,0,0,0))
        farbe = {"verbunden": (34,197,94), "sieht_mit": (6,182,212), "verbindet": (217,119,6)}.get(zustand, (156,163,175))
        ImageDraw.Draw(img).ellipse([8,8,56,56], fill=farbe)
        return img

    def on_connect(icon, item):
        if not cfg.get("token"): on_settings(icon, item); return

        def _connect():
            # Same path macOS already took: verify the stored session is still
            # alive and mint a fresh one if not. Without this, Windows kept
            # dialing a dead session id forever — the server closes with 1008
            # and the reconnect loop retries silently, which looked like
            # "hangs on Verbinde…" and forced a manual new session every time.
            state = ensure_session(cfg)
            if state == ENSURE_NEEDS_LOGIN:
                _notify("Anmeldung abgelaufen — bitte in den Einstellungen neu anmelden.")
                on_settings(icon, item)
                return
            if state == ENSURE_ERROR:
                _notify("Server nicht erreichbar — Verbindung konnte nicht vorbereitet werden.")
                return
            try:
                api_update_capabilities(cfg["url"], cfg["token"], cfg["session"],
                                        cfg.get("allowed_capabilities", sorted(DEFAULT_CAPABILITIES)),
                                        cfg)
            except Exception:  # noqa: BLE001 — capabilities are best-effort
                pass
            start_bridge(cfg)

        threading.Thread(target=_connect, daemon=True).start()

    def on_disconnect(icon, item): stop_bridge()

    # Jeder Dialog laeuft in einem eigenen Thread mit eigenem tkinter-Mainloop.
    # Zwei Klicks im Tray-Menue = zwei Mainloops gleichzeitig — tkinter ist
    # nicht threadsicher, das endet in eingefrorenen Fenstern oder einem
    # stillen Absturz des Tray-Prozesses. Deshalb: solange ein Dialog offen
    # ist, oeffnet kein zweiter.
    dialog_gate = threading.Lock()

    def _one_dialog(fn):
        def _run():
            if not dialog_gate.acquire(blocking=False):
                return
            try:
                fn()
            finally:
                dialog_gate.release()
        threading.Thread(target=_run, daemon=True).start()

    def _settings_inline():
        u = show_setup_dialog(cfg)
        if not u:
            return
        cfg.update(u)
        err = save_config(cfg)
        if err:
            # Silently swallowing this is what made the settings "reset"
            # after every restart — the user has to know they didn't stick.
            _notify(err + "\n\nDie Einstellungen gelten nur bis zum Beenden der App.")

    # Das Hauptfenster ruft Verbinden/Anmelden ueber diese Callbacks auf —
    # dieselbe Logik wie die Tray-Menuepunkte, keine zweite Implementierung.
    # `_settings_inline` laeuft dabei IM Thread des Hauptfensters (tkinter
    # vertraegt keine zwei Mainloops in parallelen Threads).
    _main_state["connect"] = lambda: on_connect(None, None)
    _main_state["settings"] = _settings_inline

    def on_open_main(icon, item): _one_dialog(lambda: show_main_window(cfg))
    def on_permissions(icon, item): _one_dialog(lambda: show_permissions_dialog(cfg))
    def on_interaction(icon, item): _one_dialog(lambda: show_interaction_bar(cfg))
    def on_mitdenken(icon, item): mitdenken_umschalten(cfg)
    def on_settings(icon, item): _one_dialog(_settings_inline)
    def on_status(icon, item): _one_dialog(lambda: show_status_window(cfg))
    def on_open(icon, item):
        if cfg.get("url"): webbrowser.open(cfg["url"])
    def on_quit(icon, item): stop_bridge(); icon.stop()
    def refresh(icon):
        import time
        zuletzt = None
        while True:
            token_frisch_halten(cfg)
            zustand = tray_zustand(_status, False, _sieht_mit(), _overlay["modell"].denkt)
            if zustand != zuletzt:
                icon.icon = make_icon(zustand)
                icon.title = {"getrennt": "AI-Employee Bridge — getrennt", "verbindet": "AI-Employee Bridge — verbindet",
                              "verbunden": "AI-Employee Bridge — verbunden",
                              "sieht_mit": "AI-Employee Bridge — sieht mit",
                              "denkt": "AI-Employee Bridge — Agent schaut hin"}[zustand]
                zuletzt = zustand
            time.sleep(3)

    icon = pystray.Icon("AI-Employee Bridge", make_icon("getrennt"), menu=pystray.Menu(
        # default=True: Doppelklick aufs Tray-Symbol oeffnet das Hauptfenster.
        pystray.MenuItem("Öffnen", on_open_main, default=True),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Verbinden", on_connect),
        pystray.MenuItem("Trennen", on_disconnect),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Berechtigungen…", on_permissions),
        pystray.MenuItem("Interaction Bar", on_interaction),
        pystray.MenuItem("Mitdenken", on_mitdenken, checked=lambda item: bool(cfg.get("mitdenken"))),
        pystray.MenuItem("Einstellungen…", on_settings),
        pystray.MenuItem("Status", on_status),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("AI-Employee öffnen", on_open),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Beenden", on_quit),
    ))
    threading.Thread(target=refresh, args=(icon,), daemon=True).start()
    if cfg.get("auto_connect") and cfg.get("token") and cfg.get("session"):
        try: api_update_capabilities(cfg["url"],cfg["token"],cfg["session"],cfg.get("allowed_capabilities",sorted(DEFAULT_CAPABILITIES)), cfg)
        except: pass
        threading.Thread(target=lambda: start_bridge(cfg), daemon=True).start()
    # Die Bridge ist eine App, kein Tray-Anhaengsel: beim Start zeigt sie ihr
    # Fenster — genau wie auf dem Mac.
    on_open_main(None, None)
    icon.run()


# ── Entry point ────────────────────────────────────────────────────────────────

def main():
    if IS_MAC:
        _install_edit_menu()
    cfg = load_config()
    if not cfg.get("url") or not cfg.get("token") or not cfg.get("session"):
        updated = show_setup_dialog(cfg)
        if not updated: sys.exit(0)
        cfg = updated
        save_config(cfg)
    if IS_MAC:
        run_macos(cfg)
    else:
        run_tray(cfg)


if __name__ == "__main__":
    main()
