"""Vault als ZIP heraus- und hineinbringen.

Wunsch des Nutzers vom 18.08.2026: Import und Export fuer Second Brains, beim
Import „Sync mittels Obsidian und mittels Upload einer Zip-Datei".

Recherchiert: **Obsidian Sync hat keine oeffentliche Schnittstelle** — es ist
ein geschlossener, Ende-zu-Ende-verschluesselter Bezahldienst. Man kann sich
nicht daranhaengen. Ein Vault ist aber nichts als Markdown in einer
Ordnerstruktur, und genau so liegt ein Second Brain ohnehin schon auf der
Platte. Deshalb der Weg ueber die Ordnerstruktur statt ueber den Dienst.

Sicherheit ist hier der Schwerpunkt, denn ein ZIP ist Fremdeingabe:

* **Zip-Slip** — Eintraege wie ``../../etc/passwd``. Jeder Pfad geht durch
  denselben Riegel wie der Dateibrowser (``vault.resolve_path``), nicht durch
  eine zweite, eigene Pruefung.
* **Zip-Bombe** — wenige Kilobyte, die zu Gigabyte werden. Deshalb zaehlt die
  ENTPACKTE Groesse mit, nicht die des Archivs.
* **Symlinks und Sonderdateien** — werden uebersprungen; ueber einen Symlink
  liesse sich sonst spaeter ausserhalb des Vaults schreiben.
"""

import logging
import os
import zipfile
from dataclasses import dataclass, field

from app.core import vault

log = logging.getLogger(__name__)

#: Obergrenzen. Ein Vault ist Text; wer hier anschlaegt, laedt etwas anderes hoch.
MAX_ARCHIV_BYTES = 200 * 1024 * 1024        # 200 MB Archivgroesse
MAX_ENTPACKT_BYTES = 1024 * 1024 * 1024     # 1 GB entpackt (Zip-Bomben-Schutz)
MAX_EINTRAEGE = 50_000

#: Was in einem Vault nichts zu suchen hat. Bewusst dieselbe Liste wie beim
#: Datei-Upload in den Arbeitsbereich — zwei Listen laufen auseinander.
GESPERRTE_ENDUNGEN = {
    ".exe", ".bat", ".cmd", ".com", ".scr", ".pif",
    ".msi", ".dll", ".sys", ".drv",
}

#: Ordner, die beim Export draussen bleiben. `.git` kann riesig sein und
#: gehoert dem Sync-Weg, nicht dem Inhalt; `.obsidian` traegt Geraete- und
#: Plugin-Einstellungen, die auf einem anderen Rechner nur stoeren.
EXPORT_AUSGENOMMEN = (".git", ".trash")


@dataclass
class ImportBericht:
    """Was der Import getan hat — damit der Nutzer es SIEHT statt zu raten."""
    geschrieben: int = 0
    uebersprungen: list[str] = field(default_factory=list)
    geloescht: int = 0
    bytes_geschrieben: int = 0

    def als_dict(self) -> dict:
        return {
            "written": self.geschrieben,
            "deleted": self.geloescht,
            "bytes": self.bytes_geschrieben,
            # Gekuerzt: bei einem kaputten Archiv waeren es sonst Tausende Zeilen.
            "skipped": self.uebersprungen[:50],
            "skipped_total": len(self.uebersprungen),
        }


def _ist_harmlos(name: str) -> bool:
    _, endung = os.path.splitext(name.lower())
    return endung not in GESPERRTE_ENDUNGEN


