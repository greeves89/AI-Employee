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


def run_prune(root: Path, lokale_repos: bool = True) -> subprocess.CompletedProcess:
    import os
    # Die Testrepos liegen lokal (``file``-Transport) — im Betrieb ist der verboten.
    env = dict(**os.environ, WORKTREE_PRUNE_ROOT=str(root))
    if lokale_repos:
        env["WORKTREE_PRUNE_LOKALE_REPOS"] = "1"
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


class WorktreePruneHaertungTest(unittest.TestCase):
    """Issue #964 Punkt 1: der Helfer liest Ablagen, die der AGENT beschreibt.

    Deren ``.git/config`` ist damit kein vertrauenswuerdiger Eingabewert: ein
    ``core.fsmonitor``-Eintrag startet beim naechsten ``git status`` ein beliebiges
    Programm — und zwar in dem Container, den der ORCHESTRATOR hochfaehrt, nicht im
    Container des Agenten. Die Tests pruefen das Verhalten (laeuft das Programm?),
    nicht die Schreibweise der ``-c``-Schalter.
    """

    def _repo_mit_bosartiger_konfig(self, base: Path, schluessel: str) -> tuple[Path, Path, Path]:
        remote = make_bare_remote(base)
        repo = make_clone(base, remote)
        wt = base / "wt-done"
        run_git(["worktree", "add", "-q", "-b", "fix/done", str(wt)], repo)
        run_git(["push", "-q", "-u", "origin", "fix/done"], wt)

        marker = base / "marker"
        programm = base / "boese.sh"
        programm.write_text("#!/bin/sh\ntouch %s\nexit 1\n" % marker)
        programm.chmod(0o755)
        # In BEIDE Konfigurationen: die Worktree teilt die Konfiguration des Repos
        # nicht in jedem Fall (``extensions.worktreeConfig``).
        run_git(["config", schluessel, str(programm)], repo)
        run_git(["config", schluessel, str(programm)], wt)
        return repo, wt, marker

    def test_core_fsmonitor_der_ablage_wird_nicht_ausgefuehrt(self):
        with TemporaryDirectory() as d:
            base = Path(d)
            _repo, wt, marker = self._repo_mit_bosartiger_konfig(base, "core.fsmonitor")

            result = run_prune(base)

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertFalse(marker.exists(),
                             "core.fsmonitor aus der Ablage wurde ausgefuehrt: " + result.stdout)
            # Und die Haertung darf den Zweck nicht aufheben — sauber + gepusht
            # heisst weiterhin: weg damit (Issue #830).
            self.assertFalse(wt.exists(), "Haertung hat das Aufraeumen mitgenommen: " + result.stdout)
            self.assertIn("PRUNED 1", result.stdout)

    def test_uploadpack_der_ablage_wird_nicht_ausgefuehrt(self):
        """Gegenprobe zu #967: ``remote.origin.uploadpack`` in der Agenten-Konfiguration
        startet bei ``ls-remote`` über einen lokalen Pfad ein beliebiges Programm."""
        with TemporaryDirectory() as d:
            base = Path(d)
            _repo, wt, marker = self._repo_mit_bosartiger_konfig(base, "remote.origin.uploadpack")

            result = run_prune(base)

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertFalse(marker.exists(),
                             "remote.origin.uploadpack aus der Ablage wurde ausgefuehrt: " + result.stdout)
            self.assertIn("PRUNED 1", result.stdout)  # der Zweck bleibt

    def test_im_betrieb_kein_lokaler_transport(self):
        """Ohne die Testfreigabe ist ``file`` verboten: kein Programm, und die Worktree bleibt
        stehen (ohne Fernabgleich wird nichts entfernt — die sichere Richtung)."""
        with TemporaryDirectory() as d:
            base = Path(d)
            _repo, wt, marker = self._repo_mit_bosartiger_konfig(base, "remote.origin.uploadpack")

            result = run_prune(base, lokale_repos=False)

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertFalse(marker.exists())
            self.assertTrue(wt.exists())
            self.assertIn("PRUNED 0", result.stdout)

    def test_core_sshcommand_der_ablage_wird_nicht_ausgefuehrt(self):
        with TemporaryDirectory() as d:
            base = Path(d)
            repo, wt, marker = self._repo_mit_bosartiger_konfig(base, "core.sshCommand")
            # ls-remote ueber ssh waere der Weg, auf dem core.sshCommand greift.
            run_git(["remote", "set-url", "origin", "ssh://example.invalid/x.git"], repo)

            result = run_prune(base)

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertFalse(marker.exists(),
                             "core.sshCommand aus der Ablage wurde ausgefuehrt: " + result.stdout)
            # Kein Fernabgleich moeglich -> Worktree bleibt stehen (sichere Richtung).
            self.assertTrue(wt.exists())
            self.assertIn("PRUNED 0", result.stdout)

    def test_status_showuntrackedfiles_der_ablage_verdeckt_keine_arbeit(self):
        """Der schwerere Teil derselben Klasse: nicht nur Schalter, die ein Programm
        STARTEN, sondern die, die das SICHERHEITSURTEIL bilden. Mit
        ``status.showUntrackedFiles=no`` meldet ``git status --short`` eine Worktree
        mit unversionierter Arbeit als sauber — und dann wird sie entfernt."""
        with TemporaryDirectory() as d:
            base = Path(d)
            remote = make_bare_remote(base)
            repo = make_clone(base, remote)
            wt = base / "wt-dirty"
            run_git(["worktree", "add", "-q", "-b", "fix/dirty", str(wt)], repo)
            run_git(["push", "-q", "-u", "origin", "fix/dirty"], wt)
            (wt / "nicht-gesicherte-arbeit.md").write_text("die einzige Fassung\n")
            run_git(["config", "status.showUntrackedFiles", "no"], wt)

            result = run_prune(base)

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue(wt.exists(),
                            "Worktree mit unversionierter Arbeit wurde entfernt: " + result.stdout)
            self.assertIn("PRUNED 0", result.stdout)

    def test_core_excludesfile_der_ablage_verdeckt_keine_arbeit(self):
        with TemporaryDirectory() as d:
            base = Path(d)
            remote = make_bare_remote(base)
            repo = make_clone(base, remote)
            wt = base / "wt-dirty"
            run_git(["worktree", "add", "-q", "-b", "fix/dirty", str(wt)], repo)
            run_git(["push", "-q", "-u", "origin", "fix/dirty"], wt)
            (wt / "nicht-gesicherte-arbeit.md").write_text("die einzige Fassung\n")
            (base / "alles-ignorieren").write_text("*\n")
            run_git(["config", "core.excludesFile", str(base / "alles-ignorieren")], wt)

            result = run_prune(base)

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue(wt.exists(),
                            "Worktree mit unversionierter Arbeit wurde entfernt: " + result.stdout)
            self.assertIn("PRUNED 0", result.stdout)

    def test_fehlgeschlagener_git_aufruf_wird_gemeldet(self):
        """Vorher endete jeder Fehlschlag in einem stillen ``continue`` — ein Volume,
        auf dem seit Wochen nichts mehr aufgeraeumt wird, sah genauso aus wie eines
        ohne Worktrees."""
        with TemporaryDirectory() as d:
            base = Path(d)
            remote = make_bare_remote(base)
            repo = make_clone(base, remote)
            wt = base / "wt-done"
            run_git(["worktree", "add", "-q", "-b", "fix/done", str(wt)], repo)
            run_git(["push", "-q", "-u", "origin", "fix/done"], wt)
            # Fernzweig unerreichbar machen: ls-remote scheitert. Bewusst ein
            # lokaler Pfad OHNE Repo -- git antwortet darauf MEHRZEILIG, und genau
            # das ist der Fall, den die Einzeiler-Zusicherung unten pruefen soll.
            kein_repo = base / "gar-kein-repo"
            kein_repo.mkdir()
            run_git(["remote", "set-url", "origin", str(kein_repo)], repo)

            result = run_prune(base)

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("GIT_FEHLER", result.stdout)
            self.assertTrue(wt.exists())
            # Der Orchestrator liest die Ausgabe ZEILENWEISE: eine mehrzeilige
            # git-Meldung verliert dort alles ab der zweiten Zeile, und eine
            # stderr-Zeile, die mit PRUNED anfaengt, waere ein gefaelschtes Ergebnis.
            fehlerzeilen = [z for z in result.stdout.splitlines() if z.startswith("GIT_FEHLER")]
            self.assertTrue(fehlerzeilen)
            ergebniszeilen = [z for z in result.stdout.splitlines() if z.startswith("PRUNED")]
            self.assertEqual(len(ergebniszeilen), 1, result.stdout)
            for zeile in fehlerzeilen:
                self.assertIn("rc=", zeile)
                self.assertNotIn("\n", zeile)
            # Vorbedingung des Tests: git hat hier WIRKLICH mehrzeilig geantwortet,
            # sonst waere die Zusicherung oben leer.
            self.assertIn("does not appear to be a git repository",
                          " ".join(fehlerzeilen), result.stdout)
            # Zweite git-Zeile muss in DERSELBEN Protokollzeile stehen.
            self.assertIn("Could not read from remote repository",
                          " ".join(fehlerzeilen), result.stdout)

    def test_safe_directory_landet_nicht_in_der_konfiguration_des_abbilds(self):
        """``git config --global --add`` schrieb in die Konfiguration des Abbilds;
        dort gilt jeder Eintrag fuer den Rest des Laufs und fuer jede spaeter
        gefundene Ablage. Jetzt gilt er nur fuer diesen Lauf."""
        import os
        with TemporaryDirectory() as d:
            base = Path(d)
            heim = base / "heim"
            heim.mkdir()
            remote = make_bare_remote(base)
            repo = make_clone(base, remote)
            wt = base / "wt-done"
            run_git(["worktree", "add", "-q", "-b", "fix/done", str(wt)], repo)
            run_git(["push", "-q", "-u", "origin", "fix/done"], wt)

            env = dict(os.environ)
            env.update(WORKTREE_PRUNE_ROOT=str(base), HOME=str(heim), WORKTREE_PRUNE_LOKALE_REPOS="1")
            env.pop("GIT_CONFIG_GLOBAL", None)
            result = subprocess.run([sys.executable, "-c", WORKTREE_PRUNE_SCRIPT],
                                    capture_output=True, text=True, env=env)

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("PRUNED 1", result.stdout)
            self.assertFalse((heim / ".gitconfig").exists(),
                             "safe.directory wurde in die Konfiguration des Abbilds geschrieben")


if __name__ == "__main__":
    unittest.main()
