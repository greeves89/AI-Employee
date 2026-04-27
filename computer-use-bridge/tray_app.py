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
import json
import os
import platform
import sys
import threading
import ssl
import urllib.error
import urllib.parse
import urllib.request
import webbrowser

_ssl_ctx = ssl.create_default_context()
_ssl_ctx.check_hostname = False
_ssl_ctx.verify_mode = ssl.CERT_NONE
from pathlib import Path

IS_MAC = platform.system() == "Darwin"
IS_WIN = platform.system() == "Windows"

CONFIG_FILE = Path.home() / ".ai_employee_bridge.json"

# ── Capability metadata ────────────────────────────────────────────────────────

CAPABILITY_META = [
    {"id": "screenshots",   "label": "Screenshots",           "desc": "Bildschirminhalt lesen",                  "risk": "gering"},
    {"id": "accessibility", "label": "Accessibility Tree",    "desc": "UI-Elemente lesen (Titel, Rollen, Pos.)", "risk": "gering"},
    {"id": "mouse",         "label": "Maus-Steuerung",        "desc": "Cursor bewegen, klicken, scrollen",       "risk": "mittel"},
    {"id": "keyboard",      "label": "Tastatur-Eingabe",      "desc": "Text schreiben und Shortcuts senden",     "risk": "mittel"},
    {"id": "apps",          "label": "Apps öffnen / schließen","desc": "Anwendungen starten und beenden",        "risk": "mittel"},
    {"id": "clipboard",     "label": "Zwischenablage",        "desc": "Zwischenablage lesen und schreiben",     "risk": "mittel"},
    {"id": "shell",         "label": "Shell-Befehle",         "desc": "Terminal-Befehle ausführen",              "risk": "hoch"},
]

DEFAULT_CAPABILITIES = {c["id"] for c in CAPABILITY_META if c["id"] in
                        {"screenshots", "accessibility", "mouse", "keyboard", "apps"}}

# ── Config ─────────────────────────────────────────────────────────────────────

def load_config() -> dict:
    if CONFIG_FILE.exists():
        try:
            cfg = json.loads(CONFIG_FILE.read_text())
            if "allowed_capabilities" not in cfg:
                cfg["allowed_capabilities"] = sorted(DEFAULT_CAPABILITIES)
            if "allowed_paths" not in cfg:
                cfg["allowed_paths"] = []
            return cfg
        except Exception:
            pass
    return {"url": "", "token": "", "session": "", "auto_connect": True,
            "allowed_capabilities": sorted(DEFAULT_CAPABILITIES), "allowed_paths": []}


def save_config(cfg: dict) -> None:
    CONFIG_FILE.write_text(json.dumps(cfg, indent=2))


# ── HTTP helpers ───────────────────────────────────────────────────────────────

def _api(method, base_url, path, token, body=None):
    url = base_url.rstrip("/") + path
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method,
                                 headers={"Content-Type": "application/json",
                                          "Authorization": f"Bearer {token}",
                                          "User-Agent": "AI-Employee-Bridge/1.0"})
    with urllib.request.urlopen(req, timeout=10, context=_ssl_ctx) as r:
        return json.loads(r.read())


def api_login(base_url, email, password):
    url = base_url.rstrip("/") + "/api/v1/auth/login"
    data = json.dumps({"email": email, "password": password}).encode()
    req = urllib.request.Request(url, data=data,
                                 headers={"Content-Type": "application/json",
                                          "User-Agent": "AI-Employee-Bridge/1.0"})
    with urllib.request.urlopen(req, timeout=10, context=_ssl_ctx) as r:
        return json.loads(r.read())["access_token"]


def api_create_session(base_url, token, caps):
    body = _api("POST", base_url, "/api/v1/computer-use/sessions", token, {})
    sid = body["session_id"]
    try:
        _api("PATCH", base_url, f"/api/v1/computer-use/sessions/{sid}/capabilities",
             token, {"allowed_capabilities": caps})
    except Exception:
        pass
    return sid, caps


def api_update_capabilities(base_url, token, session_id, caps):
    _api("PATCH", base_url, f"/api/v1/computer-use/sessions/{session_id}/capabilities",
         token, {"allowed_capabilities": caps})


