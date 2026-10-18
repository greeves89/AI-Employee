"""A NUL hole in the platform error log makes every agent's grep blind.

A log file being appended to can come back after a hard kill with a run of NUL
bytes where the last writes were never flushed. A text log with NUL bytes in it
counts as binary for grep: ``grep PATTERN file`` then prints nothing and exits
1, which an agent cannot tell apart from an honest "no match".

How readily that happens depends on the grep (measured 2026-10-18): GNU grep
goes binary on ANY NUL byte, ugrep 7.8.4 — the grep in the agent image — only
when the FIRST NUL byte lies below offset 262144. On a live installation three
of the four generations carried holes (174 / 393 / 0 / 3219 NUL bytes) and the
active file was over that line (first NUL at 211665), so its triage really was
silent and a recurring error class read as "gone".

These tests pin the repair, the "don't touch a healthy file" half of the rule,
and the instruction text that sends agents to this file in the first place.
"""

from __future__ import annotations

import logging
import os
import time
from logging.handlers import RotatingFileHandler
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
    # And it must point at ALL generations: the log rotates at 2 MB, so a
    # search against the active file alone silently measures only today —
    # the second way this same file turns a real error into a null finding.
    assert "platform-errors.log*" in bullet, bullet


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


def test_a_clean_generation_in_the_middle_does_not_end_the_repair(tmp_path):
    """The live shape: broken `.1`, CLEAN `.2`, broken `.3`.

    `strip_nul_bytes` returns 0 for a healthy generation — the normal case. If
    the startup loop read that 0 as "nothing more to do here", the OLDEST
    generation would stay binary, and the repair would look like it worked.
    Drives the real `setup_platform_error_log`, not a loop rebuilt in the test:
    the resilience being asserted lives in the production code.
    """
    target = _log_with_hole(tmp_path)
    broken_1 = tmp_path / "platform-errors.log.1"
    clean_2 = tmp_path / "platform-errors.log.2"
    broken_3 = tmp_path / "platform-errors.log.3"
    _generation_with_hole(broken_1, 393, _OLD_CLASS)
    clean_2.write_bytes(b"2026-09-06 21:18:56,001 WARNING app.core.task_router: clean\n")
    _generation_with_hole(broken_3, 3_219, _OLD_CLASS)

    root = logging.getLogger()
    before = list(root.handlers)
    try:
        assert pel.setup_platform_error_log(str(target)) is True
    finally:
        for h in list(root.handlers):
            if h not in before:
                root.removeHandler(h)
                h.close()

    assert b"\x00" not in broken_3.read_bytes(), "oldest generation left behind"
    assert b"\x00" not in broken_1.read_bytes()
    assert b"\x00" not in target.read_bytes()


def test_report_counts_bytes_and_only_the_files_it_repaired(caplog):
    """The message must not inflate itself with the healthy generations.

    It is the only trace that a forensic log was rewritten, so "spread over N
    files" has to mean N files that actually had a hole.
    """
    import tempfile

    with tempfile.TemporaryDirectory() as raw:
        tmp_path = Path(raw)
        target = _log_with_hole(tmp_path)
        _generation_with_hole(tmp_path / "platform-errors.log.1", 393, _OLD_CLASS)
        (tmp_path / "platform-errors.log.2").write_bytes(b"clean generation\n")

        root = logging.getLogger()
        before = list(root.handlers)
        try:
            with caplog.at_level(logging.WARNING, logger=pel.__name__):
                assert pel.setup_platform_error_log(str(target)) is True
        finally:
            for h in list(root.handlers):
                if h not in before:
                    root.removeHandler(h)
                    h.close()

    reports = [r for r in caplog.records if "NUL byte(s)" in r.getMessage()]
    assert len(reports) == 1, [r.getMessage() for r in reports]
    assert reports[0].args == (174 + 393, 2)


def test_repair_is_reported_even_when_the_handler_already_exists(caplog):
    """The idempotent early return repaired too — it must say so.

    A second startup in the same process (or a re-entry after a reload) takes
    the "handler already attached" path. The repair has already happened by
    then; staying silent there means a rotated generation gets rewritten with
    no trace at all.
    """
    import tempfile

    with tempfile.TemporaryDirectory() as raw:
        tmp_path = Path(raw)
        target = tmp_path / "platform-errors.log"
        target.write_bytes(b"2026-10-18 11:00:00,000 WARNING app.test: healthy start\n")
        root = logging.getLogger()
        before = list(root.handlers)
        try:
            assert pel.setup_platform_error_log(str(target)) is True
            rotated = tmp_path / "platform-errors.log.1"
            _generation_with_hole(rotated, 42, _OLD_CLASS)
            with caplog.at_level(logging.WARNING, logger=pel.__name__):
                assert pel.setup_platform_error_log(str(target)) is True
            assert b"\x00" not in rotated.read_bytes()
        finally:
            for h in list(root.handlers):
                if h not in before:
                    root.removeHandler(h)
                    h.close()

    reports = [r for r in caplog.records if "NUL byte(s)" in r.getMessage()]
    assert len(reports) == 1, [r.getMessage() for r in reports]
    assert reports[0].args == (42, 1)


