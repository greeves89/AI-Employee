"""Die CLI-Aktualisierung im Startskript darf den Agenten nie ohne CLI zuruecklassen.

Am 21.09.2026, kurz nach dem Ausrollen des Abbruch-Deckels (`timeout -k`),
meldete ein Agent im Gespraech ``No such file or directory: claude``. Im
Container lag unter ``/usr/lib/node_modules/@anthropic-ai/`` nur noch ein
halb umbenanntes ``.claude-code-ZjqcDZyQ`` neben einem unbrauchbaren
``claude-code``; ``which claude`` fand nichts, ein Reparaturversuch scheiterte
mit ``ENOTEMPTY``.

Ursache: ``npm install -g`` ist **nicht atomar**. npm benennt das vorhandene
Paketverzeichnis zuerst weg und schreibt danach das neue. Wird es in diesem
Fenster abgebrochen, bleibt weder die alte noch die neue Fassung. Vorher fiel
das nicht auf, weil ``timeout`` ohne ``-k`` gar nicht durchgriff -- der Aufruf
hing stattdessen endlos, was auf seine Weise genauso kaputt war. Der Deckel
hat den Fehler also nicht verursacht, sondern sichtbar gemacht.

Die Meldung des Startskripts behauptete dabei, man behalte "the installed
version" -- sie war schlicht falsch.

Jetzt wird zuerst vollstaendig in eine Zwischenablage installiert und erst
danach per Umbenennen eingehaengt. Diese Tests messen das an einer
npm-Attrappe nach: Sie pruefen nicht den Text des Skripts, sondern was nach
einem Abbruch, einem Fehlschlag und einem Erfolg wirklich auf der Platte liegt.
"""

import os
import shutil
import subprocess
import tempfile
import textwrap
import unittest

_ENTRYPOINT = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "entrypoint.sh"
)

#: Die Attrappen-CLI, die als "bereits installiert" gilt.
_PAKET = "@anthropic-ai/claude-code"
_BIN = "claude"


