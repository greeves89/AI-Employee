#!/usr/bin/env python3
"""
AI-Employee Computer-Use Bridge — System Tray App

First launch: shows setup dialog (URL + email + password).
Bridge logs in automatically and fetches a session.
Config saved to ~/.ai_employee_bridge.json (no passwords stored — only token).

Tray menu:
  • Status → shows connection state + capabilities
  • Berechtigungen… → toggle what the agent may do on this machine
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

# ── Capability group metadata (mirrors orchestrator/computer_use.py) ──────────

CAPABILITY_META = [
    {
        "id": "screenshots",
        "label": "Screenshots",
        "desc": "Bildschirminhalt lesen",
        "risk": "gering",
        "default": True,
    },
    {
        "id": "accessibility",
        "label": "Accessibility Tree",
        "desc": "UI-Elemente lesen (Titel, Rollen, Positionen)",
        "risk": "gering",
        "default": True,
    },
    {
        "id": "mouse",
        "label": "Maus-Steuerung",
        "desc": "Cursor bewegen, klicken, scrollen",
        "risk": "mittel",
        "default": True,
    },
    {
        "id": "keyboard",
        "label": "Tastatur-Eingabe",
        "desc": "Text schreiben und Shortcuts senden",
        "risk": "mittel",
        "default": True,
    },
    {
        "id": "apps",
        "label": "Apps öffnen / schließen",
        "desc": "Anwendungen starten und beenden",
        "risk": "mittel",
        "default": True,
    },
    {
        "id": "clipboard",
        "label": "Zwischenablage",
        "desc": "Zwischenablage lesen und schreiben",
        "risk": "mittel",
        "default": False,
    },
    {
        "id": "shell",
        "label": "Shell-Befehle",
        "desc": "Terminal-Befehle ausführen",
        "risk": "hoch",
        "default": False,
    },
]

DEFAULT_CAPABILITIES = {c["id"] for c in CAPABILITY_META if c["default"]}


# ── Config ────────────────────────────────────────────────────────────────────

def load_config() -> dict:
    if CONFIG_FILE.exists():
        try:
            cfg = json.loads(CONFIG_FILE.read_text())
            # Migrate: ensure allowed_capabilities key exists
            if "allowed_capabilities" not in cfg:
                cfg["allowed_capabilities"] = sorted(DEFAULT_CAPABILITIES)
            return cfg
        except Exception:
            pass
    return {
        "url": "",
        "token": "",
        "session": "",
        "auto_connect": True,
        "allowed_capabilities": sorted(DEFAULT_CAPABILITIES),
    }


def save_config(cfg: dict) -> None:
    CONFIG_FILE.write_text(json.dumps(cfg, indent=2))


# ── HTTP helpers ──────────────────────────────────────────────────────────────

def _api(method: str, base_url: str, path: str, token: str, body: dict | None = None) -> dict:
    url = base_url.rstrip("/") + path
    data = json.dumps(body).encode() if body is not None else None
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {token}",
        "User-Agent": "AI-Employee-Bridge/1.0",
    }
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=10, context=_ssl_ctx) as resp:
        return json.loads(resp.read())


def api_login(base_url: str, email: str, password: str) -> str:
    """POST /api/v1/auth/login → returns access_token."""
    url = base_url.rstrip("/") + "/api/v1/auth/login"
    data = json.dumps({"email": email, "password": password}).encode()
    req = urllib.request.Request(url, data=data, headers={
        "Content-Type": "application/json",
        "User-Agent": "AI-Employee-Bridge/1.0",
    })
    with urllib.request.urlopen(req, timeout=10, context=_ssl_ctx) as resp:
        body = json.loads(resp.read())
        return body["access_token"]


def api_create_session(base_url: str, token: str, allowed_capabilities: list[str]) -> tuple[str, list[str]]:
    """POST /api/v1/computer-use/sessions → (session_id, allowed_capabilities)."""
    body = _api("POST", base_url, "/api/v1/computer-use/sessions", token, {})
    # Server returns its own defaults; apply our local caps immediately
    session_id = body["session_id"]
    try:
        _api("PATCH", base_url, f"/api/v1/computer-use/sessions/{session_id}/capabilities", token,
             {"allowed_capabilities": allowed_capabilities})
    except Exception:
        pass
    return session_id, allowed_capabilities


def api_update_capabilities(base_url: str, token: str, session_id: str, caps: list[str]) -> None:
    """PATCH /api/v1/computer-use/sessions/{id}/capabilities."""
    _api("PATCH", base_url, f"/api/v1/computer-use/sessions/{session_id}/capabilities", token,
         {"allowed_capabilities": caps})


def login_and_prepare(base_url: str, email: str, password: str, caps: list[str]) -> tuple[str, str]:
    """Login + create session with given capabilities. Returns (token, session_id)."""
    token = api_login(base_url, email, password)
    session_id, _ = api_create_session(base_url, token, caps)
    return token, session_id


# ── Bridge thread ─────────────────────────────────────────────────────────────

_bridge_thread: threading.Thread | None = None
_bridge_stop = threading.Event()
_bridge_lock = threading.Lock()
_status = "disconnected"


def start_bridge(cfg: dict) -> bool:
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
            daemon=True,
        )
        _bridge_thread.start()
        _status = "connecting"
        return True


def stop_bridge() -> None:
    global _status
    _bridge_stop.set()
    _status = "disconnected"


def is_running() -> bool:
    return _bridge_thread is not None and _bridge_thread.is_alive()


def _run_bridge_thread(url: str, token: str, session_id: str) -> None:
    global _status
    import asyncio
    try:
        bridge_dir = Path(sys.executable).parent if getattr(sys, "frozen", False) else Path(__file__).parent
        if str(bridge_dir) not in sys.path:
            sys.path.insert(0, str(bridge_dir))
        import bridge as bridge_module
        _status = "connected"
        asyncio.run(bridge_module.run(url=url, token=token, session_id=session_id, stop_event=_bridge_stop))
    except Exception as e:
        _status = f"error: {e}"
    finally:
        _status = "disconnected"


# ── Setup dialog ──────────────────────────────────────────────────────────────

def show_setup_dialog(cfg: dict) -> dict | None:
    """URL + Email + Password dialog. Logs in and creates session automatically."""
    try:
        import tkinter as tk
        from tkinter import ttk, messagebox
    except ImportError:
        print("tkinter not available")
        return None

    result = {}
    root = tk.Tk()
    root.title("AI-Employee Bridge — Einstellungen")
    root.resizable(False, False)
    root.geometry("480x320")

    frame = ttk.Frame(root, padding=20)
    frame.pack(fill="both", expand=True)

    ttk.Label(frame, text="AI-Employee Bridge", font=("", 14, "bold")).grid(
        row=0, column=0, columnspan=2, pady=(0, 4), sticky="w"
    )
    ttk.Label(frame, text="Verbinde diesen Rechner mit deinem AI-Employee Server.", foreground="gray").grid(
        row=1, column=0, columnspan=2, pady=(0, 12), sticky="w"
    )

    ttk.Label(frame, text="Server URL:").grid(row=2, column=0, sticky="w", pady=4)
    url_var = tk.StringVar(value=cfg.get("url", ""))
    ttk.Entry(frame, textvariable=url_var, width=40).grid(row=2, column=1, padx=(8, 0), pady=4)

    ttk.Label(frame, text="E-Mail:").grid(row=3, column=0, sticky="w", pady=4)
    email_var = tk.StringVar()
    ttk.Entry(frame, textvariable=email_var, width=40).grid(row=3, column=1, padx=(8, 0), pady=4)

    ttk.Label(frame, text="Passwort:").grid(row=4, column=0, sticky="w", pady=4)
    pw_var = tk.StringVar()
    ttk.Entry(frame, textvariable=pw_var, width=40, show="*").grid(row=4, column=1, padx=(8, 0), pady=4)

    auto_var = tk.BooleanVar(value=cfg.get("auto_connect", True))
    ttk.Checkbutton(frame, text="Beim Start automatisch verbinden", variable=auto_var).grid(
        row=5, column=0, columnspan=2, sticky="w", pady=(8, 0)
    )

    status_var = tk.StringVar(value="")
    ttk.Label(frame, textvariable=status_var, foreground="gray").grid(
        row=6, column=0, columnspan=2, sticky="w", pady=(4, 0)
    )

    def on_save():
        url = url_var.get().strip().rstrip("/")
        email = email_var.get().strip()
        pw = pw_var.get()
        if not url or not email or not pw:
            messagebox.showerror("Fehler", "Bitte alle Felder ausfüllen.")
            return
        status_var.set("Verbinde…")
        root.update()

        caps = cfg.get("allowed_capabilities", sorted(DEFAULT_CAPABILITIES))

        def _do_login():
            try:
                token, session_id = login_and_prepare(url, email, pw, caps)
                result["url"] = url
                result["token"] = token
                result["session"] = session_id
                result["auto_connect"] = auto_var.get()
                result["allowed_capabilities"] = caps
                root.after(0, root.destroy)
            except urllib.error.HTTPError as e:
                root.after(0, lambda: status_var.set(f"Fehler: {e.code} — Zugangsdaten prüfen"))
            except Exception as e:
                root.after(0, lambda: status_var.set(f"Fehler: {e}"))

        threading.Thread(target=_do_login, daemon=True).start()

    def on_cancel():
        root.destroy()

    btn_frame = ttk.Frame(frame)
    btn_frame.grid(row=7, column=0, columnspan=2, sticky="e", pady=(12, 0))
    ttk.Button(btn_frame, text="Abbrechen", command=on_cancel).pack(side="right", padx=(4, 0))
    ttk.Button(btn_frame, text="Anmelden & Verbinden", command=on_save).pack(side="right")

    root.mainloop()
    return result if result else None


# ── Permissions dialog ────────────────────────────────────────────────────────

def show_permissions_dialog(cfg: dict) -> None:
    """Shows capability toggles. Saves to config + pushes to server if connected."""
    try:
        import tkinter as tk
        from tkinter import ttk, messagebox
    except ImportError:
        return

    current_caps: set[str] = set(cfg.get("allowed_capabilities", sorted(DEFAULT_CAPABILITIES)))
    cap_vars: dict[str, tk.BooleanVar] = {}

    root = tk.Tk()
    root.title("AI-Employee Bridge — Berechtigungen")
    root.resizable(False, False)
    root.geometry("520x460")

    frame = ttk.Frame(root, padding=20)
    frame.pack(fill="both", expand=True)

    ttk.Label(frame, text="Berechtigungen", font=("", 14, "bold")).pack(anchor="w")
    ttk.Label(
        frame,
        text="Lege fest, was der Agent auf DIESEM Rechner darf.\nÄnderungen werden sofort an den Server übertragen.",
        foreground="gray",
        justify="left",
    ).pack(anchor="w", pady=(4, 12))

    ttk.Separator(frame, orient="horizontal").pack(fill="x", pady=(0, 8))

    RISK_COLORS_TK = {"gering": "#22c55e", "mittel": "#f59e0b", "hoch": "#ef4444"}

    for cap in CAPABILITY_META:
        row_frame = ttk.Frame(frame)
        row_frame.pack(fill="x", pady=3)

        var = tk.BooleanVar(value=cap["id"] in current_caps)
        cap_vars[cap["id"]] = var

        chk = ttk.Checkbutton(row_frame, variable=var)
        chk.pack(side="left")

        info_frame = tk.Frame(row_frame)
        info_frame.pack(side="left", padx=(6, 0), fill="x", expand=True)

        tk.Label(info_frame, text=cap["label"], font=("", 10, "bold"), anchor="w").pack(anchor="w")
        tk.Label(info_frame, text=cap["desc"], foreground="gray", font=("", 9), anchor="w").pack(anchor="w")

        risk_color = RISK_COLORS_TK.get(cap["risk"], "gray")
        tk.Label(
            row_frame,
            text=f"Risiko: {cap['risk']}",
            foreground=risk_color,
            font=("", 9),
        ).pack(side="right", padx=8)

    ttk.Separator(frame, orient="horizontal").pack(fill="x", pady=(12, 0))

    status_var = tk.StringVar(value="")
    ttk.Label(frame, textvariable=status_var, foreground="gray").pack(anchor="w", pady=(4, 0))

    def on_save():
        new_caps = [cap_id for cap_id, var in cap_vars.items() if var.get()]
        cfg["allowed_capabilities"] = new_caps
        save_config(cfg)

        if cfg.get("token") and cfg.get("session") and cfg.get("url") and is_running():
            status_var.set("Übertrage an Server…")
            root.update()

            def _push():
                try:
                    api_update_capabilities(cfg["url"], cfg["token"], cfg["session"], new_caps)
                    root.after(0, lambda: status_var.set("✓ Gespeichert und übertragen"))
                    root.after(1500, root.destroy)
                except Exception as e:
                    root.after(0, lambda: status_var.set(f"Lokal gespeichert. Fehler beim Übertragen: {e}"))
                    root.after(2000, root.destroy)

            threading.Thread(target=_push, daemon=True).start()
        else:
            status_var.set("✓ Lokal gespeichert (wird beim nächsten Verbinden übertragen)")
            root.after(1500, root.destroy)

    def on_cancel():
        root.destroy()

    btn_frame = ttk.Frame(frame)
    btn_frame.pack(anchor="e", pady=(8, 0))
    ttk.Button(btn_frame, text="Abbrechen", command=on_cancel).pack(side="right", padx=(4, 0))
    ttk.Button(btn_frame, text="Speichern", command=on_save).pack(side="right")

    root.mainloop()


# ── Status window ─────────────────────────────────────────────────────────────

def show_status_window(cfg: dict) -> None:
    """Shows detailed status: connection state, session ID, capabilities."""
    try:
        import tkinter as tk
        from tkinter import ttk
    except ImportError:
        return

    root = tk.Tk()
    root.title("AI-Employee Bridge — Status")
    root.resizable(False, False)
    root.geometry("420x300")

    frame = ttk.Frame(root, padding=20)
    frame.pack(fill="both", expand=True)

    ttk.Label(frame, text="Bridge Status", font=("", 13, "bold")).pack(anchor="w")
    ttk.Separator(frame, orient="horizontal").pack(fill="x", pady=8)

    def row(label: str, value: str):
        r = ttk.Frame(frame)
        r.pack(fill="x", pady=2)
        ttk.Label(r, text=label + ":", width=16, anchor="w", foreground="gray").pack(side="left")
        ttk.Label(r, text=value, anchor="w").pack(side="left", fill="x", expand=True)

    state = _status
    state_display = "🟢 Verbunden" if state == "connected" else ("🟡 Verbinde…" if state == "connecting" else f"⚫ {state}")
    row("Verbindung", state_display)
    row("Server", cfg.get("url") or "—")
    row("Session ID", cfg.get("session") or "—")

    caps = cfg.get("allowed_capabilities", [])
    if caps:
        cap_labels = {c["id"]: c["label"] for c in CAPABILITY_META}
        caps_str = ", ".join(cap_labels.get(c, c) for c in caps)
    else:
        caps_str = "Keine"
    row("Erlaubt", caps_str or "—")

    ttk.Separator(frame, orient="horizontal").pack(fill="x", pady=8)

    btn_frame = ttk.Frame(frame)
    btn_frame.pack(anchor="e")
    ttk.Button(btn_frame, text="Schließen", command=root.destroy).pack()

    root.mainloop()


# ── macOS menu bar (rumps) ────────────────────────────────────────────────────

def run_macos(cfg: dict) -> None:
    try:
        import rumps
    except ImportError:
        print("Install rumps: pip install rumps")
        sys.exit(1)

    class BridgeApp(rumps.App):
        def __init__(self):
            super().__init__("⬡", quit_button=None)
            self.cfg = load_config()
            self._update_icon()
            if self.cfg.get("auto_connect") and self.cfg.get("token") and self.cfg.get("session"):
                threading.Thread(target=self._connect, daemon=True).start()

        def _update_icon(self):
            self.title = "🟢" if is_running() else "⬡"

        def _connect(self):
            if self.cfg.get("token") and self.cfg.get("session"):
                # Push capabilities before connecting
                try:
                    api_update_capabilities(
                        self.cfg["url"], self.cfg["token"], self.cfg["session"],
                        self.cfg.get("allowed_capabilities", sorted(DEFAULT_CAPABILITIES))
                    )
                except Exception:
                    pass
            start_bridge(self.cfg)
            self._update_icon()

        @rumps.clicked("Verbinden")
        def on_connect(self, _):
            if not self.cfg.get("token"):
                self.on_settings(None)
                return
            threading.Thread(target=self._connect, daemon=True).start()

        @rumps.clicked("Trennen")
        def on_disconnect(self, _):
            stop_bridge()
            self._update_icon()

        @rumps.clicked("Berechtigungen…")
        def on_permissions(self, _):
            def _show():
                show_permissions_dialog(self.cfg)
                self.cfg = load_config()
            threading.Thread(target=_show, daemon=True).start()

        @rumps.clicked("Einstellungen…")
        def on_settings(self, _):
            def _show():
                updated = show_setup_dialog(self.cfg)
                if updated:
                    self.cfg = updated
                    save_config(updated)
            threading.Thread(target=_show, daemon=True).start()

        @rumps.clicked("Status")
        def on_status(self, _):
            threading.Thread(target=lambda: show_status_window(self.cfg), daemon=True).start()

        @rumps.clicked("AI-Employee öffnen")
        def on_open(self, _):
            url = self.cfg.get("url", "")
            if url:
                webbrowser.open(url)

        @rumps.clicked("Beenden")
        def on_quit(self, _):
            stop_bridge()
            rumps.quit_application()

        @rumps.timer(3)
        def refresh(self, _):
            self._update_icon()

    BridgeApp().run()


# ── Windows / Linux tray (pystray) ───────────────────────────────────────────

def run_tray(cfg: dict) -> None:
    try:
        import pystray
        from PIL import Image, ImageDraw
    except ImportError:
        sys.exit(1)

    def make_icon(connected: bool):
        size = 64
        img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)
        draw.ellipse([8, 8, size - 8, size - 8], fill=(34, 197, 94) if connected else (156, 163, 175))
        return img

    def on_connect(icon, item):
        if not cfg.get("token"):
            on_settings(icon, item)
            return
        try:
            api_update_capabilities(
                cfg["url"], cfg["token"], cfg["session"],
                cfg.get("allowed_capabilities", sorted(DEFAULT_CAPABILITIES))
            )
        except Exception:
            pass
        threading.Thread(target=lambda: start_bridge(cfg), daemon=True).start()

    def on_disconnect(icon, item):
        stop_bridge()

    def on_permissions(icon, item):
        threading.Thread(target=lambda: show_permissions_dialog(cfg), daemon=True).start()

    def on_settings(icon, item):
        def _show():
            updated = show_setup_dialog(cfg)
            if updated:
                cfg.update(updated)
                save_config(cfg)
        threading.Thread(target=_show, daemon=True).start()

    def on_status(icon, item):
        threading.Thread(target=lambda: show_status_window(cfg), daemon=True).start()

    def on_open(icon, item):
        url = cfg.get("url", "")
        if url:
            webbrowser.open(url)

    def on_quit(icon, item):
        stop_bridge()
        icon.stop()

    def refresh(icon):
        import time
        while True:
            icon.icon = make_icon(is_running())
            time.sleep(3)

    icon = pystray.Icon(
        "AI-Employee Bridge", make_icon(False),
        menu=pystray.Menu(
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
        ),
    )
    threading.Thread(target=refresh, args=(icon,), daemon=True).start()
    if cfg.get("auto_connect") and cfg.get("token") and cfg.get("session"):
        try:
            api_update_capabilities(
                cfg["url"], cfg["token"], cfg["session"],
                cfg.get("allowed_capabilities", sorted(DEFAULT_CAPABILITIES))
            )
        except Exception:
            pass
        threading.Thread(target=lambda: start_bridge(cfg), daemon=True).start()
    icon.run()


# ── Entry point ───────────────────────────────────────────────────────────────

def main():
    cfg = load_config()
    if not cfg.get("url") or not cfg.get("token") or not cfg.get("session"):
        updated = show_setup_dialog(cfg)
        if not updated:
            sys.exit(0)
        cfg = updated
        save_config(cfg)

    if IS_MAC:
        run_macos(cfg)
    else:
        run_tray(cfg)


if __name__ == "__main__":
    main()
