"""A NUL hole in the platform error log makes every agent's grep blind.

A log file being appended to can come back after a hard kill with a run of NUL
bytes where the last writes were never flushed. ONE such byte anywhere makes
the whole file count as binary for grep: ``grep PATTERN file`` then prints
nothing and exits 1, which an agent cannot tell apart from an honest "no
match". Measured on a live installation: three of the four log generations
carried such holes (174 / 393 / 0 / 3219 NUL bytes), so the grep-based triage
agents are explicitly pointed at was silently blind most of the time — a
recurring error class then reads as "gone".

These tests pin the repair, the "don't touch a healthy file" half of the rule,
and the instruction text that sends agents to this file in the first place.
"""

from __future__ import annotations

import logging
import os
import time
from pathlib import Path

from app.core import platform_error_log as pel


_CLASS = "Gesetz crawler: 1 Normen fehlgeschlagen — content too large"


def _log_with_hole(tmp_path: Path) -> Path:
    """A log whose middle line was lost to an unclean stop, as seen live."""
    target = tmp_path / "platform-errors.log"
    target.write_bytes(
        b"2026-10-14 18:14:36,006 WARNING app.services.skill_crawler: Crawled skill\n"
        + b"\x00" * 174
        + f"2026-10-15 19:50:46,648 WARNING app.services.gesetz_crawler: {_CLASS}\n".encode()
    )
    return target


def test_nul_hole_removed_and_every_surviving_line_kept(tmp_path):
    target = _log_with_hole(tmp_path)
    before = target.read_bytes()

    removed = pel.strip_nul_bytes(str(target))

    assert removed == 174
    after = target.read_bytes()
    assert b"\x00" not in after
    # The repair may only delete NUL bytes — nothing else.
    assert after == before.replace(b"\x00", b"")
    # And the class an agent would grep for is readable again.
    assert _CLASS in after.decode()


def test_healthy_file_is_not_rewritten_at_all(tmp_path):
    """Counter-half of the rule: a clean log must not be touched.

    Rewriting every log on every startup would churn the file (and its mtime)
    for no reason, and would make "the file changed" useless as a signal.
    """
    target = tmp_path / "platform-errors.log"
    payload = b"2026-10-17 18:16:55,836 WARNING app.core.task_router: whatever\n"
    target.write_bytes(payload)
    os.utime(target, (1_000_000, 1_000_000))

    assert pel.strip_nul_bytes(str(target)) == 0
    assert target.read_bytes() == payload
    assert target.stat().st_mtime == 1_000_000


def test_repair_keeps_the_inode_so_an_open_handler_keeps_writing(tmp_path):
    """Must be an in-place rewrite, not os.replace.

    The orchestrator attaches a RotatingFileHandler to this path. If the repair
    swapped the inode, a descriptor opened before it would keep appending to an
    orphaned file and those lines would silently never appear.
    """
    target = _log_with_hole(tmp_path)
    inode_before = target.stat().st_ino

    with open(target, "ab") as already_open:
        pel.strip_nul_bytes(str(target))
        already_open.write(b"line written through the pre-existing descriptor\n")

    assert target.stat().st_ino == inode_before
    assert b"pre-existing descriptor" in target.read_bytes()


def test_oversized_file_is_left_alone(tmp_path):
    """Never read an unbounded file into memory to fix a log."""
    target = _log_with_hole(tmp_path)
    assert pel.strip_nul_bytes(str(target), max_bytes=10) == 0
    assert b"\x00" in target.read_bytes()


def test_missing_file_is_not_an_error(tmp_path):
    assert pel.strip_nul_bytes(str(tmp_path / "nope.log")) == 0


def test_setup_repairs_the_hole_before_appending_to_it(tmp_path):
    """End to end: startup follows the unclean stop that caused the hole."""
    target = _log_with_hole(tmp_path)
    root = logging.getLogger()
    before = list(root.handlers)
    try:
        assert pel.setup_platform_error_log(str(target)) is True
        logging.getLogger("app.test").warning("a fresh warning after the restart")
        for h in root.handlers:
            h.flush()
        body = target.read_bytes()
        assert b"\x00" not in body
        assert _CLASS in body.decode()                      # history survived
        assert b"a fresh warning after the restart" in body  # and we append on
    finally:
        for h in list(root.handlers):
            if h not in before:
                root.removeHandler(h)
                h.close()


def test_agent_guidance_warns_about_the_binary_grep_trap():
    """The instruction block that sends agents to this file must say `grep -a`.

    Without it the platform hands every agent a measurement that can fail
    silently. This is the cheapest place to stop that, so guard the text.
    """
    src = Path(__file__).resolve().parents[1] / "app" / "core" / "agent_manager.py"
    text = src.read_text(encoding="utf-8")

    # Cut at the NEXT top-level bullet, not after N characters: what is being
    # asserted is "this bullet carries the warning", and a character count
    # would quietly start measuring the wrong thing as the text around it
    # grows (see tests/test_character_windows_only_shrink.py).
    start = text.index("- **Platform errors: `/shared/platform-errors.log`**")
    rest = text[start + 1:]
    stop = rest.index("\n- ")
    bullet = rest[:stop]

    assert "grep -a" in bullet, bullet
    assert "binary" in bullet.lower(), bullet
