"""Der eingebaute Aufraeumlauf kannte nur Caches/Logs — der eigentliche
Vorfall (#830) war ein Agent, dessen Workspace zu 99,8% aus liegengebliebenen
Git-Worktrees bestand. Der Aufraeumlauf senkte die Belegung deshalb nur von
99,8% auf 96,2%: unter der 95%-Schwelle blieb der Agent trotzdem gestoppt,
und der Agent kann in diesem Zustand selbst gar nichts mehr tun (kein
laufender Container = keine Ausfuehrung).

Diese Tests fuehren ``WORKTREE_PRUNE_SCRIPT`` als ECHTES Python-Skript gegen
ECHTE lokale Git-Repos + Worktrees aus (kein Docker, kein Netzwerk noetig —
das "Fernrepo" ist ein lokales bare Repo). Ein Mock, der nur pruefte "wurde
``git worktree remove`` aufgerufen", haette die eigentliche Sicherheitslogik
(sauber? wirklich gepusht?) nicht wirklich verifiziert.
"""

import subprocess
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from app.services.docker_service import WORKTREE_PRUNE_SCRIPT


def run_git(args: list[str], cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=True)


def run_prune(root: Path) -> subprocess.CompletedProcess:
    import os
    env = dict(**os.environ, WORKTREE_PRUNE_ROOT=str(root))
    return subprocess.run(
        [sys.executable, "-c", WORKTREE_PRUNE_SCRIPT],
        capture_output=True, text=True, env=env,
    )


def make_bare_remote(base: Path) -> Path:
    remote = base / "remote.git"
    subprocess.run(["git", "init", "--bare", "-q", str(remote)], check=True)
    return remote


def make_clone(base: Path, remote: Path, name: str = "repo") -> Path:
    clone = base / name
    run_git(["clone", "-q", str(remote), str(clone)], base)
    run_git(["config", "user.email", "t@example.invalid"], clone)
    run_git(["config", "user.name", "Test"], clone)
    (clone / "README.md").write_text("erste Fassung\n")
    run_git(["add", "."], clone)
    run_git(["commit", "-q", "-m", "initial"], clone)
    run_git(["push", "-q", "origin", "HEAD"], clone)
    return clone


class WorktreePruneScriptTest(unittest.TestCase):
    def test_a_clean_fully_pushed_worktree_is_removed(self):
        with TemporaryDirectory() as d:
            base = Path(d)
            remote = make_bare_remote(base)
            repo = make_clone(base, remote)
            wt = base / "wt-done"
            run_git(["worktree", "add", "-q", "-b", "fix/done", str(wt)], repo)
            run_git(["push", "-q", "-u", "origin", "fix/done"], wt)

            result = run_prune(base)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertFalse(wt.exists(), "saubere, gepushte Worktree haette entfernt werden muessen")
            self.assertIn("PRUNED 1", result.stdout)

    def test_a_worktree_with_uncommitted_changes_is_kept(self):
        with TemporaryDirectory() as d:
            base = Path(d)
            remote = make_bare_remote(base)
            repo = make_clone(base, remote)
            wt = base / "wt-dirty"
            run_git(["worktree", "add", "-q", "-b", "fix/dirty", str(wt)], repo)
            run_git(["push", "-q", "-u", "origin", "fix/dirty"], wt)
            (wt / "scratch.txt").write_text("unfertig\n")  # nie committed

            result = run_prune(base)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue(wt.exists(), "unbestaetigte Aenderungen haetten sie schuetzen muessen")
            self.assertIn("PRUNED 0", result.stdout)

    def test_a_worktree_with_unpushed_commits_is_kept(self):
        with TemporaryDirectory() as d:
            base = Path(d)
            remote = make_bare_remote(base)
            repo = make_clone(base, remote)
            wt = base / "wt-unpushed"
            run_git(["worktree", "add", "-q", "-b", "fix/unpushed", str(wt)], repo)
            run_git(["push", "-q", "-u", "origin", "fix/unpushed"], wt)
            (wt / "more.txt").write_text("weiterer commit\n")
            run_git(["add", "."], wt)
            run_git(["commit", "-q", "-m", "lokal, nie gepusht"], wt)  # HEAD != Fernzweig

            result = run_prune(base)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue(wt.exists(), "lokal vorauseilende Commits haetten sie schuetzen muessen")
            self.assertIn("PRUNED 0", result.stdout)

    def test_a_worktree_without_a_matching_remote_branch_is_kept(self):
        """Entspricht Claude Codes eigenen .claude/worktrees/agent-*-Sitzungen:
        kein Fernabgleich moeglich -> bewusst nicht angefasst."""
        with TemporaryDirectory() as d:
            base = Path(d)
            remote = make_bare_remote(base)
            repo = make_clone(base, remote)
            wt = base / "wt-local-only"
            run_git(["worktree", "add", "-q", "-b", "worktree-agent-xyz", str(wt)], repo)
            # bewusst KEIN push der Branch

            result = run_prune(base)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue(wt.exists())
            self.assertIn("PRUNED 0", result.stdout)

    def test_the_main_checkout_itself_is_never_touched(self):
        with TemporaryDirectory() as d:
            base = Path(d)
            remote = make_bare_remote(base)
            repo = make_clone(base, remote)

            result = run_prune(base)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue((repo / ".git").exists())

    def test_multiple_repos_each_get_their_own_safe_worktrees_pruned(self):
        with TemporaryDirectory() as d:
            base = Path(d)
            remote_a = make_bare_remote(base / "a")
            remote_b = make_bare_remote(base / "b")
            (base / "projects").mkdir()
            repo_a = make_clone(base / "projects", remote_a, name="repo-a")
            repo_b = make_clone(base / "projects", remote_b, name="repo-b")

            wt_a = base / "projects" / "wt-a-done"
            run_git(["worktree", "add", "-q", "-b", "fix/a", str(wt_a)], repo_a)
            run_git(["push", "-q", "-u", "origin", "fix/a"], wt_a)

            wt_b = base / "projects" / "wt-b-done"
            run_git(["worktree", "add", "-q", "-b", "fix/b", str(wt_b)], repo_b)
            run_git(["push", "-q", "-u", "origin", "fix/b"], wt_b)

            result = run_prune(base)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertFalse(wt_a.exists())
            self.assertFalse(wt_b.exists())
            self.assertIn("PRUNED 2", result.stdout)


if __name__ == "__main__":
    unittest.main()
