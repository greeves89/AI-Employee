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
    {"id": "input_capture", "label": "Eingaben mitschneiden", "desc": "Deine Klicks und Tasten aufzeichnen (Replay)", "risk": "hoch"},
    {"id": "voice_capture", "label": "Mikrofon",              "desc": "Mikrofon mithören (nur zwischen Start und Stopp)", "risk": "hoch"},
    {"id": "shell",         "label": "Shell-Befehle",         "desc": "Terminal-Befehle in freigegebenen Ordnern (siehe Ordner-Zugriff)", "risk": "hoch"},
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


def api_login(base_url, email, password):
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
        return json.loads(r.read())["access_token"]


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


ENSURE_OK        = "ok"
ENSURE_NEEDS_LOGIN = "needs_login"
ENSURE_ERROR     = "error"

def ensure_session(cfg: dict) -> str:
    """Verify session is still alive; create new one if gone. Returns ENSURE_* constant."""
    url, token = cfg.get("url", ""), cfg.get("token", "")
    if not url or not token:
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
            cfg["token"] = ""
            cfg["session"] = ""
            save_config(cfg)
            return ENSURE_NEEDS_LOGIN
    except Exception:
        pass
    if sid and api_session_exists(url, token, sid):
        try:
            api_update_capabilities(url, token, sid, caps, cfg)
        except Exception:
            pass
        return ENSURE_OK
    # Session gone — try to create a fresh one
    try:
        new_sid, _ = api_create_session(url, token, caps, cfg)
        cfg["session"] = new_sid
        save_config(cfg)
        return ENSURE_OK
    except urllib.error.HTTPError as e:
        if e.code in (401, 403):
            cfg["token"] = ""
            cfg["session"] = ""
            save_config(cfg)
            return ENSURE_NEEDS_LOGIN
        return ENSURE_ERROR
    except Exception:
        return ENSURE_ERROR


def login_and_prepare(base_url, email, password, caps, requested_session_id=None, cfg=None):
    token = api_login(base_url, email, password)
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


