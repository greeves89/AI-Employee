#!/usr/bin/env python3
"""
AI-Employee Computer-Use Bridge — System Tray App

First launch: shows setup dialog (URL + email + password).
Bridge logs in automatically and fetches a session.
Config saved to ~/.ai_employee_bridge.json (no passwords stored — only token).
"""
import json
import os
import platform
import subprocess
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

# ── Config ────────────────────────────────────────────────────────────────────

def load_config() -> dict:
    if CONFIG_FILE.exists():
        try:
            return json.loads(CONFIG_FILE.read_text())
        except Exception:
            pass
    return {"url": "", "token": "", "session": "", "auto_connect": True}


def save_config(cfg: dict) -> None:
    CONFIG_FILE.write_text(json.dumps(cfg, indent=2))


# ── Login + session helpers ───────────────────────────────────────────────────

def api_login(base_url: str, email: str, password: str) -> str:
    """POST /api/v1/auth/login → returns access_token."""
    url = base_url.rstrip("/") + "/api/v1/auth/login"
    data = json.dumps({"email": email, "password": password}).encode()
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=10, context=_ssl_ctx) as resp:
        body = json.loads(resp.read())
        return body["access_token"]


def api_create_session(base_url: str, token: str) -> str:
    """POST /api/v1/computer-use/sessions → returns session_id."""
    url = base_url.rstrip("/") + "/api/v1/computer-use/sessions"
    req = urllib.request.Request(
        url, data=b"{}",
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {token}"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=10, context=_ssl_ctx) as resp:
        body = json.loads(resp.read())
        return body["session_id"]


def login_and_prepare(base_url: str, email: str, password: str) -> tuple[str, str]:
    """Login + create session. Returns (token, session_id)."""
    token = api_login(base_url, email, password)
    session_id = api_create_session(base_url, token)
    return token, session_id


# ── Bridge process ────────────────────────────────────────────────────────────

_bridge_proc: subprocess.Popen | None = None
_bridge_lock = threading.Lock()
_status = "disconnected"


def get_bridge_script() -> str:
    if getattr(sys, "frozen", False):
        base = Path(sys.executable).parent
    else:
        base = Path(__file__).parent
    return str(base / "bridge.py")


def start_bridge(cfg: dict) -> bool:
    global _bridge_proc, _status
    with _bridge_lock:
        if _bridge_proc and _bridge_proc.poll() is None:
            return True
        if not cfg.get("token") or not cfg.get("session"):
            _status = "error: not configured"
            return False
        cmd = [
            sys.executable, get_bridge_script(),
            "--url", cfg["url"],
            "--token", cfg["token"],
            "--session", cfg["session"],
        ]
        try:
            _bridge_proc = subprocess.Popen(
                cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
            )
            _status = "connecting"
            threading.Thread(target=_watch_output, daemon=True).start()
            return True
        except Exception as e:
            _status = f"error: {e}"
            return False


def stop_bridge() -> None:
    global _bridge_proc, _status
    with _bridge_lock:
        if _bridge_proc and _bridge_proc.poll() is None:
            _bridge_proc.terminate()
            _bridge_proc = None
        _status = "disconnected"


def is_running() -> bool:
    return _bridge_proc is not None and _bridge_proc.poll() is None


def _watch_output() -> None:
    global _status
    if not _bridge_proc:
        return
    for line in _bridge_proc.stdout:
        line = line.strip()
        if "Connected" in line:
            _status = "connected"
        elif "Reconnecting" in line or "closed" in line.lower():
            _status = "reconnecting"
        elif "Error" in line:
            _status = "error"


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
    root.title("AI-Employee Bridge Setup")
    root.resizable(False, False)
    root.geometry("460x280")

    frame = ttk.Frame(root, padding=20)
    frame.pack(fill="both", expand=True)

    ttk.Label(frame, text="AI-Employee Bridge Setup", font=("", 14, "bold")).grid(
        row=0, column=0, columnspan=2, pady=(0, 16), sticky="w"
    )

    ttk.Label(frame, text="Server URL:").grid(row=1, column=0, sticky="w", pady=4)
    url_var = tk.StringVar(value=cfg.get("url", ""))
    ttk.Entry(frame, textvariable=url_var, width=38).grid(row=1, column=1, padx=(8, 0), pady=4)

    ttk.Label(frame, text="E-Mail:").grid(row=2, column=0, sticky="w", pady=4)
    email_var = tk.StringVar()
    ttk.Entry(frame, textvariable=email_var, width=38).grid(row=2, column=1, padx=(8, 0), pady=4)

    ttk.Label(frame, text="Passwort:").grid(row=3, column=0, sticky="w", pady=4)
    pw_var = tk.StringVar()
    ttk.Entry(frame, textvariable=pw_var, width=38, show="*").grid(row=3, column=1, padx=(8, 0), pady=4)

    auto_var = tk.BooleanVar(value=cfg.get("auto_connect", True))
    ttk.Checkbutton(frame, text="Automatisch verbinden beim Start", variable=auto_var).grid(
        row=4, column=0, columnspan=2, sticky="w", pady=(8, 0)
    )

    status_var = tk.StringVar(value="")
    status_label = ttk.Label(frame, textvariable=status_var, foreground="gray")
    status_label.grid(row=5, column=0, columnspan=2, sticky="w", pady=(4, 0))

    def on_save():
        url = url_var.get().strip().rstrip("/")
        email = email_var.get().strip()
        pw = pw_var.get()
        if not url or not email or not pw:
            messagebox.showerror("Fehler", "Bitte alle Felder ausfüllen.")
            return
        status_var.set("Verbinde…")
        root.update()
        try:
            token, session_id = login_and_prepare(url, email, pw)
            result["url"] = url
            result["token"] = token
            result["session"] = session_id
            result["auto_connect"] = auto_var.get()
            root.destroy()
        except urllib.error.HTTPError as e:
            status_var.set(f"Fehler: {e.code} — Zugangsdaten prüfen")
        except Exception as e:
            status_var.set(f"Fehler: {e}")

    def on_cancel():
        root.destroy()

    btn_frame = ttk.Frame(frame)
    btn_frame.grid(row=6, column=0, columnspan=2, sticky="e", pady=(12, 0))
    ttk.Button(btn_frame, text="Abbrechen", command=on_cancel).pack(side="right", padx=(4, 0))
    ttk.Button(btn_frame, text="Anmelden & Verbinden", command=on_save).pack(side="right")

    root.mainloop()
    return result if result else None


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
            start_bridge(self.cfg)
            self._update_icon()

        @rumps.clicked("Connect")
        def on_connect(self, _):
            if not self.cfg.get("token"):
                self.on_settings(None)
                return
            threading.Thread(target=self._connect, daemon=True).start()

        @rumps.clicked("Disconnect")
        def on_disconnect(self, _):
            stop_bridge()
            self._update_icon()

        @rumps.clicked("Settings…")
        def on_settings(self, _):
            updated = show_setup_dialog(self.cfg)
            if updated:
                self.cfg = updated
                save_config(updated)

        @rumps.clicked("Open AI-Employee")
        def on_open(self, _):
            url = self.cfg.get("url", "")
            if url:
                webbrowser.open(url)

        @rumps.clicked("Status")
        def on_status(self, _):
            rumps.alert(f"Status: {_status}\nServer: {self.cfg.get('url', '—')}")

        @rumps.clicked("Quit")
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
        threading.Thread(target=lambda: start_bridge(cfg), daemon=True).start()

    def on_disconnect(icon, item):
        stop_bridge()

    def on_settings(icon, item):
        updated = show_setup_dialog(cfg)
        if updated:
            cfg.update(updated)
            save_config(cfg)

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
            pystray.MenuItem("Connect", on_connect),
            pystray.MenuItem("Disconnect", on_disconnect),
            pystray.MenuItem("Settings…", on_settings),
            pystray.MenuItem("Open AI-Employee", on_open),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Quit", on_quit),
        ),
    )
    threading.Thread(target=refresh, args=(icon,), daemon=True).start()
    if cfg.get("auto_connect") and cfg.get("token") and cfg.get("session"):
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
