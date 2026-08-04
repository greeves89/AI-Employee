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

_ssl_ctx = ssl.create_default_context()
_ssl_ctx.check_hostname = False
_ssl_ctx.verify_mode = ssl.CERT_NONE

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

def _capture_macos_inprocess():
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
    try:
        cg = CGDisplayCreateImage(CGMainDisplayID())
        if cg is None:
            return None
        w, h = CGImageGetWidth(cg), CGImageGetHeight(cg)
        raw = bytes(CGDataProviderCopyData(CGImageGetDataProvider(cg)))
        # macOS liefert BGRA; die Zeilenlaenge ist gepolstert und MUSS mitgegeben
        # werden, sonst verscheert das Bild.
        return Image.frombuffer(
            "RGBA", (w, h), raw, "raw", "BGRA", CGImageGetBytesPerRow(cg), 1
        ).convert("RGB")
    except Exception as e:  # noqa: BLE001
        log.warning("Quartz screenshot failed, falling back to pyautogui: %s", e)
        return None


def take_screenshot(scale: float = 1.0) -> str:
    """Capture screen, return as base64 PNG. Downscale for Retina displays."""
    from PIL import Image

    img = _capture_macos_inprocess() if sys.platform == "darwin" else None
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
    return base64.b64encode(buf.getvalue()).decode()


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
        self.active = False

    def _flush_text(self) -> None:
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
            if self._text_buffer:
                self._text_buffer.pop()
            return
        char = getattr(key, "char", None)
        if char is not None:
            self._text_buffer.append(char)
        elif key == keyboard.Key.space:
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


def _applescript_string_literal(value: str) -> str:
    """Escape a value for safe interpolation into a double-quoted AppleScript
    string literal. Without this, an app name containing '"' can break out of
    the literal and inject arbitrary AppleScript (e.g. `do shell script ...`)."""
    return value.replace("\\", "\\\\").replace('"', '\\"')


# ── AXUIElement (macOS Accessibility Tree) ────────────────────────────────────

def get_ax_tree(app_name: str | None = None, max_depth: int = 6) -> dict:
    """Read AXUIElement tree. Returns structured dict with roles, names, bboxes."""
    if not IS_MAC:
        return {"error": "AXUIElement only available on macOS"}

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

    def click(self, x: int, y: int, button: str = "left", double: bool = False) -> None:
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
        if sys.platform == "darwin" and text:
            import subprocess
            r = subprocess.run(
                ["osascript", "-e",
                 f'tell application "System Events" to keystroke "{_applescript_string_literal(text)}"'],
                capture_output=True, text=True,
            )
            if r.returncode == 0:
                return
            log.warning("keystroke via System Events failed, falling back: %s", r.stderr.strip())
        self._pyautogui.typewrite(text, interval=interval)

    def key_press(self, keys: list[str]) -> None:
        if len(keys) == 1:
            self._pyautogui.press(keys[0])
        else:
            self._pyautogui.hotkey(*keys)

    def scroll(self, x: int, y: int, amount: int) -> None:
        self._pyautogui.scroll(amount, x=x, y=y)

    def move(self, x: int, y: int) -> None:
        self._pyautogui.moveTo(x, y)

    def drag(self, x1: int, y1: int, x2: int, y2: int, duration: float = 0.3) -> None:
        self._pyautogui.dragTo(x2, y2, duration=duration, startX=x1, startY=y1)


# ── Command Dispatcher ────────────────────────────────────────────────────────

