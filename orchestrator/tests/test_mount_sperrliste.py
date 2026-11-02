"""Der Mount-Katalog haengt keine Host-Systemordner ein und ueberdeckt im Agenten
weder System noch Pruef-Hook (P1-13).

Bis 1.424 nahm ``parse_mount_catalog`` jeden absoluten Pfad an: ein Eintrag
``/``, ``/etc`` oder der Docker-Socket haette einem Agenten den Host gegeben, ein
Ziel ``/opt/mcp`` den Pruef-Hook ersetzt.
"""

import unittest

from app.core.mounts import MountEntry, mount_gesperrt, mounts_to_docker_volumes, parse_mount_catalog


class MountSperrliste(unittest.TestCase):
    def test_erlaubte_eintraege_bleiben(self):
        katalog = parse_mount_catalog(
            "nas:/mnt/nas/documents:/mnt/documents:ro\n"
            "repo:/srv/repos/myproject:/workspace/repo:rw\n"
            "brain:/srv/secondbrain/projekt-1:/mnt/brains/projekt-1:rw\n"
        )
        self.assertEqual(sorted(katalog), ["brain", "nas", "repo"])

    def test_gesperrte_host_pfade(self):
        for host in ("/", "/etc", "/etc/ssh", "/proc", "/sys/fs", "/dev", "/root", "/run/docker.sock",
                     "/var/run/docker.sock", "/var/lib/docker/volumes", "/usr/bin", "/boot",
                     "/srv/../etc", "/srv/x/docker.sock", "/home/pi/../../etc"):
            with self.subTest(host):
                self.assertIsNotNone(mount_gesperrt(host, "/mnt/x"))
                self.assertEqual(parse_mount_catalog(f"x:{host}:/mnt/x:ro"), {})

    def test_gesperrte_ziele_im_agenten(self):
        for ziel in ("/", "/etc", "/opt/mcp", "/opt/mcp/x", "/opt/agent", "/usr/local", "/home/agent",
                     "/home/agent/.claude", "/shared", "/opt/build-tools", "/workspace", "/workspace/../etc"):
            with self.subTest(ziel):
                self.assertIsNotNone(mount_gesperrt("/mnt/nas", ziel))
                self.assertEqual(parse_mount_catalog(f"x:/mnt/nas:{ziel}:ro"), {})

    def test_aehnliche_namen_sind_nicht_gesperrt(self):
        self.assertIsNone(mount_gesperrt("/etcetera", "/mnt/etc"))
        self.assertIsNone(mount_gesperrt("/srv/run", "/mnt/root-daten"))

    def test_letzte_stelle_vor_docker_filtert_auch_datenbank_eintraege(self):
        """Second Brains kommen aus der Datenbank, nicht aus dem Katalog-Text."""
        mounts = [MountEntry("gut", "/srv/secondbrain/a", "/mnt/brains/a", "rw"),
                  MountEntry("boese", "/var/run/docker.sock", "/mnt/brains/b", "rw"),
                  MountEntry("hook", "/srv/secondbrain/c", "/opt/mcp", "rw")]
        self.assertEqual(mounts_to_docker_volumes(mounts),
                         {"/srv/secondbrain/a": {"bind": "/mnt/brains/a", "mode": "rw"}})


if __name__ == "__main__":
    unittest.main()