def start_bridge(cfg):
    global _bridge_thread, _status
    with _bridge_lock:
        if _bridge_thread and _bridge_thread.is_alive():
            return True
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

        asyncio.run(bridge_module.run(url=url, token=token,
                                      session_id=session_id, stop_event=_bridge_stop,
                                      on_state=_on_state))
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
    f = cls.alloc().initWithFrame_(((x, y), (w, 26)))
    f.cell().setPlaceholderString_(placeholder)
    f.setFont_(NSFont.systemFontOfSize_(13))
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
    from AppKit import NSButton
    b = NSButton.alloc().initWithFrame_(((x, y), (w, h)))
    b.setTitle_(title)
    b.setBezelStyle_(style)
    if key:
        b.setKeyEquivalent_(key)
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
        "AI Employee",
        "Desktop-App und sichere Computer-Use Bridge fuer deinen Mac.",
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
    _label(cv, "Bridge Connection URL oder Server", inner_x, card_y + card_h - 50, input_w, 16, size=11, bold=True, muted=True)
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

    NSApp.activateIgnoringOtherApps_(True)
    NSApp.runModalForWindow_(panel)
    panel.close()
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
    caps_h = CAP_ROW_H * len(CAPABILITY_META) + 30
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
    _label(cv, "Startordner fuer Shell-Befehle. Ohne Eintrag sind Shell-Befehle gesperrt.",
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

    add_btn = _button(cv, "+ Hinzufuegen", folder_card_x + folder_card_w - 132, folder_card_y + 56, 112)
    del_btn = _button(cv, "Entfernen", folder_card_x + folder_card_w - 132, folder_card_y + 22, 112)

    status_lbl = _label(cv, "", PAD, 52, W-2*PAD-140, 16, size=11, muted=True)
    cancel_btn = _button(cv, "Abbrechen", PAD, 20, 110, key="\x1b")
    save_btn   = _button(cv, "Speichern", W-PAD-120, 20, 120, key="\r")

    _perms_state.update(dict(cap_checks=cap_checks, paths=paths, tv=tv,
                             apps_tv=apps_tv, dom_tv=dom_tv,
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
    H = 470
    PAD = 34
    COL = 126
    VAL_X = PAD + COL + 14
    VAL_W = W - VAL_X - PAD

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
    card_w, card_h = W - 2 * PAD, 290
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
        row("Ordner", "\n".join(paths), h=min(70, 18 * len(paths) + 18))

    close_btn = _button(cv, "Schliessen", W - PAD - 110, 22, 110, key="\r")

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


def show_main_window(cfg: dict) -> None:
    """Das macOS-Hauptfenster — nicht-modal, einmalig, live aktualisiert."""
    if not _appkit_available():
        return _show_main_window_ctk(cfg)

    from AppKit import NSApp, NSMakeRect, NSObject, NSVisualEffectView

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
    W, H = 680, 560
    PAD = 32
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

    _header(cv, "AI Employee Bridge",
            "Dein Rechner, sicher verbunden mit deinen Agenten.",
            "cpu", PAD, H - 96, W - 2 * PAD)

    # ── Karte: Verbindung ────────────────────────────────────────────────
    card_x, card_w = PAD, W - 2 * PAD
    conn_y, conn_h = H - 330, 210
    _card(cv, card_x, conn_y, card_w, conn_h)
    _label(cv, "Verbindung", card_x + 22, conn_y + conn_h - 34, 200, 18, size=13, bold=True)
    text, farbe = _main_status_parts()
    badge = _badge(cv, f"● {text}", card_x + card_w - 240, conn_y + conn_h - 34, farbe)

    def _row(lbl, val, y):
        _label(cv, lbl, card_x + 24, y, 90, 16, size=12, muted=True)
        return _label(cv, val, card_x + 124, y, card_w - 150, 16, size=12)

    server_lbl = _row("Server", cfg.get("url") or "—", conn_y + conn_h - 68)
    session_lbl = _row("Session", (cfg.get("session") or "—")[:20], conn_y + conn_h - 92)
    _row("Version", f"Bridge v{BRIDGE_VERSION}", conn_y + conn_h - 116)
    cap_map = {c["id"]: c["label"] for c in CAPABILITY_META}
    caps = cfg.get("allowed_capabilities", [])
    caps_lbl = _row("Erlaubt", ", ".join(cap_map.get(c, c) for c in caps) or "Keine",
                    conn_y + conn_h - 140)

    connect_btn = _button(cv, "Verbinden", card_x + 20, conn_y + 16, 130)
    disconnect_btn = _button(cv, "Trennen", card_x + 160, conn_y + 16, 110)

    # ── Karte: Arbeiten ──────────────────────────────────────────────────
    act_y, act_h = conn_y - 96, 82
    _card(cv, card_x, act_y, card_w, act_h)
    _label(cv, "Arbeiten", card_x + 22, act_y + act_h - 30, 200, 18, size=13, bold=True)
    voice_btn = _button(cv, "Voice starten", card_x + 20, act_y + 14, 140)
    perms_btn = _button(cv, "Berechtigungen…", card_x + 170, act_y + 14, 150)
    settings_btn = _button(cv, "Einstellungen…", card_x + 330, act_y + 14, 140)
    web_btn = _button(cv, "Web-UI öffnen", card_x + 480, act_y + 14, 130)

    hint = _label(cv, "Die Bridge läuft im Hintergrund weiter, wenn du dieses Fenster schließt.",
                  PAD, 24, W - 2 * PAD, 16, size=11, muted=True)
    del hint

    class _MainHandler(NSObject):
        def doconnect_(self, _s):
            cb = _main_state.get("connect")
            if cb:
                cb()

        def dodisconnect_(self, _s):
            stop_bridge()
            _main_window_refresh(cfg)

        def dovoice_(self, _s):
            show_interaction_bar(cfg)

        def doperms_(self, _s):
            show_permissions_dialog(cfg)
            cfg.update(load_config())
            _main_window_refresh(cfg)

        def dosettings_(self, _s):
            cb = _main_state.get("settings")
            if cb:
                cb()

        def doweb_(self, _s):
            if cfg.get("url"):
                webbrowser.open(cfg["url"])

    handler = _MainHandler.alloc().init()
    for btn, action in ((connect_btn, "doconnect:"), (disconnect_btn, "dodisconnect:"),
                        (voice_btn, "dovoice:"), (perms_btn, "doperms:"),
                        (settings_btn, "dosettings:"), (web_btn, "doweb:")):
        btn.setTarget_(handler)
        btn.setAction_(action)

    _main_state.update({
        "panel": panel, "handler": handler, "badge": badge,
        "server_lbl": server_lbl, "session_lbl": session_lbl, "caps_lbl": caps_lbl,
        "cap_map": cap_map,
    })

    NSApp.activateIgnoringOtherApps_(True)
    panel.orderFrontRegardless()
    panel.makeKeyAndOrderFront_(None)


def _main_window_refresh(cfg: dict) -> None:
    """Badge und Zeilen des Hauptfensters an den echten Zustand angleichen.
    Wird vom rumps-Timer alle 3 s aufgerufen — nur wenn das Fenster offen ist."""
    panel = _main_state.get("panel")
    if panel is None:
        return
    try:
        if not panel.isVisible():
            return
        from AppKit import NSColor
        text, farbe = _main_status_parts()
        colors = {"green": (0.13, 0.76, 0.37, 1), "amber": (1.0, 0.62, 0.04, 1),
                  "red": (1.0, 0.27, 0.23, 1), "gray": (0.55, 0.55, 0.58, 1)}
        r, g, b, a = colors.get(farbe, colors["gray"])
        badge = _main_state.get("badge")
        badge.setStringValue_(f"● {text}")
        badge.setTextColor_(NSColor.colorWithSRGBRed_green_blue_alpha_(r, g, b, a))
        _main_state["server_lbl"].setStringValue_(cfg.get("url") or "—")
        _main_state["session_lbl"].setStringValue_((cfg.get("session") or "—")[:20])
        cap_map = _main_state.get("cap_map") or {}
        caps = cfg.get("allowed_capabilities", [])
        _main_state["caps_lbl"].setStringValue_(
            ", ".join(cap_map.get(c, c) for c in caps) or "Keine")
    except Exception:  # noqa: BLE001 — ein kaputtes Fenster darf das Tray nicht mitreissen
        _main_state.clear()


def _show_main_window_ctk(cfg: dict) -> None:
    """Das Windows-Hauptfenster — Tabs statt verstreuter Dialoge."""
    if not _ctk_available():
        return _show_status_plain_tkinter(cfg)
    ctk = _ctk_setup()

    root = ctk.CTk()
    root.title("AI Employee Bridge")
    root.geometry("760x560")
    root.minsize(680, 500)

    head = ctk.CTkFrame(root, fg_color="transparent")
    head.pack(fill="x", padx=24, pady=(20, 0))
    ctk.CTkLabel(head, text="AI Employee Bridge",
                 font=ctk.CTkFont(size=22, weight="bold")).pack(side="left")
    status_badge = ctk.CTkLabel(head, text="●", font=ctk.CTkFont(size=13))
    status_badge.pack(side="right")

    tabs = ctk.CTkTabview(root, corner_radius=12)
    tabs.pack(fill="both", expand=True, padx=20, pady=14)
    tab_ueber = tabs.add("Übersicht")
    tab_voice = tabs.add("Voice")

    # ── Übersicht ────────────────────────────────────────────────────────
    grid = ctk.CTkFrame(tab_ueber, fg_color="#1e1e2e", corner_radius=10)
    grid.pack(fill="x", padx=8, pady=8)

    rows = {}
    for i, (lbl, val) in enumerate((
        ("Server", cfg.get("url") or "—"),
        ("Session", (cfg.get("session") or "—")[:20]),
        ("Version", f"Bridge v{BRIDGE_VERSION}"),
        ("Erlaubt", ""),
    )):
        ctk.CTkLabel(grid, text=lbl, text_color="gray50", width=90, anchor="w",
                     font=ctk.CTkFont(size=12)).grid(row=i, column=0, sticky="w", padx=(16, 6), pady=6)
        rows[lbl] = ctk.CTkLabel(grid, text=val, anchor="w", font=ctk.CTkFont(size=12))
        rows[lbl].grid(row=i, column=1, sticky="w", pady=6)

    btns = ctk.CTkFrame(tab_ueber, fg_color="transparent")
    btns.pack(fill="x", padx=8, pady=(6, 0))

    def _do_connect():
        cb = _main_state.get("connect")
        if cb:
            cb()

    ctk.CTkButton(btns, text="Verbinden", width=130, command=_do_connect).pack(side="left")
    ctk.CTkButton(btns, text="Trennen", width=110, fg_color="#333", hover_color="#444",
                  command=stop_bridge).pack(side="left", padx=8)
    ctk.CTkButton(btns, text="Berechtigungen…", width=150,
                  command=lambda: show_permissions_dialog(cfg)).pack(side="left", padx=8)

    def _do_settings():
        cb = _main_state.get("settings")
        if cb:
            cb()

    ctk.CTkButton(btns, text="Einstellungen…", width=140, command=_do_settings).pack(side="left")
    ctk.CTkButton(btns, text="Web-UI", width=90, fg_color="transparent", border_width=1,
                  border_color="#444", text_color="gray70",
                  command=lambda: cfg.get("url") and webbrowser.open(cfg["url"])).pack(side="left", padx=8)

    ctk.CTkLabel(tab_ueber, text="Die Bridge läuft im Hintergrund weiter, wenn du dieses Fenster schließt.",
                 text_color="gray50", font=ctk.CTkFont(size=11)).pack(anchor="w", padx=8, pady=(14, 0))

    # ── Voice ────────────────────────────────────────────────────────────
    ctk.CTkLabel(tab_voice, text="Direkt mit dem Voice Layer deines Agenten sprechen.",
                 text_color="gray60", font=ctk.CTkFont(size=12)).pack(anchor="w", padx=8, pady=(8, 2))
    ctk.CTkButton(tab_voice, text="Voice-Leiste öffnen", width=170,
                  command=lambda: show_interaction_bar(cfg)).pack(anchor="w", padx=8, pady=8)

    def _tick():
        text, farbe = _main_status_parts()
        colors = {"green": "#22c55e", "amber": "#f59e0b", "red": "#ef4444", "gray": "#6b7280"}
        status_badge.configure(text=f"● {text}", text_color=colors.get(farbe, "#6b7280"))
        rows["Server"].configure(text=cfg.get("url") or "—")
        rows["Session"].configure(text=(cfg.get("session") or "—")[:20])
        cap_map = {c["id"]: c["label"] for c in CAPABILITY_META}
        rows["Erlaubt"].configure(
            text=", ".join(cap_map.get(c, c) for c in cfg.get("allowed_capabilities", [])) or "Keine")
        root.after(2000, _tick)

    _tick()
    root.mainloop()


# ── Interaction Bar / Voice Mode ─────────────────────────────────────────────

_voice_state: dict = {}


def _voice_ws_url(base_url: str, agent_id: str, token: str) -> str:
    ws_base = base_url.rstrip("/").replace("https://", "wss://").replace("http://", "ws://")
    q = urllib.parse.urlencode({"token": token})
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

        url = _voice_ws_url(cfg["url"], agent_id, cfg["token"])
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

    W, H = 760, 86
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
        status_text = "Keine Agenten gefunden — Anmeldung pruefen"
    status = _label(cv, status_text, 312, 51, 250, 16, size=11, color=color(0.63, 0.70, 0.80, 1))
    transcript = _label(cv, "Drücke Speech und sprich.", 312, 28, 274, 18, size=12, color=color(1, 1, 1, 0.86))
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
            return f"Mikrofon liess sich nicht oeffnen: {e}"
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
    root.geometry("480x560")
    root.minsize(480, 480)
    root.resizable(False, True)

    # Header
    ctk.CTkLabel(root, text="AI-Employee Bridge", font=ctk.CTkFont(size=20, weight="bold")).pack(anchor="w", padx=24, pady=(24, 2))
    ctk.CTkLabel(root, text="Verbinde diesen PC mit deinem AI-Employee Server.", text_color="gray60", font=ctk.CTkFont(size=12)).pack(anchor="w", padx=24)
    ctk.CTkFrame(root, height=1, fg_color="#333").pack(fill="x", padx=24, pady=12)

    def field(label, placeholder, secure=False, value=""):
        ctk.CTkLabel(root, text=label, font=ctk.CTkFont(size=10, weight="bold"), text_color="gray50").pack(anchor="w", padx=24, pady=(4,1))
        e = ctk.CTkEntry(root, placeholder_text=placeholder, show="●" if secure else "", width=432, height=36)
        if value: e.insert(0, value)
        e.pack(padx=24)
        return e

    url_f = field("BRIDGE URL ODER SERVER", "wss://agents.example.com/ws/computer-use/bridge?session_id=...", value=cfg.get("url",""))
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
    ctk.CTkButton(btn_frame, text="Anmelden & Verbinden", width=180, command=on_save).pack(side="right")

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
                result.update({"url":url,"token":t,"session":s,"email":em,"auto_connect":auto_v.get(),"allowed_capabilities":cfg.get("allowed_capabilities",sorted(DEFAULT_CAPABILITIES)),"allowed_paths":cfg.get("allowed_paths",[])})
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
            if is_running():
                self.title = "●"
            elif _status == "connecting" or self._connecting:
                self.title = "◐"
            else:
                self.title = "○"
            self._sync_menu()

        def _sync_menu(self):
            connected = is_running()
            connecting = _status == "connecting" or self._connecting
            configured = bool(self.cfg.get("url") and self.cfg.get("token") and self.cfg.get("session"))
            try:
                self.menu["Status:"] = "Status: " + (
                    "verbunden" if connected else
                    "verbinde..." if connecting else
                    _status.replace("error: ", "") if _status != "disconnected" else
                    "nicht verbunden"
                )
            except Exception:
                pass
            for title, enabled in {
                "Öffnen": True,
                "Verbinden": not connected,
                "Trennen": connected or connecting,
                "Berechtigungen…": True,
                "Interaction Bar": True,
                "Status": True,
                "Einstellungen…": True,
                "AI-Employee öffnen": True,
                "Beenden": True,
            }.items():
                try:
                    self.menu[title].enabled = enabled
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

    def make_icon(connected):
        img = Image.new("RGBA", (64, 64), (0,0,0,0))
        ImageDraw.Draw(img).ellipse([8,8,56,56], fill=(34,197,94) if connected else (156,163,175))
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
    def on_settings(icon, item): _one_dialog(_settings_inline)
    def on_status(icon, item): _one_dialog(lambda: show_status_window(cfg))
    def on_open(icon, item):
        if cfg.get("url"): webbrowser.open(cfg["url"])
    def on_quit(icon, item): stop_bridge(); icon.stop()
    def refresh(icon):
        import time
        while True: icon.icon = make_icon(is_running()); time.sleep(3)

    icon = pystray.Icon("AI-Employee Bridge", make_icon(False), menu=pystray.Menu(
        # default=True: Doppelklick aufs Tray-Symbol oeffnet das Hauptfenster.
        pystray.MenuItem("Öffnen", on_open_main, default=True),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Verbinden", on_connect),
        pystray.MenuItem("Trennen", on_disconnect),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Berechtigungen…", on_permissions),
        pystray.MenuItem("Interaction Bar", on_interaction),
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