class CommandDispatcher:
    def __init__(self):
        self._ctrl = InputController()
        # Set by the WS client so human-capture events can be pushed upstream.
        self.input_recorder: InputRecorder | None = None

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

            elif action == "screenshot":
                scale = params.get("scale", 1.0)
                return {"screenshot_b64": take_screenshot(scale)}

            elif action == "ax_tree":
                app = params.get("app")
                depth = params.get("max_depth", 6)
                return {"ax_tree": get_ax_tree(app, depth)}

            elif action in ("click", "mouse_click"):
                self._ctrl.click(
                    params["x"], params["y"],
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
                self._ctrl.scroll(params["x"], params["y"], params.get("amount", 3))
                return {"ok": True}

            elif action in ("move", "mouse_move"):
                self._ctrl.move(params["x"], params["y"])
                return {"ok": True}

            elif action == "drag":
                self._ctrl.drag(params["x1"], params["y1"], params["x2"], params["y2"],
                               params.get("duration", 0.3))
                return {"ok": True}

            elif action == "open_app":
                app = params.get("app") or params["name"]
                import subprocess
                result = subprocess.run(["open", "-a", app], capture_output=True, text=True)
                if result.returncode != 0:
                    return {"ok": False, "app": app, "error": result.stderr.strip() or f'"{app}" not found'}
                return {"ok": True, "app": app}

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
                if IS_MAC:
                    import subprocess
                    result = subprocess.run(["pbpaste"], capture_output=True, text=True)
                    return {"text": result.stdout}
                elif IS_WIN:
                    import subprocess
                    result = subprocess.run(["powershell", "-command", "Get-Clipboard"], capture_output=True, text=True)
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
                    subprocess.run(["powershell", "-command", f"Set-Clipboard '{text}'"], check=True)
                    return {"ok": True}
                return {"error": "Clipboard write not supported on this platform"}

            elif action == "find_element":
                # Search AX tree for element matching role/title/label, return center coords
                query = params.get("query", "")
                role = params.get("role", "")
                app = params.get("app")
                tree = get_ax_tree(app, max_depth=8)

                def _search(node: dict) -> dict | None:
                    if not node:
                        return None
                    node_title = node.get("title", "").lower()
                    node_label = node.get("label", "").lower()
                    node_value = node.get("value", "").lower()
                    node_role = node.get("role", "").lower()
                    q = query.lower()
                    role_match = (not role) or node_role == role.lower()
                    text_match = (not q) or q in node_title or q in node_label or q in node_value
                    if role_match and text_match and node.get("bbox"):
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
                        found = _search(child)
                        if found:
                            return found
                    return None

                result = _search(tree)
                return result or {"found": False, "query": query, "role": role}

            elif action == "wait_for_element":
                # Poll AX tree until element appears (or timeout)
                query = params.get("query", "")
                role = params.get("role", "")
                app = params.get("app")
                timeout = min(params.get("timeout", 10), 30)  # max 30s
                interval = params.get("interval", 0.5)

                deadline = time.time() + timeout
                while time.time() < deadline:
                    tree = get_ax_tree(app, max_depth=8)

                    def _find(node: dict) -> dict | None:
                        if not node:
                            return None
                        q = query.lower()
                        role_match = (not role) or node.get("role", "").lower() == role.lower()
                        text_match = (not q) or q in node.get("title", "").lower() or q in node.get("label", "").lower()
                        if role_match and text_match and node.get("bbox"):
                            bbox = node["bbox"]
                            return {
                                "found": True,
                                "role": node.get("role"),
                                "title": node.get("title", ""),
                                "bbox": bbox,
                                "center": {"x": int(bbox["x"] + bbox["w"] / 2), "y": int(bbox["y"] + bbox["h"] / 2)},
                            }
                        for child in node.get("children", []):
                            r = _find(child)
                            if r:
                                return r
                        return None

                    found = _find(tree)
                    if found:
                        return found
                    time.sleep(interval)

                return {"found": False, "timeout": True, "query": query}

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
        self.dispatcher: CommandDispatcher | None = None
        self._running = False
        # Human-capture events are produced on pynput's own threads, so they
        # land in a thread-safe queue and are drained by an asyncio task that
        # owns the WebSocket (never send from the listener thread directly).
        self._input_events: queue.Queue = queue.Queue(maxsize=1000)

    def _ensure_dispatcher(self) -> CommandDispatcher:
        if self.dispatcher is None:
            log.info("Initializing desktop control")
            self.dispatcher = CommandDispatcher()
            self.dispatcher.input_recorder = InputRecorder(self._queue_input_event)
            log.info("Desktop control ready")
        return self.dispatcher

    def _queue_input_event(self, event: dict) -> None:
        """Called from the pynput listener thread — must not block or send."""
        try:
            self._input_events.put_nowait(event)
        except queue.Full:
            log.warning("Input-capture queue full — dropping event")

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
        ssl_context = _ssl_ctx if url.startswith("wss://") else None
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
                    "capabilities": ["screenshot", "ax_tree", "click", "type", "key", "scroll", "move", "drag",
                                     "open_app", "open_url", "close_app", "get_clipboard", "set_clipboard", "find_element",
                                     "wait_for_element", "start_input_capture", "stop_input_capture"],
                    "ax_tree_available": IS_MAC,
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
                try:
                    async for raw in ws:
                        await self._handle_message(ws, raw)
                finally:
                    drain_task.cancel()
                    if self.dispatcher and self.dispatcher.input_recorder:
                        # Never leave a keylogger running past the connection.
                        self.dispatcher.input_recorder.stop()

            except websockets.ConnectionClosed as e:
                # 1008 = the server REJECTED us (session expired/unknown, wrong
                # user, another bridge already attached). Retrying that forever
                # can never succeed, and silently doing so is what made an
                # expired session look like a hanging connection / firewall
                # problem. Report it so the UI can tell the user what to do.
                reason = (getattr(e, "reason", "") or str(e)).strip()
                if getattr(getattr(e, "rcvd", None), "code", None) == 1008:
                    log.error(f"Rejected by server: {reason}")
                    self._emit_state("rejected", reason)
                else:
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
) -> None:
    """Async entry point for use as a library (e.g. from tray_app).

    ``on_state(state, detail)`` is called on connect/disconnect so a GUI can show
    the real state. Without it the tray had no way to learn that the handshake
    succeeded — connect() never returns while the bridge is up — and therefore
    displayed "Verbinde…" forever, even on a perfectly healthy connection.
    """
    ws_url = url.rstrip("/").replace("http://", "ws://").replace("https://", "wss://")
    if extra_headers is None:
        extra_headers = collect_extra_headers(None)
    bridge = Bridge(ws_url, token, session_id, extra_headers=extra_headers, on_state=on_state)
    if stop_event:
        async def _watch_stop():
            while not stop_event.is_set():
                await asyncio.sleep(0.5)
            bridge.running = False
        asyncio.create_task(_watch_stop())
    await bridge.connect()


if __name__ == "__main__":
    main()
