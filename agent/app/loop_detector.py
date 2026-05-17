"""Repetitive tool-call detection — shared by the task runner and chat handler.

An agent stuck in a loop calls the exact same tool with the exact same input
over and over. Both the interactive chat handler and the autonomous task
runner need to detect that and stop, instead of burning turns until the cap.
"""

import json

LOOP_DETECTION_WINDOW = 6  # consecutive identical tool calls → treated as a loop


class LoopDetector:
    """Tracks recent tool-call signatures and flags a runaway loop."""

    def __init__(self, window: int = LOOP_DETECTION_WINDOW):
        self.window = window
        self._sigs: list[str] = []

    def record(self, tool_name: str, tool_input: dict) -> None:
        """Record one tool call by its (name + sorted-args) signature."""
        try:
            sig = f"{tool_name}:{json.dumps(tool_input, sort_keys=True)}"
        except (TypeError, ValueError):
            sig = f"{tool_name}:{tool_input!r}"
        self._sigs.append(sig)

    def is_looping(self) -> bool:
        """True when the last `window` tool calls are all identical."""
        if len(self._sigs) < self.window:
            return False
        return len(set(self._sigs[-self.window:])) == 1

    def reset(self) -> None:
        self._sigs.clear()
