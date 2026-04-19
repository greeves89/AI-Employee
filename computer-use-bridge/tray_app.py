#!/usr/bin/env python3
"""
AI-Employee Computer-Use Bridge — System Tray App

Wraps bridge.py in a menubar/tray icon so it runs as a background app.
- macOS: menu bar icon (top-right)
- Windows: system tray icon (bottom-right)

First launch: shows a setup dialog to enter server URL + token.
Config is saved to ~/.ai_employee_bridge.json
"""
import asyncio
import json
import os
import platform
import subprocess
import sys
import threading
import webbrowser
from pathlib import Path

IS_MAC = platform.system() == "Darwin"
IS_WIN = platform.system() == "Windows"

CONFIG_FILE = Path.home() / ".ai_employee_bridge.json"
ICON_CONNECTED = None
ICON_IDLE = None

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


# ── Bridge process management ─────────────────────────────────────────────────

_bridge_proc: subprocess.Popen | None = None
_bridge_lock = threading.Lock()
_status = "disconnected"


def get_bridge_script() -> str:
    """Find bridge.py next to this executable."""
    if getattr(sys, "frozen", False):
        base = Path(sys.executable).parent
    else:
        base = Path(__file__).parent
    return str(base / "bridge.py")


def start_bridge(cfg: dict) -> bool:
    global _bridge_proc, _status
    with _bridge_lock:
        if _bridge_proc and _bridge_proc.poll() is None:
            return True  # already running
        if not cfg.get("session"):
            _status = "error: session_id missing — open Settings to enter it"
            return False
        script = get_bridge_script()
        python = sys.executable
        cmd = [
            python, script,
            "--url", cfg["url"],
            "--token", cfg["token"],
            "--session", cfg["session"],
        ]
        try:
            _bridge_proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
            )
            _status = "connecting"
            # Watch stdout in background thread
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
            _status = f"error"


# ── Setup dialog (Tkinter — built-in) ─────────────────────────────────────────

def show_setup_dialog(cfg: dict) -> dict | None:
    """URL + token + session_id input dialog. Returns updated config or None if cancelled."""
    try:
        import tkinter as tk
        from tkinter import ttk
    except ImportError:
        print("tkinter not available — edit ~/.ai_employee_bridge.json manually")
        return None

    result = {}

    root = tk.Tk()
    root.title("AI-Employee Bridge Setup")
    root.resizable(False, False)
    root.geometry("480x320")

    frame = ttk.Frame(root, padding=20)
    frame.pack(fill="both", expand=True)

    ttk.Label(frame, text="AI-Employee Bridge Setup", font=("", 14, "bold")).grid(
        row=0, column=0, columnspan=2, pady=(0, 16), sticky="w"
    )

    ttk.Label(frame, text="Server URL:").grid(row=1, column=0, sticky="w", pady=4)
    url_var = tk.StringVar(value=cfg.get("url", ""))
    url_entry = ttk.Entry(frame, textvariable=url_var, width=40)
    url_entry.grid(row=1, column=1, padx=(8, 0), pady=4)

    ttk.Label(frame, text="Auth Token:").grid(row=2, column=0, sticky="w", pady=4)
    token_var = tk.StringVar(value=cfg.get("token", ""))
    token_entry = ttk.Entry(frame, textvariable=token_var, width=40, show="*")
    token_entry.grid(row=2, column=1, padx=(8, 0), pady=4)

    ttk.Label(frame, text="Session ID:").grid(row=3, column=0, sticky="w", pady=4)
    session_var = tk.StringVar(value=cfg.get("session", ""))
    session_entry = ttk.Entry(frame, textvariable=session_var, width=40)
    session_entry.grid(row=3, column=1, padx=(8, 0), pady=4)

    ttk.Label(
        frame,
        text="Session ID: web UI → Agent → Computer Use tab → New Session",
        foreground="gray",
    ).grid(row=4, column=0, columnspan=2, sticky="w", pady=(0, 4))

    auto_var = tk.BooleanVar(value=cfg.get("auto_connect", True))
    ttk.Checkbutton(frame, text="Connect automatically on startup", variable=auto_var).grid(
        row=5, column=0, columnspan=2, sticky="w", pady=(4, 0)
    )

    ttk.Label(frame, text="Get your token: AI-Employee → Profile → API Token",
              foreground="gray").grid(row=6, column=0, columnspan=2, sticky="w", pady=(4, 16))

    def on_save():
        result["url"] = url_var.get().strip()
        result["token"] = token_var.get().strip()
        result["session"] = session_var.get().strip()
        result["auto_connect"] = auto_var.get()
        root.destroy()

    def on_cancel():
        root.destroy()

    btn_frame = ttk.Frame(frame)
    btn_frame.grid(row=7, column=0, columnspan=2, sticky="e")
    ttk.Button(btn_frame, text="Cancel", command=on_cancel).pack(side="right", padx=(4, 0))
    ttk.Button(btn_frame, text="Save & Connect", command=on_save).pack(side="right")

    url_entry.focus()
    root.mainloop()
    return result if result else None


