"""Regression tests for the disk-quota monitor (KeyError: 'disk_available_mb').

`DiskMonitorService._write_warning` reads ``stats["disk_available_mb"]`` while
``DockerService.get_workspace_disk_usage`` used to return only usage/limit/percent.
The missing key raised a KeyError that (a) suppressed the 80% warning file and
(b) crashed ``_stop_agent`` *before* the container was stopped, so over-quota
agents were never actually stopped. These tests pin the producer/consumer
contract so the four keys stay in sync.
"""

import unittest
from unittest.mock import MagicMock

from app.services.docker_service import DockerService
from app.services.disk_monitor import DiskMonitorService


def _docker_with_du(du_stdout: str) -> DockerService:
    svc = DockerService.__new__(DockerService)  # bypass __init__ (no docker daemon)
    container = MagicMock()
    container.exec_run.return_value = (0, (du_stdout.encode("utf-8"), b""))
    client = MagicMock()
    client.containers.get.return_value = container
    svc.client = client
    return svc


class TestWorkspaceDiskUsage(unittest.TestCase):
    def test_returns_disk_available_mb(self):
        # 2 GB used against a 10 GB quota -> 8 GB (8192 MB) available, 20%.
        stats = _docker_with_du("2048\t/workspace\n").get_workspace_disk_usage("c1", 10.0)
        self.assertIsNotNone(stats)
        self.assertEqual(
            set(stats),
            {"disk_usage_mb", "disk_limit_mb", "disk_percent", "disk_available_mb"},
        )
        self.assertEqual(stats["disk_available_mb"], 8192.0)
        self.assertEqual(stats["disk_percent"], 20.0)

    def test_over_quota_available_clamped_to_zero(self):
        # 11 GB used against a 10 GB quota -> available 0, percent capped at 100.
        stats = _docker_with_du("11264\t/workspace\n").get_workspace_disk_usage("c1", 10.0)
        self.assertEqual(stats["disk_available_mb"], 0.0)
        self.assertEqual(stats["disk_percent"], 100.0)


class TestWriteWarningContract(unittest.TestCase):
    def test_write_warning_consumes_real_stats_without_keyerror(self):
        docker = _docker_with_du("9728\t/workspace\n")  # 9.5 GB / 10 GB = 95%
        stats = docker.get_workspace_disk_usage("c1", 10.0)

        monitor = DiskMonitorService(session_factory=MagicMock(), docker_service=docker)
        # This is the call site that used to raise KeyError('disk_available_mb').
        monitor._write_warning("c1", stats)

        # The .disk_warning file must actually be written into the container.
        put_archive = docker.client.containers.get.return_value.put_archive
        put_archive.assert_called_once()
        self.assertEqual(put_archive.call_args.args[0], "/workspace")


if __name__ == "__main__":
    unittest.main()
