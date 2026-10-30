"""Tests for the /shared volume permission repair (issue #507).

Docker creates the shared volume root:root 0755, so agents (uid 1000) can read
but never write it. ``ensure_shared_volume_perms`` fixes ownership/mode at
orchestrator startup. These tests pin the exact owner/group/mode and prove the
function never raises (a failure must not block startup).
"""

import os
import stat

import pytest

from app.core.shared_volume import (
    AGENT_GID,
    SHARED_MODE,
    ensure_shared_volume_perms,
)


def test_mode_constant_is_setgid_sticky_group_writable():
    # 3770 = rwxrws--T
    assert SHARED_MODE == 0o3770
    assert SHARED_MODE & stat.S_ISGID  # new subdirs inherit the agent group
    assert SHARED_MODE & stat.S_ISVTX  # sticky: agent can only remove its own entries
    assert SHARED_MODE & stat.S_IWGRP  # group (agent) may write


def test_mode_grants_no_world_access():
    # Only root (owner) and the agent group need /shared; "others" get nothing.
    # World-read on a dir carrying credential sub-dirs is unnecessary exposure.
    assert not SHARED_MODE & stat.S_IROTH  # no world read
    assert not SHARED_MODE & stat.S_IWOTH  # no world write
    assert not SHARED_MODE & stat.S_IXOTH  # no world execute


def test_applies_owner_group_and_mode(tmp_path, monkeypatch):
    target = tmp_path / "shared"
    target.mkdir()
    calls = {}
    target_ino = os.stat(target).st_ino

    def fake_fchown(fd, uid, gid):
        if os.fstat(fd).st_ino == target_ino:
            calls["chown"] = (uid, gid)

    def fake_fchmod(fd, mode):
        if os.fstat(fd).st_ino == target_ino:
            calls["chmod"] = mode

    monkeypatch.setattr(os, "fchown", fake_fchown)
    monkeypatch.setattr(os, "fchmod", fake_fchmod)

    assert ensure_shared_volume_perms(str(target)) is True
    # owner root (uid 0), group = agent gid
    assert calls["chown"] == (0, AGENT_GID)
    assert calls["chmod"] == 0o3770


def test_creates_dir_when_missing(tmp_path, monkeypatch):
    target = tmp_path / "shared"  # does not exist yet
    monkeypatch.setattr(os, "fchown", lambda *a: None)
    monkeypatch.setattr(os, "fchmod", lambda *a: None)

    assert ensure_shared_volume_perms(str(target)) is True
    assert target.is_dir()


def test_permission_error_is_swallowed_returns_false(tmp_path, monkeypatch):
    target = tmp_path / "shared"
    target.mkdir()

    def deny(*_a, **_k):
        raise PermissionError("not root")

    monkeypatch.setattr(os, "fchown", deny)
    # must not raise -> startup keeps going even without root
    assert ensure_shared_volume_perms(str(target)) is False


def test_generic_error_is_swallowed_returns_false(tmp_path, monkeypatch):
    target = tmp_path / "shared"
    target.mkdir()

    def boom(*_a, **_k):
        raise OSError("read-only filesystem")

    monkeypatch.setattr(os, "fchown", boom)
    assert ensure_shared_volume_perms(str(target)) is False


def test_symlink_statt_unterordner_wird_nicht_verfolgt(tmp_path, monkeypatch):
    """Ein Agent legt .plattform als Symlink auf einen Orchestrator-Pfad an."""
    target = tmp_path / "shared"
    target.mkdir()
    opfer = tmp_path / "opfer"
    opfer.mkdir()
    opfer.chmod(0o755)
    (target / ".plattform").symlink_to(opfer)
    geaendert = []
    monkeypatch.setattr(os, "fchown", lambda fd, *a: geaendert.append(os.fstat(fd).st_ino))
    monkeypatch.setattr(os, "chown", lambda *a, **k: pytest.fail("chown folgt Symlinks"))

    assert ensure_shared_volume_perms(str(target)) is False
    assert os.stat(opfer).st_ino not in geaendert
    assert stat.S_IMODE(os.stat(opfer).st_mode) == 0o755


def test_symlink_als_teamtoken_wird_nicht_verfolgt(tmp_path, monkeypatch):
    target = tmp_path / "shared"
    (target / ".auth").mkdir(parents=True)
    opfer = tmp_path / "geheim.txt"
    opfer.write_text("x")
    opfer.chmod(0o600)
    (target / ".auth" / "token.json").symlink_to(opfer)
    monkeypatch.setattr(os, "fchown", lambda *a: None)

    assert ensure_shared_volume_perms(str(target)) is False
    assert stat.S_IMODE(os.stat(opfer).st_mode) == 0o600