# ── macOS menu bar app (rumps) ────────────────────────────────────────────────

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
            if self.cfg.get("auto_connect") and self.cfg.get("url") and self.cfg.get("token") and self.cfg.get("session"):
                threading.Thread(target=self._connect, daemon=True).start()

        def _update_icon(self):
            self.title = "🟢" if is_running() else "⬡"

        def _connect(self):
            start_bridge(self.cfg)
            self._update_icon()

        @rumps.clicked("Connect")
        def on_connect(self, _):
            if not self.cfg.get("url") or not self.cfg.get("token"):
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
            url = self.cfg.get("url", "").replace("ws://", "http://").replace("wss://", "https://")
            if url:
                webbrowser.open(url)

        @rumps.clicked("Status")
        def on_status(self, _):
            rumps.alert(f"Bridge status: {_status}\nServer: {self.cfg.get('url', '—')}")

        @rumps.clicked("Quit")
        def on_quit(self, _):
            stop_bridge()
            rumps.quit_application()

        @rumps.timer(3)
        def refresh(self, _):
            self._update_icon()

    BridgeApp().run()


# ── Windows / Linux system tray (pystray) ────────────────────────────────────

def run_tray(cfg: dict) -> None:
    try:
        import pystray
        from PIL import Image, ImageDraw
    except ImportError:
        print("Install pystray + Pillow: pip install pystray Pillow")
        sys.exit(1)

    def make_icon(connected: bool) -> "Image.Image":
        size = 64
        img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)
        color = (34, 197, 94) if connected else (156, 163, 175)
        draw.ellipse([8, 8, size - 8, size - 8], fill=color)
        return img

    def on_connect(icon, item):
        if not cfg.get("url") or not cfg.get("token"):
            on_settings(icon, item)
            return
        threading.Thread(target=lambda: start_bridge(cfg), daemon=True).start()

    def on_disconnect(icon, item):
        stop_bridge()
        icon.icon = make_icon(False)

    def on_settings(icon, item):
        updated = show_setup_dialog(cfg)
        if updated:
            cfg.update(updated)
            save_config(cfg)

    def on_open(icon, item):
        url = cfg.get("url", "").replace("ws://", "http://").replace("wss://", "https://")
        if url:
            webbrowser.open(url)

    def on_quit(icon, item):
        stop_bridge()
        icon.stop()

    def refresh(icon):
        while True:
            icon.icon = make_icon(is_running())
            import time; time.sleep(3)

    icon = pystray.Icon(
        "AI-Employee Bridge",
        make_icon(False),
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

    if cfg.get("auto_connect") and cfg.get("url") and cfg.get("token") and cfg.get("session"):
        threading.Thread(target=lambda: start_bridge(cfg), daemon=True).start()

    icon.run()


# ── Entry point ───────────────────────────────────────────────────────────────

def main():
    cfg = load_config()

    # First launch or missing session: show setup
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
