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


# --- The rest gap of the startup fix: rotated generations (issue #997 A) ------
#
# `strip_nul_bytes` used to be called with exactly ONE path, the active file.
# With backupCount=3 that leaves a hole unreachable forever as soon as the file
# rotates: it moves to `.1` and nothing ever looks at `.1` again. Live proof on
# 2026-10-17: `.1` carried 393 NUL bytes from 07.09. and `.3` carried 3.219 from
# 06.09. — months old, in the files an honest triage reads alongside the active
# one. These tests pin the loop over all generations and, just as importantly,
# that it does not reach beyond them.

_OLD_CLASS = "app.db.session [resilient_session] connect attempt 2/3 failed"


def _generation_with_hole(path: Path, nul_bytes: int, marker: str) -> None:
    path.write_bytes(
        b"2026-09-07 10:42:20,113 WARNING app.services.scheduler_service: before\n"
        + b"\x00" * nul_bytes
        + f"2026-09-07 10:43:52,004 WARNING {marker}\n".encode()
    )


def test_rotated_generations_are_repaired_too(tmp_path):
    """The live case: holes in `.1` and `.3`, both unreachable before this fix."""
    target = _log_with_hole(tmp_path)
    _generation_with_hole(tmp_path / "platform-errors.log.1", 393, _OLD_CLASS)
    _generation_with_hole(tmp_path / "platform-errors.log.3", 3_219, _OLD_CLASS)

    removed = sum(pel.strip_nul_bytes(p) for p in pel.log_generations(str(target)))

    assert removed == 174 + 393 + 3_219
    for name in ("platform-errors.log", "platform-errors.log.1", "platform-errors.log.3"):
        body = (tmp_path / name).read_bytes()
        assert b"\x00" not in body, name
    # And the line a triage would grep for in an OLD generation is readable.
    assert _OLD_CLASS in (tmp_path / "platform-errors.log.1").read_text()


def test_setup_repairs_the_rotated_generations_as_well(tmp_path):
    """End to end through the real startup path, not just the helper."""
    target = _log_with_hole(tmp_path)
    rotated = tmp_path / "platform-errors.log.2"
    _generation_with_hole(rotated, 393, _OLD_CLASS)
    root = logging.getLogger()
    before = list(root.handlers)
    try:
        assert pel.setup_platform_error_log(str(target)) is True
        assert b"\x00" not in rotated.read_bytes()
        assert _OLD_CLASS in rotated.read_text()
    finally:
        for h in list(root.handlers):
            if h not in before:
                root.removeHandler(h)
                h.close()


def test_generation_orphaned_by_a_lowered_backup_count_is_repaired(tmp_path):
    """A `.7` exists only because backupCount used to be larger.

    The handler will never touch it again, so a range loop over 1..backupCount
    would leave exactly this file binary forever — the same dead end the fix is
    supposed to close, one generation further out.
    """
    target = _log_with_hole(tmp_path)
    orphan = tmp_path / "platform-errors.log.7"
    _generation_with_hole(orphan, 12, _OLD_CLASS)

    assert str(orphan) in pel.log_generations(str(target))
    for p in pel.log_generations(str(target)):
        pel.strip_nul_bytes(p)
    assert b"\x00" not in orphan.read_bytes()


def test_only_numeric_generations_of_this_log_are_touched(tmp_path):
    """Counter-half: the selector must not widen into the rest of the directory.

    These files legitimately contain NUL bytes (a gzip member does) or belong to
    a different log. Rewriting them would corrupt data the repair has no business
    touching.
    """
    target = _log_with_hole(tmp_path)
    strangers = {
        "platform-errors.log.1.gz": b"\x1f\x8b\x08\x00payload\x00\x00",
        "platform-errors.log.bak": b"backup\x00copy\n",
        "platform-errors.log.old2": b"older\x00copy\n",
        "other-service.log.1": b"someone else's\x00log\n",
    }
    for name, payload in strangers.items():
        (tmp_path / name).write_bytes(payload)

    generations = pel.log_generations(str(target))
    for p in generations:
        pel.strip_nul_bytes(p)

    assert generations == [str(target)]
    for name, payload in strangers.items():
        assert (tmp_path / name).read_bytes() == payload, name


