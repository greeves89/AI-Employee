"""Security + behaviour tests for Second Brain vault writes.

Writing into a shared knowledge vault is a real side effect, so the jailing
(no path escape, no .git, markdown-only, size cap) must never regress.
"""

import os
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


if __name__ == "__main__":
    unittest.main()
