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
import argparse
import asyncio
import base64
import io
import json
import logging
import os
import platform
import sys
import threading
import time
from typing import Any

import websockets

logging.basicConfig(level=logging.INFO, format="[Bridge] %(message)s")
log = logging.getLogger(__name__)

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

def take_screenshot(scale: float = 1.0) -> str:
    """Capture screen, return as base64 PNG. Downscale for Retina displays."""
    import pyautogui
    from PIL import Image

    img: Image.Image = pyautogui.screenshot()

    # Scale down large Retina screenshots (Claude hallucinates coordinates >1280px)
    max_width = 1280
    if img.width > max_width or scale != 1.0:
        ratio = min(max_width / img.width, scale) if scale == 1.0 else scale
        new_size = (int(img.width * ratio), int(img.height * ratio))
        img = img.resize(new_size, Image.LANCZOS)

    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    return base64.b64encode(buf.getvalue()).decode()


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
                ["osascript", "-e", f'id of app "{app_name}"'],
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

    def dispatch(self, command: dict) -> dict:
        action = command.get("action", "")
        params = command.get("params", {})

        try:
            if action == "screenshot":
                scale = params.get("scale", 1.0)
                return {"screenshot_b64": take_screenshot(scale)}

            elif action == "ax_tree":
                app = params.get("app")
                depth = params.get("max_depth", 6)
                return {"ax_tree": get_ax_tree(app, depth)}

            elif action == "click":
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

            elif action == "scroll":
                self._ctrl.scroll(params["x"], params["y"], params.get("amount", 3))
                return {"ok": True}

            elif action == "move":
                self._ctrl.move(params["x"], params["y"])
                return {"ok": True}

            elif action == "drag":
                self._ctrl.drag(params["x1"], params["y1"], params["x2"], params["y2"],
                               params.get("duration", 0.3))
                return {"ok": True}

            elif action == "open_app":
                app = params["app"]
                import subprocess
                subprocess.Popen(["open", "-a", app])
                return {"ok": True, "app": app}

            elif action == "get_clipboard":
                if IS_MAC:
                    import subprocess
                    result = subprocess.run(["pbpaste"], capture_output=True, text=True)
                    return {"text": result.stdout}
                elif IS_WIN:
                    import subprocess
                    result = subprocess.run(["powershell", "-command", "Get-Clipboard"], capture_output=True, text=True)
                    return {"text": result.stdout.strip()}
                return {"error": "Clipboard read not supported on this platform"}

            elif action == "set_clipboard":
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
    def __init__(self, ws_url: str, token: str, session_id: str | None = None):
        self.ws_url = ws_url
        self.token = token
        self.session_id = session_id
        self.dispatcher = CommandDispatcher()
        self._running = False

    async def connect(self) -> None:
        if not self.session_id:
            raise ValueError(
                "session_id is required.\n"
                "Go to the web UI → agent → Computer Use tab → create a session first,\n"
                "then pass the session ID with --session <id>."
            )

        url = f"{self.ws_url}/ws/computer-use/bridge?session_id={self.session_id}"
        headers = {"Authorization": f"Bearer {self.token}"}
        log.info(f"Connecting to {url}")

        async for ws in websockets.connect(url, additional_headers=headers, ping_interval=30):
            self._running = True
            try:
                # Announce capabilities
                caps = {
                    "type": "hello",
                    "platform": platform.system(),
                    "capabilities": ["screenshot", "ax_tree", "click", "type", "key", "scroll", "move", "drag",
                                     "open_app", "get_clipboard", "set_clipboard", "find_element", "wait_for_element"],
                    "ax_tree_available": IS_MAC,
                }
                await ws.send(json.dumps(caps))
                log.info(f"Connected. Waiting for commands... (platform: {platform.system()})")

                async for raw in ws:
                    await self._handle_message(ws, raw)

            except websockets.ConnectionClosed as e:
                log.warning(f"Connection closed: {e}. Reconnecting in 5s...")
                await asyncio.sleep(5)
            except Exception as e:
                log.error(f"Error: {e}. Reconnecting in 5s...")
                await asyncio.sleep(5)
            finally:
                self._running = False

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
                None, self.dispatcher.dispatch, command
            )
            response = {"type": "result", "id": cmd_id, "result": result}
            await ws.send(json.dumps(response))

        elif msg_type == "ping":
            await ws.send(json.dumps({"type": "pong"}))

        elif msg_type == "session_info":
            self.session_id = msg.get("session_id")
            log.info(f"Session ID: {self.session_id}")


# ── Entry Point ───────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="AI-Employee Computer-Use Bridge")
    parser.add_argument("--url", default=os.environ.get("AI_EMPLOYEE_URL", ""),
                        help="AI-Employee WebSocket URL (e.g. wss://myserver.com)")
    parser.add_argument("--token", default=os.environ.get("AI_EMPLOYEE_TOKEN", ""),
                        help="JWT auth token from AI-Employee web UI")
    parser.add_argument("--session", default=os.environ.get("AI_EMPLOYEE_SESSION", ""),
                        help="Optional: specific session ID to connect to")
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
    bridge = Bridge(ws_url, args.token, args.session or None)

    print(f"AI-Employee Computer-Use Bridge")
    print(f"  Platform: {platform.system()}")
    print(f"  Server:   {ws_url}")
    print(f"  Press Ctrl+C to stop")
    print()

    try:
        asyncio.run(bridge.connect())
    except KeyboardInterrupt:
        print("\nBridge stopped.")


async def run(url: str, token: str, session_id: str, stop_event: threading.Event | None = None) -> None:
    """Async entry point for use as a library (e.g. from tray_app)."""
    ws_url = url.rstrip("/").replace("http://", "ws://").replace("https://", "wss://")
    bridge = Bridge(ws_url, token, session_id)
    if stop_event:
        async def _watch_stop():
            while not stop_event.is_set():
                await asyncio.sleep(0.5)
            bridge.running = False
        asyncio.create_task(_watch_stop())
    await bridge.connect()


if __name__ == "__main__":
    main()
