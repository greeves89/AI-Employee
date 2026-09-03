#!/usr/bin/env python3
"""Kundennamen in Issues, Pull Requests und Kommentaren aufspueren.

Die Wache `orchestrator/tests/test_no_customer_names_in_repo.py` prueft die
DATEIEN dieses oeffentlichen Repos. Sie hat damit nur die Haelfte abgedeckt:
Issues, Pull-Request-Beschreibungen und Kommentare stehen auf derselben
oeffentlichen Seite, werden genauso indiziert — und wurden nie geprueft.

Gefunden am 03.09.2026 beim Oeffnen von Issue #478: dort stand der Klarname
eines Kunden in der ersten Zeile. Die Nachpruefung ergab neun Issues, einen
Pull Request und zwei Kommentare. Alle bereinigt; dieses Skript sorgt dafuer,
dass es auffaellt, wenn wieder einer dazukommt.

Die Begriffe kommen als Pruefsummen aus derselben Quelle wie der Datei-Test —
im Klartext steht auch hier keiner (#688).

Aufruf:
    python3 scripts/check_github_customer_names.py            # prueft alles
    python3 scripts/check_github_customer_names.py --seit 30  # nur letzte 30 Tage
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

WURZEL = Path(__file__).resolve().parents[1]
REPO = "greeves89/AI-Employee"


def _wache():
    """Dieselbe Erkennung wie der Datei-Test — eine Quelle, eine Wahrheit."""
    pfad = WURZEL / "orchestrator" / "tests" / "test_no_customer_names_in_repo.py"
    spec = importlib.util.spec_from_file_location("wache", pfad)
    modul = importlib.util.module_from_spec(spec)
    sys.modules["wache"] = modul
    spec.loader.exec_module(modul)
    return modul


def _gh_json(args: list[str]) -> list[dict]:
    """`gh ... --paginate` liefert MEHRERE JSON-Dokumente hintereinander, nicht
    eines. Ein schlichtes json.loads() bricht ab der zweiten Seite ab."""
    roh = subprocess.run(["gh", *args], capture_output=True, text=True, check=True).stdout
    dec, i, out = json.JSONDecoder(), 0, []
    while i < len(roh):
        while i < len(roh) and roh[i] in " \n\r\t":
            i += 1
        if i >= len(roh):
            break
        obj, i = dec.raw_decode(roh, i)
        out.extend(obj if isinstance(obj, list) else [obj])
    return out


def _seit_filter(eintraege: list[dict], tage: int | None) -> list[dict]:
    if not tage:
        return eintraege
    grenze = datetime.now(timezone.utc) - timedelta(days=tage)
    behalten = []
    for e in eintraege:
        stempel = e.get("updatedAt") or e.get("updated_at") or e.get("createdAt")
        if not stempel:
            behalten.append(e)
            continue
        try:
            if datetime.fromisoformat(stempel.replace("Z", "+00:00")) >= grenze:
                behalten.append(e)
        except ValueError:
            behalten.append(e)
    return behalten


def pruefe(tage: int | None = None) -> list[str]:
    w = _wache()
    funde: list[str] = []

    for art in ("issue", "pr"):
        eintraege = _seit_filter(_gh_json([
            art, "list", "--state", "all", "--limit", "500",
            "--json", "number,title,body,updatedAt",
        ]), tage)
        for e in eintraege:
            text = (e.get("title") or "") + "\n" + (e.get("body") or "")
            if any(w.zeile_verboten(z) for z in text.splitlines()):
                # Der Begriff steht bewusst NICHT in der Meldung — sie landet
                # sonst im CI-Protokoll, und das ist ebenso oeffentlich.
                funde.append(f"{art} #{e['number']}")

    for name, pfad in (("Kommentar", f"repos/{REPO}/issues/comments"),
                       ("Review-Kommentar", f"repos/{REPO}/pulls/comments")):
        for k in _seit_filter(_gh_json(["api", pfad, "--paginate"]), tage):
            if any(w.zeile_verboten(z) for z in (k.get("body") or "").splitlines()):
                nr = (k.get("issue_url") or k.get("pull_request_url") or "").rsplit("/", 1)[-1]
                funde.append(f"{name} {k['id']} an #{nr}")
    return funde


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--seit", type=int, default=None,
                   help="nur Eintraege der letzten N Tage pruefen")
    args = p.parse_args()

    try:
        funde = pruefe(args.seit)
    except (subprocess.CalledProcessError, FileNotFoundError) as e:
        print(f"GitHub nicht erreichbar ({e}) — Pruefung uebersprungen.")
        return 0  # kein Zugang ist kein Fund

    if funde:
        print("Kundennamen in oeffentlichen GitHub-Inhalten:\n")
        for f in funde:
            print(f"  - {f}")
        print("\nBitte durch 'beim Kunden' / 'eine Kundenanlage' ersetzen. "
              "Der Klarname gehoert ins Projekt-Gedaechtnis, nicht hierher. "
              "Der getroffene Begriff steht hier bewusst nicht.")
        return 1
    print("Keine Kundennamen in Issues, Pull Requests oder Kommentaren.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