def api_session_exists(base_url, token, session_id) -> bool:
    try:
        _api("GET", base_url, f"/api/v1/computer-use/sessions/{session_id}", token)
        return True
    except Exception:
        return False


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
    if sid and api_session_exists(url, token, sid):
        try:
            api_update_capabilities(url, token, sid, caps)
        except Exception:
            pass
        return ENSURE_OK
    # Session gone — try to create a fresh one
    try:
        new_sid, _ = api_create_session(url, token, caps)
        cfg["session"] = new_sid
        save_config(cfg)
        return ENSURE_OK
    except urllib.error.HTTPError as e:
        if e.code in (401, 403):
            return ENSURE_NEEDS_LOGIN
        return ENSURE_ERROR
    except Exception:
        return ENSURE_ERROR


def login_and_prepare(base_url, email, password, caps):
    token = api_login(base_url, email, password)
    session_id, _ = api_create_session(base_url, token, caps)
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
        if not cfg.get("token") or not cfg.get("session"):
            _status = "error: not configured"
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
        _status = "connected"
        asyncio.run(bridge_module.run(url=url, token=token,
                                      session_id=session_id, stop_event=_bridge_stop))
    except Exception as e:
        _status = f"error: {e}"
        import traceback, logging
        logging.getLogger(__name__).error(f"Bridge error:\n{traceback.format_exc()}")
    finally:
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
                url   = st["url_f"].stringValue().strip().rstrip("/")
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
                        token, sid = login_and_prepare(url, email, pw, caps)
                        st["result_box"][0] = {
                            "url": url, "token": token, "session": sid,
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
                save_config(cfg)
                if cfg.get("token") and cfg.get("session") and cfg.get("url") and is_running():
                    st["status_lbl"].setStringValue_("Übertrage an Server…")
                    def _push():
                        try:
                            api_update_capabilities(cfg["url"], cfg["token"], cfg["session"], new_caps)
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
    p.setLevel_(8)  # NSFloatingWindowLevel
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
    from AppKit import NSApp

    W, H = 480, 390
    panel = _make_panel("AI-Employee — Einstellungen", W, H)
    cv = panel.contentView()
    PAD = 24

    _label(cv, "AI-Employee Bridge",    PAD, H-50, W-2*PAD, 28, size=17, bold=True)
    _label(cv, "Verbinde diesen Mac mit deinem AI-Employee Server.",
           PAD, H-72, W-2*PAD, 18, size=12, muted=True)
    _separator(cv, PAD, H-82, W-2*PAD)

    _label(cv, "SERVER URL", PAD, H-108, 120, 14, size=10, bold=True, muted=True)
    url_f  = _input(cv, PAD, H-136, W-2*PAD, "https://agents.example.com", value=cfg.get("url",""))

    _label(cv, "E-MAIL", PAD, H-168, 80, 14, size=10, bold=True, muted=True)
    em_f   = _input(cv, PAD, H-196, W-2*PAD, "name@example.com")

    _label(cv, "PASSWORT", PAD, H-228, 100, 14, size=10, bold=True, muted=True)
    pw_f   = _input(cv, PAD, H-256, W-2*PAD, "••••••••", secure=True)

    _separator(cv, PAD, H-270, W-2*PAD)
    auto_chk   = _checkbox(cv, "Beim Start automatisch verbinden",
                           PAD, H-298, W-2*PAD, cfg.get("auto_connect", True))
    status_lbl = _label(cv, "", PAD, H-324, W-2*PAD, 18, size=12, muted=True)
    cancel_btn = _button(cv, "Abbrechen",            PAD,       16, 100, key="\x1b")
    save_btn   = _button(cv, "Anmelden & Verbinden", W-PAD-180, 16, 180, key="\r")

    result_box = [None]
    _setup_state.update(dict(url_f=url_f, em_f=em_f, pw_f=pw_f, auto_chk=auto_chk,
                             status_lbl=status_lbl, result_box=result_box, cfg=cfg))

    h = _setup_state["_handler"]
    cancel_btn.setTarget_(h); cancel_btn.setAction_("cancel:")
    save_btn.setTarget_(h);   save_btn.setAction_("save:")

    NSApp.runModalForWindow_(panel)
    panel.close()
    return result_box[0]


# ── Permissions Dialog ─────────────────────────────────────────────────────────

def show_permissions_dialog(cfg: dict) -> None:
    if not _appkit_available():
        return _show_permissions_tkinter(cfg)

    _appkit_handlers_init()
    from AppKit import (NSApp, NSScrollView, NSTextView, NSMakeRect, NSFont)

    W, H = 540, 700
    panel = _make_panel("AI-Employee — Berechtigungen", W, H)
    cv = panel.contentView()
    PAD = 24

    _label(cv, "Berechtigungen", PAD, H-46, W-2*PAD, 26, size=17, bold=True)
    _label(cv, "Was darf der Agent auf diesem Mac tun?",
           PAD, H-68, W-2*PAD, 18, size=12, muted=True)
    _separator(cv, PAD, H-78, W-2*PAD)

    cap_checks = {}
    current_caps = set(cfg.get("allowed_capabilities", sorted(DEFAULT_CAPABILITIES)))
    y = H - 78
    for cap in CAPABILITY_META:
        y -= 54
        chk = _checkbox(cv, cap["label"], PAD, y+28, 240, cap["id"] in current_caps)
        chk.setFont_(NSFont.boldSystemFontOfSize_(13))
        cap_checks[cap["id"]] = chk
        _label(cv, cap["desc"], PAD+20, y+10, 280, 16, size=11, muted=True)
        _risk_badge(cv, cap["risk"], W-PAD, y+28)

    _separator(cv, PAD, y-8, W-2*PAD)

    folder_y = y - 32
    _label(cv, "ORDNER-ZUGRIFF", PAD, folder_y, 200, 14, size=10, bold=True, muted=True)
    _label(cv, "(Shell-Befehle sind auf diese Ordner beschränkt)",
           PAD+145, folder_y, W-PAD-165, 14, size=10, muted=True)

    paths = list(cfg.get("allowed_paths", []))
    list_y, list_h = folder_y - 24, 80

    scroll = NSScrollView.alloc().initWithFrame_(NSMakeRect(PAD, list_y - list_h, W-2*PAD, list_h))
    scroll.setHasVerticalScroller_(True)
    scroll.setBorderType_(2)
    tv = NSTextView.alloc().initWithFrame_(NSMakeRect(0, 0, W-2*PAD-4, list_h))
    tv.setEditable_(False)
    tv.setFont_(NSFont.userFixedPitchFontOfSize_(12))
    tv.setString_("\n".join(paths) if paths else "(keine Ordner definiert)")
    scroll.setDocumentView_(tv)
    cv.addSubview_(scroll)

    btn_y = list_y - list_h - 4
    add_btn = _button(cv, "+ Hinzufügen", PAD,       btn_y - 28, 120)
    del_btn = _button(cv, "– Entfernen",  PAD+128,   btn_y - 28, 110)

    _separator(cv, PAD, 56, W-2*PAD)
    status_lbl = _label(cv, "", PAD, 38, W-2*PAD-140, 16, size=11, muted=True)
    cancel_btn = _button(cv, "Abbrechen", PAD,        16, 100, key="\x1b")
    save_btn   = _button(cv, "Speichern", W-PAD-110,  16, 110, key="\r")

    _perms_state.update(dict(cap_checks=cap_checks, paths=paths, tv=tv,
                             status_lbl=status_lbl, cfg=cfg))

    h = _perms_state["_handler"]
    cancel_btn.setTarget_(h); cancel_btn.setAction_("cancel:")
    add_btn.setTarget_(h);    add_btn.setAction_("addPath:")
    del_btn.setTarget_(h);    del_btn.setAction_("delPath:")
    save_btn.setTarget_(h);   save_btn.setAction_("save:")

    NSApp.runModalForWindow_(panel)
    panel.close()


# ── Status Window ──────────────────────────────────────────────────────────────

def show_status_window(cfg: dict) -> None:
    if not _appkit_available():
        return _show_status_tkinter(cfg)

    _appkit_handlers_init()
    from AppKit import NSApp, NSColor

    W = 420
    PAD = 24
    COL = 110   # label column width
    VAL_X = PAD + COL + 12
    VAL_W = W - VAL_X - PAD

    state = _status
    if state == "connected":
        dot_color, state_text = (0.13, 0.76, 0.37, 1), "● Verbunden"
    elif state == "connecting":
        dot_color, state_text = (1.0, 0.62, 0.04, 1), "● Verbinde…"
    else:
        dot_color, state_text = (0.6, 0.6, 0.6, 1), "● " + state.replace("error: ", "")
    dot_col = NSColor.colorWithSRGBRed_green_blue_alpha_(*dot_color)

    caps = cfg.get("allowed_capabilities", [])
    cap_map = {c["id"]: c["label"] for c in CAPABILITY_META}
    caps_str = ", ".join(cap_map.get(c, c) for c in caps) if caps else "Keine"
    paths = cfg.get("allowed_paths", [])

    # Build rows top→down, track y from top
    HEADER_H  = 64   # title + separator
    ROW_H     = 28
    CAPS_H    = 44 if len(caps_str) > 40 else 28
    PATH_H    = 16 * len(paths) + 12 if paths else 0
    FOOTER_H  = 60   # separator + button
    H = HEADER_H + ROW_H * 3 + CAPS_H + (12 + PATH_H if paths else 0) + FOOTER_H

    panel = _make_panel("AI-Employee Bridge — Status", W, H)
    cv = panel.contentView()

    y = H - 38
    _label(cv, "Bridge Status", PAD, y, W - 2*PAD, 22, size=17, bold=True)
    y -= 26
    _separator(cv, PAD, y, W - 2*PAD)
    y -= 10

    def row(lbl, val, h=ROW_H, val_color=None):
        nonlocal y
        y -= h
        _label(cv, lbl, PAD, y, COL, h-2, size=12, muted=True)
        _label(cv, val, VAL_X, y, VAL_W, h-2, size=12, color=val_color)

    row("Verbindung", state_text, val_color=dot_col)
    row("Server",     cfg.get("url") or "—")
    row("Session",    (cfg.get("session") or "—")[:16])
    row("Erlaubt",    caps_str, h=CAPS_H)

    if paths:
        y -= 12
        _separator(cv, PAD, y, W - 2*PAD)
        y -= 4
        row("Ordner", "\n".join(paths), h=PATH_H + 8)

    _separator(cv, PAD, 52, W - 2*PAD)
    close_btn = _button(cv, "Schließen", W - PAD - 100, 16, 100, key="\r")

    h = _status_state["_handler"]
    close_btn.setTarget_(h)
    close_btn.setAction_("close:")

    NSApp.runModalForWindow_(panel)
    panel.close()


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
    root.geometry("480x400")
    root.resizable(False, False)

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

    url_f = field("SERVER URL", "https://agents.example.com", value=cfg.get("url",""))
    em_f  = field("E-MAIL",    "name@example.com")
    pw_f  = field("PASSWORT",  "••••••••", secure=True)

    ctk.CTkFrame(root, height=1, fg_color="#333").pack(fill="x", padx=24, pady=12)

    auto_var = ctk.BooleanVar(value=cfg.get("auto_connect", True))
    ctk.CTkCheckBox(root, text="Beim Start automatisch verbinden", variable=auto_var).pack(anchor="w", padx=24)

    status_lbl = ctk.CTkLabel(root, text="", text_color="gray50", font=ctk.CTkFont(size=11))
    status_lbl.pack(anchor="w", padx=24, pady=(6,0))

    btn_frame = ctk.CTkFrame(root, fg_color="transparent")
    btn_frame.pack(fill="x", padx=24, pady=12)

    def on_cancel(): root.destroy()

    def on_save():
        url   = url_f.get().strip().rstrip("/")
        email = em_f.get().strip()
        pw    = pw_f.get()
        if not url or not email or not pw:
            status_lbl.configure(text="⚠  Bitte alle Felder ausfüllen.", text_color="#f59e0b"); return
        status_lbl.configure(text="Verbinde…", text_color="gray50")
        caps = cfg.get("allowed_capabilities", sorted(DEFAULT_CAPABILITIES))
        def _do():
            try:
                token, sid = login_and_prepare(url, email, pw, caps)
                result.update({"url":url,"token":token,"session":sid,"auto_connect":bool(auto_var.get()),
                               "allowed_capabilities":caps,"allowed_paths":cfg.get("allowed_paths",[])})
                root.after(0, root.destroy)
            except urllib.error.HTTPError:
                root.after(0, lambda: status_lbl.configure(text="⚠  Falsche E-Mail oder Passwort.", text_color="#ef4444"))
            except Exception as e:
                root.after(0, lambda: status_lbl.configure(text=f"⚠  {e}", text_color="#ef4444"))
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

    ctk.CTkLabel(root, text="ORDNER-ZUGRIFF", font=ctk.CTkFont(size=10, weight="bold"), text_color="gray50").pack(anchor="w", padx=24, pady=(0,4))

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

    status_lbl = ctk.CTkLabel(root, text="", text_color="gray50", font=ctk.CTkFont(size=11))
    status_lbl.pack(anchor="w", padx=24)

    bf = ctk.CTkFrame(root, fg_color="transparent")
    bf.pack(fill="x", padx=24, pady=(4,16))

    def on_cancel(): root.destroy()

    def on_save():
        cfg["allowed_capabilities"] = [cid for cid, v in cap_vars.items() if v.get()]
        cfg["allowed_paths"] = paths
        save_config(cfg)
        if is_running():
            status_lbl.configure(text="Übertrage an Server…")
            def _p():
                try:
                    api_update_capabilities(cfg["url"],cfg["token"],cfg["session"],cfg["allowed_capabilities"])
                    root.after(0, lambda: status_lbl.configure(text="✓ Gespeichert", text_color="#22c55e"))
                except Exception as e:
                    root.after(0, lambda: status_lbl.configure(text=f"Lokal gespeichert ({e})", text_color="#f59e0b"))
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
    if state == "connected":
        dot, dot_col, state_text = "●", "#22c55e", "Verbunden"
    elif state == "connecting":
        dot, dot_col, state_text = "●", "#f59e0b", "Verbinde…"
    else:
        dot, dot_col, state_text = "●", "#6b7280", state.replace("error: ", "")

    root = ctk.CTk()
    root.title("AI-Employee Bridge — Status")
    root.geometry("420x320")
    root.resizable(False, False)

    ctk.CTkLabel(root, text="Bridge Status", font=ctk.CTkFont(size=20, weight="bold")).pack(anchor="w", padx=24, pady=(24,2))
    ctk.CTkFrame(root, height=1, fg_color="#333").pack(fill="x", padx=24, pady=10)

    def row(lbl, val, val_color=None):
        r = ctk.CTkFrame(root, fg_color="transparent")
        r.pack(fill="x", padx=24, pady=3)
        ctk.CTkLabel(r, text=lbl, text_color="gray50", width=100, anchor="w", font=ctk.CTkFont(size=12)).pack(side="left")
        ctk.CTkLabel(r, text=val, text_color=val_color or "white", anchor="w", font=ctk.CTkFont(size=12)).pack(side="left", fill="x", expand=True)

    row("Verbindung", f"{dot}  {state_text}", val_color=dot_col)
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
    ttk.Label(f, text="Server URL:").grid(row=0, column=0, sticky="w", pady=3)
    url_v = tk.StringVar(value=cfg.get("url","")); ttk.Entry(f, textvariable=url_v, width=36).grid(row=0, column=1)
    ttk.Label(f, text="E-Mail:").grid(row=1, column=0, sticky="w", pady=3)
    em_v = tk.StringVar(); ttk.Entry(f, textvariable=em_v, width=36).grid(row=1, column=1)
    ttk.Label(f, text="Passwort:").grid(row=2, column=0, sticky="w", pady=3)
    pw_v = tk.StringVar(); ttk.Entry(f, textvariable=pw_v, show="*", width=36).grid(row=2, column=1)
    auto_v = tk.BooleanVar(value=cfg.get("auto_connect",True))
    ttk.Checkbutton(f, text="Automatisch verbinden", variable=auto_v).grid(row=3, column=0, columnspan=2, sticky="w")
    sv = tk.StringVar(); ttk.Label(f, textvariable=sv).grid(row=4, column=0, columnspan=2, sticky="w")
    def save():
        url, em, pw = url_v.get().strip().rstrip("/"), em_v.get().strip(), pw_v.get()
        if not url or not em or not pw: sv.set("Felder ausfüllen!"); return
        sv.set("Verbinde…"); root.update()
        def _do():
            try:
                t, s = login_and_prepare(url, em, pw, cfg.get("allowed_capabilities", sorted(DEFAULT_CAPABILITIES)))
                result.update({"url":url,"token":t,"session":s,"auto_connect":auto_v.get(),"allowed_capabilities":cfg.get("allowed_capabilities",sorted(DEFAULT_CAPABILITIES)),"allowed_paths":cfg.get("allowed_paths",[])})
                root.after(0, root.destroy)
            except Exception as e:
                root.after(0, lambda: sv.set(f"Fehler: {e}"))
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
            threading.Thread(target=lambda: api_update_capabilities(cfg["url"],cfg["token"],cfg["session"],cfg["allowed_capabilities"]), daemon=True).start()
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
    ttk.Label(f, text="● Verbunden" if state=="connected" else f"● {state}").pack(anchor="w")
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
            super().__init__("⬡", quit_button=None)
            self.cfg = load_config()
            self._needs_login = False
            self._update_icon()
            if self.cfg.get("auto_connect") and self.cfg.get("token") and self.cfg.get("session"):
                threading.Thread(target=self._connect, daemon=True).start()

        def _update_icon(self):
            self.title = "🟢" if is_running() else "⬡"

        def _connect(self):
            global _status
            result = ensure_session(self.cfg)
            if result == ENSURE_NEEDS_LOGIN:
                _status = "error: token abgelaufen — bitte neu anmelden"
                self._update_icon()
                # Signal main thread to open settings
                self._needs_login = True
                return
            if result != ENSURE_OK:
                _status = "error: server nicht erreichbar"
                self._update_icon()
                return
            start_bridge(self.cfg)
            self._update_icon()

        @rumps.clicked("Verbinden")
        def on_connect(self, _):
            if not self.cfg.get("token"):
                self.on_settings(None); return
            threading.Thread(target=self._connect, daemon=True).start()

        @rumps.clicked("Trennen")
        def on_disconnect(self, _):
            stop_bridge(); self._update_icon()

        @rumps.clicked("Berechtigungen…")
        def on_permissions(self, _):
            show_permissions_dialog(self.cfg)
            self.cfg = load_config()

        @rumps.clicked("Einstellungen…")
        def on_settings(self, _):
            updated = show_setup_dialog(self.cfg)
            if updated:
                self.cfg = updated
                save_config(updated)

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
        try: api_update_capabilities(cfg["url"],cfg["token"],cfg["session"],cfg.get("allowed_capabilities",sorted(DEFAULT_CAPABILITIES)))
        except: pass
        threading.Thread(target=lambda: start_bridge(cfg), daemon=True).start()

    def on_disconnect(icon, item): stop_bridge()
    def on_permissions(icon, item): threading.Thread(target=lambda: show_permissions_dialog(cfg), daemon=True).start()
    def on_settings(icon, item):
        def _s():
            u = show_setup_dialog(cfg)
            if u: cfg.update(u); save_config(cfg)
        threading.Thread(target=_s, daemon=True).start()
    def on_status(icon, item): threading.Thread(target=lambda: show_status_window(cfg), daemon=True).start()
    def on_open(icon, item):
        if cfg.get("url"): webbrowser.open(cfg["url"])
    def on_quit(icon, item): stop_bridge(); icon.stop()
    def refresh(icon):
        import time
        while True: icon.icon = make_icon(is_running()); time.sleep(3)

    icon = pystray.Icon("AI-Employee Bridge", make_icon(False), menu=pystray.Menu(
        pystray.MenuItem("Verbinden", on_connect),
        pystray.MenuItem("Trennen", on_disconnect),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Berechtigungen…", on_permissions),
        pystray.MenuItem("Einstellungen…", on_settings),
        pystray.MenuItem("Status", on_status),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("AI-Employee öffnen", on_open),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Beenden", on_quit),
    ))
    threading.Thread(target=refresh, args=(icon,), daemon=True).start()
    if cfg.get("auto_connect") and cfg.get("token") and cfg.get("session"):
        try: api_update_capabilities(cfg["url"],cfg["token"],cfg["session"],cfg.get("allowed_capabilities",sorted(DEFAULT_CAPABILITIES)))
        except: pass
        threading.Thread(target=lambda: start_bridge(cfg), daemon=True).start()
    icon.run()


# ── Entry point ────────────────────────────────────────────────────────────────

def main():
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
