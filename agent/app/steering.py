"""Live steering for the CLI chat handlers (claude_code + codex_cli).

Both CLI runtimes execute one agentic turn as a monolithic subprocess (a "turn" is
often the whole task). Without steering a message that arrives WHILE the turn runs
would only be handled after the whole turn finishes ("nach dem Task-Bulk"). This
helper folds newly-arrived same-channel messages into the running work using the
platform-standard pattern for all three runtimes:

    Queue  →  Interrupt (SIGINT, handled as returncode -2)  →  Resume

- A background watcher polls ``pending_drain`` (destructive pop of same-channel
  messages, wired by the ChatConsumer). When messages arrive it STASHES them and
  SIGINTs the running subprocess.
- After the turn ends (interrupted or naturally), the stashed + boundary messages
  are folded and the next turn is run via ``run_turn`` — which resumes the session
  (Claude ``--resume <id>``, Codex ``exec resume --last``), so the new message
  continues the SAME conversation instead of starting over.

The in-process ``custom_llm`` handler already folds at turn boundaries in its own
loop; it does not use this helper.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Awaitable, Callable

logger = logging.getLogger(__name__)


async def run_turns_with_steering(
    *,
    initial_text: str,
    run_turn: Callable[[str, bool], Awaitable[dict]],
    stop_current: Callable[[], Awaitable[None]],
    pending_drain: Callable[[], Awaitable[list[str]]] | None,
    publish_system: Callable[[str], Awaitable[None]] | None = None,
    max_folds: int = 6,
    poll_interval: float = 1.5,
) -> dict:
    """Run CLI turns, folding in newly-arrived same-channel messages.

    ``run_turn(text, is_resume)`` runs ONE CLI turn and returns its result dict.
    ``is_resume`` is False for the very first turn and True for every fold turn, so
    the handler can resume the session on continuations.
    ``stop_current()`` SIGINTs the running subprocess (graceful; -2 is not an error).
    ``pending_drain()`` pops queued same-channel messages (already prepared text).
    Returns the LAST turn's result dict.
    """
    text = initial_text
    result: dict = {"status": "completed", "text": ""}
    folds = 0
    is_resume = False

    while True:
        stash: list[str] = []
        turn_done = asyncio.Event()

        async def _watch() -> None:
            # Poll for new same-channel messages while the turn runs; on arrival,
            # stash them and interrupt the subprocess so we can fold immediately.
            while not turn_done.is_set():
                try:
                    await asyncio.wait_for(turn_done.wait(), timeout=poll_interval)
                    return  # turn finished on its own
                except asyncio.TimeoutError:
                    pass
                if pending_drain is None:
                    continue
                try:
                    extra = await pending_drain()
                except Exception:  # noqa: BLE001 — never let the watcher crash the turn
                    extra = None
                if extra:
                    stash.extend(extra)
                    try:
                        await stop_current()
                    except Exception:  # noqa: BLE001
                        pass
                    return

        watcher = asyncio.create_task(_watch()) if pending_drain is not None else None
        try:
            result = await run_turn(text, is_resume)
        finally:
            turn_done.set()
            if watcher is not None:
                try:
                    await watcher
                except Exception:  # noqa: BLE001
                    pass

        # Collect messages that arrived: stashed by the watcher mid-turn PLUS any that
        # landed right at the boundary (after the watcher's last drain).
        extra = list(stash)
        if pending_drain is not None and folds < max_folds:
            try:
                boundary = await pending_drain()
                if boundary:
                    extra.extend(boundary)
            except Exception:  # noqa: BLE001
                pass

        if extra and folds < max_folds:
            folds += 1
            if publish_system is not None:
                try:
                    await publish_system(
                        f"{len(extra)} neue Nachricht(en) aufgenommen — wird mitverarbeitet."
                    )
                except Exception:  # noqa: BLE001
                    pass
            text = "\n\n".join(t for t in extra if t and t.strip())
            is_resume = True
            continue
        break

    return result