@unittest.skipUnless(
    shutil.which("timeout") is not None,
    "timeout nicht verfuegbar (macOS ohne coreutils) -- in der CI laeuft dieser Test",
)
class CliAktualisierungTest(unittest.TestCase):
    def setUp(self):
        self.wurzel = tempfile.mkdtemp()
        self.module = os.path.join(self.wurzel, "node_modules")
        self.bins = os.path.join(self.wurzel, "bin")
        self.attrappen = os.path.join(self.wurzel, "attrappen")
        for d in (self.module, self.bins, self.attrappen):
            os.makedirs(d, exist_ok=True)
        self._installiere("1.0.0-alt")

    def tearDown(self):
        shutil.rmtree(self.wurzel, ignore_errors=True)

    # --- Aufbau ------------------------------------------------------------

    def _installiere(self, version: str) -> None:
        """Legt eine lauffaehige 'vorhandene Installation' an."""
        ziel = os.path.join(self.module, _PAKET)
        os.makedirs(ziel, exist_ok=True)
        skript = os.path.join(ziel, "cli.js")
        with open(skript, "w") as f:
            f.write(f'#!/bin/sh\necho "{version}"\n')
        os.chmod(skript, 0o755)
        verknuepfung = os.path.join(self.bins, _BIN)
        if os.path.lexists(verknuepfung):
            os.remove(verknuepfung)
        os.symlink(skript, verknuepfung)

    def _npm_attrappe(self, verhalten: str) -> None:
        """Schreibt ein 'npm', das sich wie gewuenscht verhaelt.

        ``haengt``   -- laeuft in die Frist (und ignoriert SIGTERM, wie das
                        echte npm), waehrend es NICHTS am Ziel anfasst;
        ``fehler``   -- bricht mit Fehlercode ab;
        ``erfolg``   -- legt ein vollstaendiges Paket in der Zwischenablage an.
        """
        pfad = os.path.join(self.attrappen, "npm")
        if verhalten == "haengt":
            inhalt = textwrap.dedent("""\
                #!/bin/bash
                trap '' TERM
                sleep 60
            """)
        elif verhalten == "fehler":
            inhalt = textwrap.dedent("""\
                #!/bin/bash
                echo "npm error ENOTEMPTY" >&2
                exit 1
            """)
        else:
            inhalt = textwrap.dedent(f"""\
                #!/bin/bash
                # Zwischenablage aus "--prefix <dir>" herauslesen.
                prefix=""
                while [ $# -gt 0 ]; do
                  if [ "$1" = "--prefix" ]; then prefix="$2"; shift; fi
                  shift
                done
                ziel="$prefix/lib/node_modules/{_PAKET}"
                mkdir -p "$ziel" "$prefix/bin"
                printf '#!/bin/sh\\necho "2.0.0-neu"\\n' > "$ziel/cli.js"
                chmod +x "$ziel/cli.js"
                ln -sf "$ziel/cli.js" "$prefix/bin/{_BIN}"
            """)
        with open(pfad, "w") as f:
            f.write(inhalt)
        os.chmod(pfad, 0o755)

    def _lauf(self, frist: str = "2"):
        umgebung = {
            **os.environ,
            "PATH": self.attrappen + os.pathsep + os.environ["PATH"],
            "ENTRYPOINT_MODULE_DIR": self.module,
            "ENTRYPOINT_BIN_DIR": self.bins,
            "ENTRYPOINT_NPM_TIMEOUT": frist,
            "ENTRYPOINT_NUR_DEFINIEREN": "1",
        }
        return subprocess.run(
            ["bash", "-c",
             f'source "{_ENTRYPOINT}"; update_cli "{_PAKET}" "{_BIN}"'],
            capture_output=True, text=True, env=umgebung,
        )

    def _version(self):
        """Was meldet die installierte CLI -- oder warum nicht?"""
        pfad = os.path.join(self.bins, _BIN)
        if not os.path.exists(pfad):
            return None
        r = subprocess.run([pfad], capture_output=True, text=True)
        return r.stdout.strip() if r.returncode == 0 else None

    # --- Die eigentlichen Pruefungen ---------------------------------------

    def test_abbruch_laesst_die_vorhandene_fassung_unberuehrt(self):
        """Der Fall vom 21.09.: Frist laeuft ab, waehrend npm arbeitet."""
        self._npm_attrappe("haengt")
        ergebnis = self._lauf(frist="2")
        self.assertEqual(
            self._version(), "1.0.0-alt",
            "Nach einem Abbruch stand der Agent ohne CLI da -- genau der Fehler, "
            "der im Gespraech als 'No such file or directory: claude' ankam.\n"
            + ergebnis.stdout + ergebnis.stderr,
        )

    def test_fehlschlag_laesst_die_vorhandene_fassung_unberuehrt(self):
        self._npm_attrappe("fehler")
        self._lauf()
        self.assertEqual(self._version(), "1.0.0-alt")

    def test_erfolg_haengt_die_neue_fassung_ein(self):
        self._npm_attrappe("erfolg")
        ergebnis = self._lauf(frist="30")
        self.assertEqual(
            self._version(), "2.0.0-neu",
            "Die neue Fassung wurde nicht eingehaengt.\n" + ergebnis.stdout + ergebnis.stderr,
        )

    def test_nach_dem_einhaengen_bleibt_kein_schutt_liegen(self):
        """Die Ausweich-Kopie muss weg sein -- sonst waechst das Abbild bei
        jedem Start um eine weitere Fassung."""
        self._npm_attrappe("erfolg")
        self._lauf(frist="30")
        uebrig = [n for n in os.listdir(os.path.join(self.module, "@anthropic-ai"))
                  if n.endswith(".vorher") or n.startswith(".")]
        self.assertEqual(uebrig, [], f"Liegengebliebene Verzeichnisse: {uebrig}")

    def test_der_agent_startet_auch_ohne_erreichbares_npm(self):
        """Kein npm im Pfad: Das Skript darf trotzdem nicht abbrechen
        (``set -eu`` ist aktiv) und die CLI muss stehen bleiben."""
        # Nur die Systemverzeichnisse -- genug fuer bash/timeout, aber ohne npm.
        ohne_npm = os.pathsep.join(
            d for d in os.environ["PATH"].split(os.pathsep)
            if not os.path.exists(os.path.join(d, "npm"))
        )
        ergebnis = subprocess.run(
            ["bash", "-c", f'source "{_ENTRYPOINT}"; update_cli "{_PAKET}" "{_BIN}"; echo FERTIG'],
            capture_output=True, text=True,
            env={**os.environ, "PATH": ohne_npm,
                 "ENTRYPOINT_MODULE_DIR": self.module,
                 "ENTRYPOINT_BIN_DIR": self.bins,
                 "ENTRYPOINT_NUR_DEFINIEREN": "1"},
        )
        self.assertIn("FERTIG", ergebnis.stdout, ergebnis.stdout + ergebnis.stderr)
        self.assertEqual(self._version(), "1.0.0-alt")


if __name__ == "__main__":
    unittest.main()