def test_a_directory_named_like_a_generation_is_not_opened(tmp_path):
    target = _log_with_hole(tmp_path)
    (tmp_path / "platform-errors.log.2").mkdir()

    assert pel.log_generations(str(target)) == [str(target)]


def test_healthy_generation_is_left_byte_identical(tmp_path):
    """Same "don't churn a clean file" rule as for the active log."""
    target = _log_with_hole(tmp_path)
    clean = tmp_path / "platform-errors.log.2"
    payload = b"2026-09-06 21:18:56,001 WARNING app.core.task_router: clean\n"
    clean.write_bytes(payload)
    os.utime(clean, (1_000_000, 1_000_000))

    for p in pel.log_generations(str(target)):
        pel.strip_nul_bytes(p)

    assert clean.read_bytes() == payload
    assert clean.stat().st_mtime == 1_000_000


def test_one_unrepairable_generation_does_not_stop_the_others(tmp_path):
    """An oversized `.1` must not swallow the repair of `.2`.

    `strip_nul_bytes` skips a file above `max_bytes`. If the loop treated that
    as "stop here", one big generation would re-open the blindness for every
    older one behind it.
    """
    target = _log_with_hole(tmp_path)
    big = tmp_path / "platform-errors.log.1"
    _generation_with_hole(big, 393, _OLD_CLASS)
    small = tmp_path / "platform-errors.log.2"
    _generation_with_hole(small, 7, _OLD_CLASS)
    root = logging.getLogger()
    before = list(root.handlers)
    try:
        # Only `.1` is above the cap; the active file and `.2` are below it.
        assert big.stat().st_size > 400
        assert small.stat().st_size < 400
        repaired = [
            pel.strip_nul_bytes(p, max_bytes=400) for p in pel.log_generations(str(target))
        ]
        assert repaired[0] == 174          # active file repaired
        assert sum(repaired) == 174 + 7    # `.2` repaired despite `.1` skipped
        assert b"\x00" in big.read_bytes()      # skipped, as designed
        assert b"\x00" not in small.read_bytes()
    finally:
        for h in list(root.handlers):
            if h not in before:
                root.removeHandler(h)
                h.close()


def test_handler_backup_count_and_repair_range_cannot_drift(tmp_path):
    """One value, two users.

    If the handler rotated more generations than the repair loop scans by range,
    the extra ones would only be caught by the directory scan — and a future
    change to either number must not be able to silently reopen the gap.
    """
    target = tmp_path / "platform-errors.log"
    root = logging.getLogger()
    before = list(root.handlers)
    try:
        assert pel.setup_platform_error_log(str(target)) is True
        installed = [h for h in root.handlers if h not in before]
        assert installed, "handler was not installed"
        assert installed[0].backupCount == pel._BACKUP_COUNT
        # The by-range part of the selector must cover exactly those files.
        for generation in range(1, pel._BACKUP_COUNT + 1):
            (tmp_path / f"platform-errors.log.{generation}").write_bytes(b"x\x00\n")
        found = pel.log_generations(str(target))
        assert len(found) == pel._BACKUP_COUNT + 1, found
    finally:
        for h in list(root.handlers):
            if h not in before:
                root.removeHandler(h)
                h.close()


def test_known_generations_are_found_even_if_the_directory_cannot_be_listed(tmp_path, monkeypatch):
    """Why the by-range part exists at all, next to the directory scan.

    The scan finds every generation that is on disk — so on a healthy host the
    range adds nothing, and a mutation emptying it stays green. It earns its
    place in exactly one case: if `os.listdir` fails (a directory whose entries
    we may not enumerate, while the files themselves are readable), a scan-only
    selector would silently fall back to repairing the active file alone and
    reopen the gap this fix closes. The generations the handler itself manages
    must not depend on being able to list the directory.
    """
    target = _log_with_hole(tmp_path)
    rotated = tmp_path / "platform-errors.log.1"
    _generation_with_hole(rotated, 393, _OLD_CLASS)

    def _refuse(_path):
        raise PermissionError("directory is not listable")

    monkeypatch.setattr(pel.os, "listdir", _refuse)

    found = pel.log_generations(str(target))

    assert str(rotated) in found
    for p in found:
        pel.strip_nul_bytes(p)
    assert b"\x00" not in rotated.read_bytes()
