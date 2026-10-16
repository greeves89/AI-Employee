"""Security + behaviour tests for Second Brain vault writes.

Writing into a shared knowledge vault is a real side effect, so the jailing
(no path escape, no .git, markdown-only, size cap) must never regress.
"""

import os
import stat
import tempfile
import unittest

from app.core import vault


class VaultWriteTests(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()

    def test_write_creates_then_updates(self):
        r1 = vault.write_file(self.root, "Wiki/note.md", "# hi")
        self.assertTrue(r1["created"])
        self.assertTrue(os.path.isfile(os.path.join(self.root, "Wiki", "note.md")))
        self.assertEqual(vault.read_file(self.root, "Wiki/note.md"), "# hi")
        r2 = vault.write_file(self.root, "Wiki/note.md", "# updated")
        self.assertFalse(r2["created"])
        self.assertEqual(vault.read_file(self.root, "Wiki/note.md"), "# updated")

    def test_overwrite_false_raises(self):
        vault.write_file(self.root, "a.md", "x")
        with self.assertRaises(FileExistsError):
            vault.write_file(self.root, "a.md", "y", overwrite=False)

    def test_only_text_suffixes_allowed(self):
        for bad in ("evil.sh", "run.py", "data.bin", "noext"):
            with self.assertRaises(ValueError):
                vault.write_file(self.root, bad, "x")
        for good in ("a.md", "b.markdown", "c.txt"):
            vault.write_file(self.root, good, "x")  # must not raise

    def test_path_escape_refused(self):
        for bad in ("../escape.md", "../../etc/passwd.md", "sub/../../out.md"):
            with self.assertRaises(ValueError):
                vault.write_file(self.root, bad, "x")

    def test_git_dir_refused(self):
        with self.assertRaises(ValueError):
            vault.write_file(self.root, ".git/hooks/pre-commit.md", "x")

    def test_size_cap(self):
        too_big = "a" * (vault.MAX_FILE_BYTES + 1)
        with self.assertRaises(ValueError):
            vault.write_file(self.root, "big.md", too_big)

    def test_delete(self):
        vault.write_file(self.root, "gone.md", "x")
        vault.delete_file(self.root, "gone.md")
        self.assertFalse(os.path.isfile(os.path.join(self.root, "gone.md")))
        with self.assertRaises(FileNotFoundError):
            vault.delete_file(self.root, "gone.md")

    def test_delete_path_escape_refused(self):
        with self.assertRaises(ValueError):
            vault.delete_file(self.root, "../../secret.md")

    def test_tree_text(self):
        vault.write_file(self.root, "A/x.md", "1")
        vault.write_file(self.root, "B/y.md", "2")
        tree = vault.tree_text(self.root)
        self.assertIn("A/", tree)
        self.assertIn("x.md", tree)


class GitSicherTests(unittest.TestCase):
    """``vault.git_sicher`` läuft über Inhalte, die Agenten schreiben.

    Die Härtung hat zwei Hälften, die unabhängig voneinander verschwinden können:
    die ``-c``-Optionen (Hooks, fsmonitor) und die schlanke Umgebung (keine System-,
    keine Benutzer-Konfiguration, nichts aus ``os.environ``). Beide werden hier an
    ihrer Wirkung festgenagelt, nicht am Quelltext.
    """

    def setUp(self):
        self.root = tempfile.mkdtemp()
        vault.git_sicher(self.root, "init", "-q")
        with open(os.path.join(self.root, "a.md"), "w") as f:
            f.write("eins\n")

    def _commit(self):
        vault.git_sicher(self.root, "add", "--", "a.md")
        return vault.git_sicher(self.root, "-c", "user.name=Test", "-c", "user.email=t@example.invalid",
                                "commit", "-q", "-m", "x", "--", "a.md")

    def test_hook_aus_der_ablage_laeuft_nicht(self):
        spur = os.path.join(tempfile.mkdtemp(), "hook-lief")
        for hook in ("pre-commit", "post-commit"):
            pfad = os.path.join(self.root, ".git", "hooks", hook)
            with open(pfad, "w") as f:
                f.write("#!/bin/sh\ntouch %s\n" % spur)
            os.chmod(pfad, os.stat(pfad).st_mode | stat.S_IEXEC)
        self.assertEqual(self._commit().returncode, 0)
        self.assertFalse(os.path.exists(spur))

    def test_umgebung_des_orchestrators_faerbt_nicht_ab(self):
        """Ohne schlanke Umgebung würde ``GIT_AUTHOR_NAME`` den Autor bestimmen."""
        os.environ["GIT_AUTHOR_NAME"] = "Durchgereicht"
        self.addCleanup(os.environ.pop, "GIT_AUTHOR_NAME", None)
        self.assertEqual(self._commit().returncode, 0)
        autor = vault.git_sicher(self.root, "log", "-1", "--format=%an").stdout.strip()
        self.assertEqual(autor, "Test")

    def test_fehlschlag_wird_protokolliert_statt_verschluckt(self):
        """Ein kaputter Verlauf darf nicht lautlos als "keine Versionen" erscheinen."""
        with self.assertLogs("app.core.vault", level="WARNING") as protokoll:
            r = vault.git_sicher(self.root, "log", "-1", "--", "gibtsnicht-als-commit.md")
            self.assertNotEqual(r.returncode, 0)
        self.assertTrue(any("rc=" in z for z in protokoll.output), protokoll.output)


if __name__ == "__main__":
    unittest.main()
