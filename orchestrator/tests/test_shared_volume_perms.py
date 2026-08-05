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

    def fake_chown(path, uid, gid):
        calls["chown"] = (path, uid, gid)

    def fake_chmod(path, mode):
        calls["chmod"] = (path, mode)

    monkeypatch.setattr(os, "chown", fake_chown)
    monkeypatch.setattr(os, "chmod", fake_chmod)

    assert ensure_shared_volume_perms(str(target)) is True
    # owner root (uid 0), group = agent gid
    assert calls["chown"] == (str(target), 0, AGENT_GID)
    assert calls["chmod"] == (str(target), 0o3770)


def test_creates_dir_when_missing(tmp_path, monkeypatch):
    target = tmp_path / "shared"  # does not exist yet
    monkeypatch.setattr(os, "chown", lambda *a: None)
    monkeypatch.setattr(os, "chmod", lambda *a: None)

    assert ensure_shared_volume_perms(str(target)) is True
    assert target.is_dir()


def test_permission_error_is_swallowed_returns_false(tmp_path, monkeypatch):
    target = tmp_path / "shared"
    target.mkdir()

    def deny(*_a, **_k):
        raise PermissionError("not root")

    monkeypatch.setattr(os, "chown", deny)
    # must not raise -> startup keeps going even without root
    assert ensure_shared_volume_perms(str(target)) is False


def test_generic_error_is_swallowed_returns_false(tmp_path, monkeypatch):
    target = tmp_path / "shared"
    target.mkdir()

    def boom(*_a, **_k):
        raise OSError("read-only filesystem")

    monkeypatch.setattr(os, "chown", boom)
    assert ensure_shared_volume_perms(str(target)) is False