def importiere_zip(host_path: str, archiv: zipfile.ZipFile, *, ersetzen: bool = False) -> ImportBericht:
    """Ein ZIP in den Vault entpacken.

    ``ersetzen=False`` (Vorgabe) fuegt zusammen: vorhandene Dateien werden
    ueberschrieben, nicht enthaltene bleiben stehen. ``ersetzen=True`` macht den
    Vault zum Abbild des Archivs — was nicht drin ist, wird geloescht. Das ist
    die ehrlichere Lesart von „Sync", aber die gefaehrlichere; die Oberflaeche
    fragt deshalb nach.
    """
    bericht = ImportBericht()
    eintraege = archiv.infolist()

    if len(eintraege) > MAX_EINTRAEGE:
        raise ValueError(f"Archiv hat zu viele Eintraege ({len(eintraege)} > {MAX_EINTRAEGE})")

    gesamt = sum(e.file_size for e in eintraege)
    if gesamt > MAX_ENTPACKT_BYTES:
        raise ValueError(
            f"Archiv waere entpackt zu gross ({gesamt} > {MAX_ENTPACKT_BYTES} Bytes)"
        )

    behalten: set[str] = set()

    for eintrag in eintraege:
        name = eintrag.filename
        if name.endswith("/"):
            continue  # Ordner entstehen beim Schreiben von selbst

        # Symlinks und Sonderdateien: das obere Nibble des externen Attributs
        # traegt den Dateityp. 0xA = Symlink.
        if (eintrag.external_attr >> 28) not in (0x8, 0x0):
            bericht.uebersprungen.append(f"{name} (kein regulaerer Eintrag)")
            continue

        if not _ist_harmlos(name):
            bericht.uebersprungen.append(f"{name} (gesperrte Dateiendung)")
            continue

        # DER Riegel: derselbe wie im Dateibrowser. Faengt ../-Ausbrueche und
        # absolute Pfade im Archiv.
        try:
            ziel = vault.resolve_path(host_path, name)
        except ValueError as e:
            bericht.uebersprungen.append(f"{name} ({e})")
            continue

        os.makedirs(os.path.dirname(ziel), exist_ok=True)
        with archiv.open(eintrag) as quelle, open(ziel, "wb") as senke:
            inhalt = quelle.read()
            senke.write(inhalt)
        try:
            os.chmod(ziel, 0o666)
        except OSError:
            pass

        bericht.geschrieben += 1
        bericht.bytes_geschrieben += len(inhalt)
        behalten.add(os.path.realpath(ziel))

    if ersetzen:
        bericht.geloescht = _entferne_uebrige(host_path, behalten)

    return bericht


def _entferne_uebrige(host_path: str, behalten: set[str]) -> int:
    """Alles loeschen, was nicht im Archiv stand — nur bei ``ersetzen=True``."""
    entfernt = 0
    wurzel = os.path.realpath(host_path)
    for ordner, unterordner, dateien in os.walk(wurzel, topdown=True):
        unterordner[:] = [u for u in unterordner if u not in EXPORT_AUSGENOMMEN]
        for datei in dateien:
            pfad = os.path.realpath(os.path.join(ordner, datei))
            if pfad in behalten:
                continue
            # Zur Sicherheit doppelt: nichts ausserhalb der Wurzel anfassen,
            # falls os.walk ueber einen Symlink gelaufen ist.
            if not pfad.startswith(wurzel + os.sep):
                continue
            try:
                os.remove(pfad)
                entfernt += 1
            except OSError as e:
                log.warning("[Vault] %s nicht loeschbar: %s", vault.safe_log(pfad), e)
    return entfernt


def exportiere_zip(host_path: str, ziel_datei) -> int:
    """Den Vault als ZIP schreiben. Gibt die Zahl der Dateien zurueck."""
    wurzel = os.path.realpath(host_path)
    anzahl = 0
    with zipfile.ZipFile(ziel_datei, "w", zipfile.ZIP_DEFLATED) as archiv:
        for ordner, unterordner, dateien in os.walk(wurzel, topdown=True):
            unterordner[:] = [u for u in unterordner if u not in EXPORT_AUSGENOMMEN]
            for datei in sorted(dateien):
                pfad = os.path.join(ordner, datei)
                # Symlinks nicht mitnehmen: das Ziel liegt womoeglich ausserhalb
                # des Vaults, und dann exportierten wir fremde Dateien.
                if os.path.islink(pfad):
                    continue
                archiv.write(pfad, os.path.relpath(pfad, wurzel))
                anzahl += 1
    return anzahl
