#!/usr/bin/env python3
"""Release eines Pull Requests: erst prüfen, dann (nur auf Wunsch) mergen, taggen, veröffentlichen.

    python3 scripts/release.py 1234              # Probelauf: alle Prüfungen + Plan, ändert nichts
    python3 scripts/release.py 1234 --warten     # Probelauf, wartet vorher auf die CI des PR
    python3 scripts/release.py 1234 --ausfuehren # wirklich: Squash-Merge, Tag, GitHub-Release

Warum es das gibt: Ein Release ist der Moment, in dem ein Stand auf Kundenanlagen geht
(``scripts/update.sh`` holt die Abbilder zum Tag). Bisher lief das als Kette von Hand
(``gh pr merge`` → ``git tag`` → ``gh release create``) auf dem Rechner des Entwicklers.
Was dabei schiefgehen kann, ist alles schon passiert: ``main`` war eine Woche rot, ohne
dass es auffiel; zwei Stände trugen dieselbe Nummer (#699, #707); ein PR bekam nach der
Prüfung noch einen Commit. Dieses Skript prüft das alles VOR jeder Änderung und bricht ab,
wenn etwas nicht stimmt — und macht ohne ``--ausfuehren`` gar nichts.

Geprüft wird (jede Prüfung einzeln abschaltbar ist bewusst NICHT vorgesehen):

1. Der PR ist offen, zielt auf ``main`` und ist ohne Konflikt mergebar.
2. ``VERSION`` = ``LABEL ai-employee.version`` im Agenten-Dockerfile, der CHANGELOG hat einen
   Abschnitt für die Version, die Version ist größer als auf ``main`` (``release_track_check``).
3. Den Tag ``v<VERSION>`` gibt es noch nicht.
4. Alle Prüfungen des PR sind grün (keine laufende, keine rote).
5. Der letzte CI-Lauf auf ``main`` ist grün.
6. Beim Merge: genau der geprüfte Kopf (``--match-head-commit``), danach steht auf ``main``
   die erwartete Version, erst dann Tag und Release (Notizen = CHANGELOG-Abschnitt).

Den Server aktualisiert das Skript NICHT: Das macht jede Anlage selbst mit
``scripts/update.sh`` (Handbuch 37.4).
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import release_track_check as spur  # noqa: E402

REPO_STANDARD = "greeves89/AI-Employee"
CI_WORKFLOW = "ci.yml"


# ── Fakten ───────────────────────────────────────────────────────────────────
@dataclass
class Fakten:
    pr: int
    zustand: str = ""                 # OPEN / MERGED / CLOSED
    basis: str = ""                   # baseRefName
    mergebar: str = ""                # MERGEABLE / CONFLICTING / UNKNOWN
    kopf: str = ""                    # headRefOid
    version: str = ""                 # VERSION am Kopf
    label: str | None = None          # LABEL im Agenten-Dockerfile am Kopf
    changelog: str = ""               # CHANGELOG.md am Kopf
    version_main: str = ""            # VERSION auf main
    tag_vorhanden: bool = False
    pruefungen: list[dict] = field(default_factory=list)   # gh pr checks --json name,state
    ci_main: dict | None = None       # letzter Lauf: {"status", "conclusion", "headSha"}


def beanstandungen(f: Fakten) -> list[str]:
    """Alles, was gegen ein Release spricht — leer heißt: darf."""
    b: list[str] = []
    if f.zustand != "OPEN":
        b.append(f"PR #{f.pr} ist nicht offen ({f.zustand or 'unbekannt'})")
    if f.basis != "main":
        b.append(f"PR #{f.pr} zielt auf {f.basis or '?'}, nicht auf main")
    if f.mergebar != "MERGEABLE":
        b.append(f"PR #{f.pr} ist nicht ohne Konflikt mergebar ({f.mergebar or 'unbekannt'})")
    if not re.fullmatch(r"\d+\.\d+\.\d+", f.version or ""):
        b.append(f"VERSION am Kopf ist keine Versionsnummer: {f.version!r}")
    else:
        if f.label != f.version:
            b.append(f"LABEL im Agenten-Dockerfile ({f.label}) passt nicht zu VERSION {f.version}")
        if not spur.changelog_kennt(f.changelog, f.version):
            b.append(f"CHANGELOG hat keinen Abschnitt für {f.version}")
        if f.version_main and not spur.steigt_streng(f.version_main, f.version):
            b.append(f"VERSION {f.version} ist nicht größer als auf main ({f.version_main})")
        if f.tag_vorhanden:
            b.append(f"Tag v{f.version} gibt es schon")
    if not f.pruefungen:
        b.append("Der PR hat keine Prüfungen (CI nicht gelaufen?)")
    for p in f.pruefungen:
        if p.get("state") not in ("SUCCESS", "SKIPPED", "NEUTRAL"):
            b.append(f"Prüfung „{p.get('name')}“ ist {p.get('state')}")
    if not f.ci_main:
        b.append("Kein CI-Lauf auf main gefunden")
    elif f.ci_main.get("status") != "completed" or f.ci_main.get("conclusion") != "success":
        b.append(f"Letzter CI-Lauf auf main ist nicht grün ({f.ci_main.get('status')}/"
                 f"{f.ci_main.get('conclusion')}) — erst main reparieren")
    return b


def notizen(changelog: str, version: str) -> str:
    """Der CHANGELOG-Abschnitt der Version, ohne Überschrift — die Release-Notizen."""
    m = re.search(rf"^## \[{re.escape(version)}\][^\n]*\n(.*?)(?=^## \[|\Z)", changelog, re.M | re.S)
    return m.group(1).strip() + "\n" if m else ""


def plan(f: Fakten) -> list[str]:
    return [
        f"gh pr merge {f.pr} --squash --match-head-commit {f.kopf}",
        f"prüfen: VERSION auf main = {f.version}",
        f"git tag v{f.version} <merge-commit> && git push origin v{f.version}",
        f"gh release create v{f.version} --notes <CHANGELOG {f.version}>",
    ]


# ── Ausführung ───────────────────────────────────────────────────────────────
def lauf(*args: str, eingabe: str | None = None) -> str:
    r = subprocess.run(list(args), capture_output=True, text=True, input=eingabe)
    if r.returncode != 0:
        raise RuntimeError(f"{' '.join(args[:3])} …: {r.stderr.strip() or r.stdout.strip()}")
    return r.stdout


class GitHub:
    """Die Aufrufe an gh/git — im Test durch ein Double ersetzt."""

    def __init__(self, repo: str, run=lauf):
        self.repo, self.run = repo, run

    def fakten(self, pr: int) -> Fakten:
        info = json.loads(self.run("gh", "pr", "view", str(pr), "-R", self.repo, "--json",
                                   "state,baseRefName,mergeable,headRefOid"))
        f = Fakten(pr=pr, zustand=info.get("state", ""), basis=info.get("baseRefName", ""),
                   mergebar=info.get("mergeable", ""), kopf=info.get("headRefOid", ""))
        self.run("git", "fetch", "-q", "origin", "main", f"pull/{pr}/head")
        f.version = self.run("git", "show", f"{f.kopf}:VERSION").strip()
        f.label = spur.label_aus_dockerfile(self.run("git", "show", f"{f.kopf}:agent/Dockerfile"))
        f.changelog = self.run("git", "show", f"{f.kopf}:CHANGELOG.md")
        f.version_main = self.run("git", "show", "origin/main:VERSION").strip()
        f.tag_vorhanden = bool(self.run("git", "ls-remote", "--tags", "origin", f"refs/tags/v{f.version}").strip())
        try:
            f.pruefungen = json.loads(self.run("gh", "pr", "checks", str(pr), "-R", self.repo,
                                               "--json", "name,state") or "[]")
        except RuntimeError:
            f.pruefungen = []   # gh meldet „keine Prüfungen“ als Fehler
        laeufe = json.loads(self.run("gh", "run", "list", "-R", self.repo, "--branch", "main",
                                     "--workflow", CI_WORKFLOW, "--limit", "1",
                                     "--json", "status,conclusion,headSha"))
        f.ci_main = laeufe[0] if laeufe else None
        return f

    def ausfuehren(self, f: Fakten) -> str:
        self.run("gh", "pr", "merge", str(f.pr), "-R", self.repo, "--squash",
                 "--match-head-commit", f.kopf, "--delete-branch=false")
        self.run("git", "fetch", "-q", "origin", "main")
        auf_main = self.run("git", "show", "origin/main:VERSION").strip()
        if auf_main != f.version:
            raise RuntimeError(f"Nach dem Merge steht auf main {auf_main}, erwartet {f.version} — "
                               "kein Tag, kein Release. Bitte von Hand klären.")
        merge = self.run("git", "rev-parse", "origin/main").strip()
        self.run("git", "tag", f"v{f.version}", merge)
        self.run("git", "push", "-q", "origin", f"v{f.version}")
        return self.run("gh", "release", "create", f"v{f.version}", "-R", self.repo, "--title",
                        f"v{f.version}", "--verify-tag", "--notes-file", "-",
                        eingabe=notizen(f.changelog, f.version)).strip()


def warten_auf_ci(gh: GitHub, pr: int, minuten: int = 60, takt: int = 60) -> None:
    for _ in range(max(1, minuten * 60 // takt)):
        f = gh.fakten(pr)
        offen = [p for p in f.pruefungen if p.get("state") in ("PENDING", "QUEUED", "IN_PROGRESS")]
        if f.pruefungen and not offen:
            return
        print(f"… CI läuft noch ({len(offen)} offen)", flush=True)
        time.sleep(takt)
    raise RuntimeError(f"CI des PR #{pr} nach {minuten} Minuten nicht fertig")


def main(argv: list[str] | None = None, gh: GitHub | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("pr", type=int)
    p.add_argument("--ausfuehren", action="store_true", help="wirklich mergen, taggen, veröffentlichen")
    p.add_argument("--warten", action="store_true", help="vorher auf die CI des PR warten")
    p.add_argument("--repo", default=REPO_STANDARD)
    a = p.parse_args(argv)
    gh = gh or GitHub(a.repo)

    if a.warten:
        warten_auf_ci(gh, a.pr)
    f = gh.fakten(a.pr)
    print(f"PR #{f.pr} · Kopf {f.kopf[:8]} · Version {f.version} (main: {f.version_main})")
    fehler = beanstandungen(f)
    if fehler:
        print("\nKein Release:")
        for z in fehler:
            print(f"  - {z}")
        return 1
    print("\nAlle Prüfungen bestanden. Plan:")
    for z in plan(f):
        print(f"  {z}")
    if not a.ausfuehren:
        print("\nProbelauf — nichts geändert. Mit --ausfuehren wirklich ausführen.")
        return 0
    if not notizen(f.changelog, f.version).strip():
        print("CHANGELOG-Abschnitt ist leer — kein Release.")
        return 1
    print(f"\n{gh.ausfuehren(f)}")
    print(f"Fertig. Anlagen aktualisieren mit scripts/update.sh (Handbuch 37.4).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
