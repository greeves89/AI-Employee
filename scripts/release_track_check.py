#!/usr/bin/env python3
"""Prueft die Release-Spur: VERSION, Dockerfile-Label und CHANGELOG-Abdeckung.

Hintergrund (#699): Die Pflicht, zu jeder Aenderung `VERSION`, das
`LABEL ai-employee.version` in `agent/Dockerfile` und einen CHANGELOG-Abschnitt
zu fuehren, war reine Disziplin — nichts prueft sie maschinell. Entsprechend
trugen am 02.09.2026 gleich zwei Paare offener PRs dieselbe Nummer, drei weitere
lagen unter dem Stand von main, und ein Direkt-Commit hatte gar keinen Eintrag.
Wer wissen will, was auf seiner Anlage neu ist, schaut in den CHANGELOG — steht
dort nichts, ist die Aenderung unsichtbar ausgerollt worden.

**Geprueft wird Monotonie, nicht Lueckenlosigkeit.** Eine auf einem Branch
gesetzte Nummer ist kein Versprechen an main: liegen mehrere versionierte
Branches in der Warteschlange und main laeuft dazwischen weiter, entstehen
Luecken voellig regulaer (belegt an 1.276.7). Eine Regel gegen Luecken wuerde bei
jedem gestapelten Branch falsch anschlagen — und ein Check, der oft falsch
anschlaegt, wird bald ignoriert.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

WURZEL = Path(__file__).resolve().parents[1]

#: Dateien, die allein noch keinen Versionssprung rechtfertigen. Ein reiner
#: Nachtrag in der Doku soll nicht am Versions-Check scheitern.
NUR_DOKU = re.compile(r"^(docs/|\.github/ISSUE_TEMPLATE/|[^/]*\.md$)")
#: ... mit Ausnahme des CHANGELOG selbst — der gehoert zur Spur.
DOKU_AUSNAHME = {"CHANGELOG.md"}

LABEL = re.compile(r'^LABEL ai-employee\.version="([^"]+)"', re.M)


def als_zahlen(version: str) -> tuple[int, ...]:
    """`1.276.11` -> (1, 276, 11).

    Zeichenkettenvergleich waere hier schlimmer als kein Check: er haelt
    `1.276.9` fuer groesser als `1.276.11`.
    """
    teile = version.strip().split(".")
    try:
        return tuple(int(t) for t in teile)
    except ValueError as e:
        raise ValueError(f"Keine Versionsnummer: {version!r}") from e


def steigt_streng(alt: str, neu: str) -> bool:
    return als_zahlen(neu) > als_zahlen(alt)


def label_aus_dockerfile(text: str) -> str | None:
    treffer = LABEL.search(text)
    return treffer.group(1) if treffer else None


def changelog_kennt(text: str, version: str) -> bool:
    return re.search(rf"^## \[{re.escape(version)}\]", text, re.M) is not None


def nur_doku_beruehrt(pfade: list[str]) -> bool:
    """Ob eine Aenderung ausschliesslich Dokumentation angefasst hat."""
    echte = [p for p in pfade
             if p not in DOKU_AUSNAHME and not NUR_DOKU.match(p)]
    return not echte


def git(*args: str) -> str:
    return subprocess.run(["git", "-C", str(WURZEL), *args],
                          capture_output=True, text=True, check=True).stdout.strip()


def _lies(pfad: str, ref: str | None = None) -> str:
    if ref is None:
        return (WURZEL / pfad).read_text()
    return git("show", f"{ref}:{pfad}")


def pruefe_push(vorher: str) -> list[str]:
    """Harte Pruefung nach einem Push auf main. Gibt die Beanstandungen zurueck."""
    fehler: list[str] = []
    version = _lies("VERSION").strip()

    label = label_aus_dockerfile(_lies("agent/Dockerfile"))
    if label != version:
        fehler.append(
            f"VERSION ist {version}, das Label in agent/Dockerfile aber {label!r}. "
            "Beide muessen gleich sein — sonst meldet ein laufender Agent eine "
            "Version, die es so nie gab."
        )

    if not changelog_kennt(_lies("CHANGELOG.md"), version):
        fehler.append(
            f"CHANGELOG.md hat keinen Abschnitt `## [{version}]`. Ohne ihn sieht "
            "der Betreiber nicht, was auf seiner Anlage neu ist."
        )

    geaendert = [z for z in git("diff", "--name-only", vorher, "HEAD").splitlines() if z]
    if nur_doku_beruehrt(geaendert):
        return fehler  # reiner Nachtrag: kein Versionssprung noetig

    vorherige = _lies("VERSION", vorher).strip()
    if not steigt_streng(vorherige, version):
        fehler.append(
            f"VERSION steigt nicht: vorher {vorherige}, jetzt {version}. "
            "Geprueft wird Monotonie, nicht Lueckenlosigkeit — uebersprungene "
            "Nummern sind in Ordnung, ein Stillstand oder Rueckschritt nicht."
        )
    return fehler


def doppelt_vergeben(meine: str, fremde_versionen: dict[str, str]) -> list[str]:
    """Welche anderen offenen PRs dieselbe Nummer tragen.

    Rein gehalten, damit die Faelle ohne Git und ohne GitHub pruefbar sind —
    genau diese Doppelvergabe ist am 02.09.2026 zweimal aufgetreten.
    """
    return [f"#{nr} ({v.strip()})" for nr, v in sorted(fremde_versionen.items())
            if v.strip() == meine.strip()]


def pruefe_pull_request(basis: str, fremde_versionen: dict[str, str]) -> list[str]:
    """Weiche Warnungen fuer einen offenen PR — der Merge kann sie noch aufloesen."""
    warnungen: list[str] = []
    meine = _lies("VERSION").strip()
    ihre = _lies("VERSION", basis).strip()

    if not steigt_streng(ihre, meine):
        warnungen.append(
            f"Dieser Branch traegt {meine}, main steht schon auf {ihre}. Beim Merge "
            "laeuft die Nummer rueckwaerts, sofern der Merge sie nicht aufloest."
        )

    doppelt = doppelt_vergeben(meine, fremde_versionen)
    if doppelt:
        warnungen.append(
            f"Version {meine} ist auch in {', '.join(doppelt)} vergeben. Wer zuerst "
            "merged, entwertet die Nummer des anderen."
        )
    return warnungen


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("modus", choices=["push", "pr"])
    p.add_argument("--vorher", help="Vergleichs-Commit (push: der vorherige Stand)")
    p.add_argument("--basis", default="origin/main", help="Basis-Ref (pr)")
    p.add_argument("--fremde", default="",
                   help="pr: 'nr=version,nr=version' der anderen offenen PRs")
    args = p.parse_args()

    if args.modus == "push":
        vorher = args.vorher or "HEAD^"
        beanstandungen = pruefe_push(vorher)
        if beanstandungen:
            print("Release-Spur unvollstaendig:\n")
            for b in beanstandungen:
                print(f"  - {b}")
            print("\nHintergrund: #699")
            return 1
        print(f"Release-Spur in Ordnung (VERSION {_lies('VERSION').strip()})")
        return 0

    fremde = dict(
        eintrag.split("=", 1) for eintrag in args.fremde.split(",") if "=" in eintrag
    )
    warnungen = pruefe_pull_request(args.basis, fremde)
    if warnungen:
        print("Hinweise zur Release-Spur (kein Fehlschlag):\n")
        for w in warnungen:
            print(f"  - {w}")
    else:
        print("Release-Spur dieses Branches ist stimmig.")
    return 0  # bei einem PR NIE blockieren


if __name__ == "__main__":
    sys.exit(main())