def test_oversized_generation_is_skipped_without_stopping_the_loop(tmp_path):
    """Unit-level counter-half to the clean-middle case.

    `_MAX_REPAIR_BYTES` (8 MB) is four times the rotation size, so in production
    this cap cannot trigger — it is a guard against a path that is NOT this
    handler's log. Pinned here on the helper rather than through startup, so the
    test does not depend on a byte budget that a longer log line would break.
    """
    first = tmp_path / "platform-errors.log.1"
    second = tmp_path / "platform-errors.log.2"
    _generation_with_hole(first, 393, _OLD_CLASS)
    _generation_with_hole(second, 7, _OLD_CLASS)
    assert first.stat().st_size > second.stat().st_size

    cap = second.stat().st_size + 1
    repaired = [pel.strip_nul_bytes(str(f), max_bytes=cap) for f in (first, second)]

    assert repaired == [0, 7]
    assert b"\x00" in first.read_bytes()        # skipped, as designed
    assert b"\x00" not in second.read_bytes()   # and the next one still ran


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
        installed = [
            h for h in root.handlers
            if h not in before and isinstance(h, RotatingFileHandler)
        ]
        assert installed, "rotating handler was not installed"
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


def test_a_symlink_under_a_generation_name_is_not_followed(tmp_path):
    """Everyone with write access to /shared can plant that name.

    Following it would let the repair rewrite a file OUTSIDE the log directory.
    """
    target = _log_with_hole(tmp_path)
    outside = tmp_path / "elsewhere" / "secrets.bin"
    outside.parent.mkdir()
    payload = b"not a log\x00at all\n"
    outside.write_bytes(payload)
    (tmp_path / "platform-errors.log.2").symlink_to(outside)

    for p in pel.log_generations(str(target)):
        pel.strip_nul_bytes(p)

    assert str(tmp_path / "platform-errors.log.2") not in pel.log_generations(str(target))
    assert outside.read_bytes() == payload


def test_a_hard_link_under_a_generation_name_is_not_rewritten(tmp_path):
    """`islink` cannot see a hard link — `st_nlink` can.

    Without that check a `platform-errors.log.2` hard-linked to another
    service's log would have THAT file stripped of its NUL bytes.
    """
    target = _log_with_hole(tmp_path)
    other = tmp_path / "other-service.log"
    payload = b"another service\x00important\n"
    other.write_bytes(payload)
    os.link(other, tmp_path / "platform-errors.log.2")

    for p in pel.log_generations(str(target)):
        pel.strip_nul_bytes(p)

    assert other.read_bytes() == payload


def test_non_ascii_digit_suffixes_are_not_generations(tmp_path):
    r"""`\d` is Unicode in Python, and `int()` accepts those digits too.

    A file named `platform-errors.log.٣` (U+0663) or `.１２` (fullwidth) would
    otherwise be picked up and rewritten as if it were a rotated generation.
    """
    target = _log_with_hole(tmp_path)
    impostors = {
        "platform-errors.log.٣": b"arabic-indic\x00three\n",
        "platform-errors.log.１２": b"fullwidth\x00twelve\n",
    }
    for name, payload in impostors.items():
        (tmp_path / name).write_bytes(payload)

    for p in pel.log_generations(str(target)):
        pel.strip_nul_bytes(p)

    assert pel.log_generations(str(target)) == [str(target)]
    for name, payload in impostors.items():
        assert (tmp_path / name).read_bytes() == payload, name


def test_the_file_name_is_matched_literally_not_as_a_pattern(tmp_path):
    """The basename goes into a regex, so its dots must be escaped.

    Unescaped, `platform-errors.log` matches `platform-errorsXlogY` as well —
    any similarly named file in the directory would be rewritten.
    """
    target = _log_with_hole(tmp_path)
    look_alike = tmp_path / "platform-errorsXlog.1"
    payload = b"different file\x00entirely\n"
    look_alike.write_bytes(payload)

    for p in pel.log_generations(str(target)):
        pel.strip_nul_bytes(p)

    assert look_alike.read_bytes() == payload


def test_generations_come_back_in_order_active_file_first(tmp_path):
    """Order is part of what the function promises.

    The active file is the one a repair must reach before the handler appends
    to it; the rotated ones follow youngest to oldest, so a truncated run still
    fixes the files most likely to be read.
    """
    target = tmp_path / "platform-errors.log"
    target.write_bytes(b"active\x00\n")
    for generation in (3, 1, 2, 11):
        (tmp_path / f"platform-errors.log.{generation}").write_bytes(b"x\x00\n")

    assert pel.log_generations(str(target)) == [
        str(target),
        str(tmp_path / "platform-errors.log.1"),
        str(tmp_path / "platform-errors.log.2"),
        str(tmp_path / "platform-errors.log.3"),
        str(tmp_path / "platform-errors.log.11"),
    ]
