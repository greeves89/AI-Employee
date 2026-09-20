import logging
import os
import shlex
from dataclasses import dataclass, field

from app.core.log_redaction import scrub_log
from app.services.docker_service import DockerService

logger = logging.getLogger(__name__)

# Upload limits
MAX_FILE_SIZE_BYTES = 50 * 1024 * 1024  # 50 MB per file
MAX_UPLOAD_TOTAL_BYTES = 200 * 1024 * 1024  # 200 MB total per upload batch
#: Obergrenze fuers Bearbeiten im Browser. Wer eine 5-MB-Datei im Textfeld
#: aendert, hat sich vertan — und der ganze Inhalt geht durch den Speicher.
MAX_EDIT_SIZE_BYTES = 1 * 1024 * 1024  # 1 MB

# Blocked file extensions (dangerous executables / scripts that could escape container)
BLOCKED_EXTENSIONS = {
    ".exe", ".bat", ".cmd", ".com", ".scr", ".pif",
    ".msi", ".dll", ".sys", ".drv",
}


_WORKSPACE_ROOT = "/workspace"


def _validate_path(path: str) -> str:
    """Validate a container file path and confirm it stays within /workspace."""
    if "\x00" in path:
        raise ValueError("Null bytes not allowed in path")

    if not path.startswith("/"):
        raise ValueError("Path must be absolute")

    if len(path) > 4096:
        raise ValueError("Path too long")

    # Resolve .. *first*, then check confinement — this is the correct order.
    # Checking for ".." in the raw string is insufficient because normpath
    # strips them before we can catch traversal like /workspace/../etc.
    normalized = os.path.normpath(path)
    if normalized != _WORKSPACE_ROOT and not normalized.startswith(_WORKSPACE_ROOT + "/"):
        raise ValueError("Path must be within /workspace")

    # Return the normalized path (not raw input) so callers always get a clean path
    return normalized


def _validate_filename(filename: str) -> str:
    """Validate an uploaded filename."""
    if not filename or not filename.strip():
        raise ValueError("Empty filename")

    # Null byte check
    if "\x00" in filename:
        raise ValueError("Null bytes not allowed in filename")

    # Path traversal in filename
    if "/" in filename or "\\" in filename or ".." in filename:
        raise ValueError("Invalid characters in filename")

    # Blocked extensions
    _, ext = os.path.splitext(filename.lower())
    if ext in BLOCKED_EXTENSIONS:
        raise ValueError(f"File extension '{ext}' is not allowed")

    # Overly long filenames
    if len(filename) > 255:
        raise ValueError("Filename too long")

    return filename


#: Was beim Ordner-Export draussen bleibt. Alles davon ist entweder aus dem
#: Rest wiederherstellbar (`npm install`, `pip install`) oder gehoert nicht zum
#: Inhalt. In einem Projektordner machen diese Verzeichnisse leicht das
#: Tausendfache des eigentlichen Codes aus — ein Export, der daran scheitert
#: oder eine Stunde laeuft, hilft niemandem.
EXPORT_AUSGENOMMEN = {
    "node_modules", ".git", "__pycache__", ".venv", "venv", ".mypy_cache",
    ".pytest_cache", ".next", ".turbo", "dist", "build", ".cache",
}

#: Obergrenze fuer einen Ordner-Export (entpackt gemessen).
MAX_EXPORT_BYTES = 500 * 1024 * 1024  # 500 MB

#: Grenzen fuer den Ordner-Import. Das Archiv selbst muss durch den
#: Reverse-Proxy passen — Cloudflare deckelt den Anfrage-Koerper bei rund
#: 100 MB, groessere Pakete prallen dort ab, bevor sie hier ankommen. Die
#: Grenze hier liegt bewusst knapp darunter, damit die Meldung aus unserer
#: App kommt und nicht als nackte 413-Seite vom Proxy.
MAX_IMPORT_ARCHIV_BYTES = 95 * 1024 * 1024   # 95 MB Archivgroesse
MAX_IMPORT_ENTPACKT_BYTES = 1024 * 1024 * 1024  # 1 GB entpackt (Zip-Bombe)
MAX_IMPORT_EINTRAEGE = 50_000

#: Dateinamen, unter denen die Plattform eine startbare App erkennt.
#: Muss mit ``_resolve_compose_file`` in api/docker_apps.py uebereinstimmen.
COMPOSE_NAMEN = ("docker-compose.yml", "docker-compose.yaml", "compose.yml", "compose.yaml")

#: Der Verzeichnis-Scan der App-Uebersicht laeuft mit ``-maxdepth 3`` ueber
#: /workspace. Liegt die Compose-Datei tiefer, taucht die App nirgends auf.
MAX_COMPOSE_TIEFE = 3


def _ist_ausgenommen(pfad: str) -> bool:
    """Liegt dieser Eintrag in einem ausgenommenen Verzeichnis?"""
    return any(teil in EXPORT_AUSGENOMMEN for teil in pfad.split("/"))


@dataclass
class Befund:
    """Ein einzelnes Ergebnis des Schnell-Checkups.

    ``art`` ist ``"fehler"`` (App laeuft so nicht), ``"warnung"`` (laeuft
    vermutlich, aber jemand sollte hinsehen) oder ``"ok"``.
    """

    art: str
    text: str


@dataclass
class ImportBericht:
    """Was beim Import wirklich passiert ist.

    Bewusst ehrlich: ``uebersprungen`` nennt die ersten 50 Faelle beim Namen,
    ``uebersprungen_gesamt`` die Wahrheit. Ein Import, der stillschweigend
    Dateien weglaesst, ist schlimmer als einer, der abbricht.
    """

    geschrieben: int = 0
    bytes_geschrieben: int = 0
    uebersprungen: list[str] = field(default_factory=list)
    uebersprungen_gesamt: int = 0
    ordner: str = ""
    befunde: list[Befund] = field(default_factory=list)

    @property
    def startklar(self) -> bool:
        """Kann die Plattform diese App starten?"""
        return not any(b.art == "fehler" for b in self.befunde)


def _wurzelordner(namen) -> str:
    """Der gemeinsame oberste Ordner eines Archivs — oder "" wenn es keinen gibt.

    Der Export packt den Ordner MIT seinem Namen ein, ein Paket hat also
    normalerweise genau eine Wurzel. Liegt alles flach im Archiv, gibt es
    keine, und die Dateien landen direkt im Zielverzeichnis.
    """
    wurzeln = {name.split("/")[0] for name in namen if "/" in name}
    return wurzeln.pop() if len(wurzeln) == 1 else ""


def pruefe_app_paket(namen: list[str], archiv=None) -> list[Befund]:
    """Schnell-Checkup: Taugt dieses Paket als App auf der Plattform?

    Prueft genau die Bedingungen, an denen eine App sonst erst beim Startversuch
    scheitert — oder schlimmer: gar nicht erst in der Uebersicht auftaucht,
    weil der Verzeichnis-Scan sie nicht findet.
    """
    befunde: list[Befund] = []

    compose = [n for n in namen if os.path.basename(n) in COMPOSE_NAMEN]
    if not compose:
        befunde.append(Befund(
            "fehler",
            "Keine Compose-Datei gefunden. Die Plattform startet Apps ueber "
            f"{' / '.join(COMPOSE_NAMEN)} — ohne eine davon laesst sich die App nicht starten.",
        ))
    else:
        # Der Scan der App-Uebersicht laeuft mit -maxdepth 3 ueber /workspace.
        # Die Tiefe zaehlt ab dem Zielordner, deshalb +1 fuer /workspace selbst.
        tiefste = min(n.count("/") for n in compose)
        if tiefste + 1 > MAX_COMPOSE_TIEFE:
            befunde.append(Befund(
                "fehler",
                f"Die Compose-Datei liegt zu tief ({compose[0]}). Der Verzeichnis-Scan "
                f"geht nur {MAX_COMPOSE_TIEFE} Ebenen tief — tiefer liegende Apps "
                "erscheinen nicht in der Uebersicht.",
            ))
        else:
            befunde.append(Befund("ok", f"Compose-Datei gefunden: {compose[0]}"))

    # Baut ein Dienst selbst, braucht er ein Dockerfile im Paket.
    if compose and archiv is not None:
        try:
            import yaml
            with archiv.open(compose[0]) as f:
                daten = yaml.safe_load(f.read()) or {}
            dienste = daten.get("services") or {}
            baut = [n for n, d in dienste.items() if isinstance(d, dict) and d.get("build")]
            hat_dockerfile = any(os.path.basename(n).startswith("Dockerfile") for n in namen)
            if baut and not hat_dockerfile:
                befunde.append(Befund(
                    "warnung",
                    f"Dienst(e) {', '.join(baut)} bauen selbst (build:), aber im Paket "
                    "liegt kein Dockerfile. Der Start wird daran scheitern.",
                ))
            elif dienste:
                befunde.append(Befund("ok", f"{len(dienste)} Dienst(e) in der Compose-Datei"))
        except Exception as e:  # defekte YAML soll den Import nicht sprengen
            befunde.append(Befund(
                "warnung", f"Compose-Datei konnte nicht gelesen werden: {e}"
            ))

    # .env kommt beim Export mit und enthaelt typischerweise Geheimnisse.
    envs = [n for n in namen if os.path.basename(n) == ".env" or os.path.basename(n).startswith(".env.")]
    if envs:
        befunde.append(Befund(
            "warnung",
            f"Das Paket enthaelt {len(envs)} .env-Datei(en). Praktisch, weil die App "
            "sofort laeuft — aber dort stehen ueblicherweise Zugangsdaten. Bitte "
            "pruefen, bevor das Paket weitergereicht wird.",
        ))

    return befunde


class ExportZuGross(ValueError):
    """Der Ordner passt nicht in einen Export — mit Zahlen zum Anzeigen."""


class FileManager:
    """Manages file access in agent workspace volumes via Docker exec."""

    def __init__(self, docker: DockerService):
        self.docker = docker

    def list_directory(self, container_id: str, path: str = "/workspace") -> list[dict]:
        safe_path = _validate_path(path)
        # Pass args as list to avoid shell interpretation / nested-quoting injection
        exit_code, output = self.docker.exec_in_container(
            container_id,
            ["find", safe_path, "-maxdepth", "1",
             "-not", "-path", safe_path,
             "-not", "-type", "l",
             "-printf", "%y|%s|%T@|%f\n"],
        )

        entries = []
        for line in output.strip().split("\n"):
            if not line:
                continue
            parts = line.split("|", 3)
            if len(parts) != 4:
                continue

            file_type, size, mtime, name = parts
            entries.append(
                {
                    "name": name,
                    "type": "directory" if file_type == "d" else "file",
                    "size": int(size) if size.isdigit() else 0,
                    "modified": float(mtime) if mtime else 0,
                    "path": f"{path.rstrip('/')}/{name}",
                }
            )
        entries.sort(key=lambda e: e["name"])

        return entries

    def read_file(self, container_id: str, file_path: str) -> bytes:
        validated = _validate_path(file_path)
        # Verify it's not a symlink before reading (list args — no shell injection)
        exit_code, output = self.docker.exec_in_container(
            container_id, ["bash", "-c", f"test -L {shlex.quote(validated)} && echo SYMLINK || echo OK"]
        )
        if output.strip() == "SYMLINK":
            raise ValueError("Cannot read symlinks for security reasons")

        return self.docker.get_file_from_container(container_id, validated)

    def write_file(self, container_id: str, file_path: str, content: str) -> int:
        """Eine Textdatei im Arbeitsbereich ueberschreiben.

        Vom Kunden am 18.08.2026 gewuenscht: ``.env``-Dateien liessen sich
        ansehen, aber nicht aendern — wer eine Zeile korrigieren wollte, musste
        herunterladen, bearbeiten und wieder hochladen.

        Bewusst hier und nicht in der Schnittstelle: ``_validate_path`` ist die
        EINE Stelle, die den Arbeitsbereich absichert (Nullbytes, absolute
        Pfade, ``..`` NACH dem Normalisieren). Ein zweiter Schreibweg mit
        eigener Pruefung waere genau die Luecke, die man spaeter sucht.
        """
        validated = _validate_path(file_path)
        _validate_filename(os.path.basename(validated))

        roh = content.encode("utf-8")
        if len(roh) > MAX_EDIT_SIZE_BYTES:
            raise ValueError(
                f"Datei zu gross zum Bearbeiten "
                f"({len(roh)} > {MAX_EDIT_SIZE_BYTES} Bytes)"
            )

        # Symlinks und Verzeichnisse ausschliessen — ueber einen Symlink liesse
        # sich sonst ausserhalb des Arbeitsbereichs schreiben, obwohl der Pfad
        # selbst sauber aussieht. Lesen prueft dasselbe.
        ziel = shlex.quote(validated)
        _, art = self.docker.exec_in_container(
            container_id,
            ["bash", "-c", f"if [ -L {ziel} ]; then echo SYMLINK; "
                           f"elif [ -d {ziel} ]; then echo DIR; else echo OK; fi"],
        )
        art = (art or "").strip()
        if art == "SYMLINK":
            raise ValueError("Symlinks werden aus Sicherheitsgruenden nicht beschrieben")
        if art == "DIR":
            raise ValueError("Das ist ein Verzeichnis, keine Datei")

        self.docker.write_file_in_container(container_id, validated, content)
        logger.info("[Dateien] %s geschrieben (%d Bytes)", scrub_log(validated), len(roh))
        return len(roh)

    def export_folder_zip(self, container_id: str, folder: str) -> tuple[bytes, int]:
        """Einen Ordner aus dem Arbeitsbereich als ZIP zurueckgeben.

        Wunsch des Nutzers vom 21.08.2026: das Verzeichnis einer App als ZIP
        herunterladen — sowohl aus der App-Uebersicht als auch aus dem
        Dateibaum.

        Der Weg fuehrt ueber ``get_archive`` (Docker liefert ein tar) und packt
        um. Bewusst NICHT ``zip`` im Container aufrufen: das ist dort oft gar
        nicht installiert, und ein Export, der je nach Abbild funktioniert oder
        nicht, ist keiner.

        Ausgenommen sind die ueblichen Wiederherstellbaren (``node_modules``,
        ``.git``, ``__pycache__`` …). In einem Projektordner machen die leicht
        das Tausendfache des eigentlichen Codes aus.

        Gibt ``(zip_bytes, anzahl_dateien)`` zurueck.
        """
        import io
        import tarfile
        import zipfile

        validated = _validate_path(folder)

        container = self.docker.client.containers.get(container_id)
        bits, _ = container.get_archive(validated)
        tar_puffer = io.BytesIO(b"".join(bits))

        zip_puffer = io.BytesIO()
        gesamt = 0
        anzahl = 0
        # Docker packt den Ordner MIT seinem eigenen Namen als Wurzel ein —
        # genau richtig: entpackt entsteht wieder ein Ordner statt einer
        # Dateiwolke im Download-Verzeichnis.
        with tarfile.open(fileobj=tar_puffer) as tar, \
                zipfile.ZipFile(zip_puffer, "w", zipfile.ZIP_DEFLATED) as archiv:
            for eintrag in tar:
                if not eintrag.isfile():
                    continue  # Verzeichnisse entstehen von selbst, Symlinks bleiben draussen
                if _ist_ausgenommen(eintrag.name):
                    continue
                gesamt += eintrag.size
                if gesamt > MAX_EXPORT_BYTES:
                    raise ExportZuGross(
                        f"Der Ordner ist groesser als {MAX_EXPORT_BYTES // (1024 * 1024)} MB "
                        "(ohne node_modules und Konsorten). Lade einen Unterordner herunter."
                    )
                quelle = tar.extractfile(eintrag)
                if quelle is None:
                    continue
                archiv.writestr(eintrag.name, quelle.read())
                anzahl += 1

        logger.info("[Dateien] Export %s: %d Dateien, %d Bytes",
                    scrub_log(validated), anzahl, gesamt)
        return zip_puffer.getvalue(), anzahl

    def importiere_ordner_zip(
        self, container_id: str, ziel_ordner: str, archiv_bytes: bytes
    ) -> "ImportBericht":
        """Ein ZIP als App-Ordner in den Arbeitsbereich entpacken.

        Gegenstueck zu :meth:`export_folder_zip`. Erwartet wird das Format, das
        der Export erzeugt: ein ZIP mit genau EINEM Ordner an der Wurzel. Genau
        das liefert Docker beim ``get_archive``, und genau so erwartet es der
        Verzeichnis-Scan der App-Uebersicht wieder.

        Die Pruefungen folgen dem Vault-Import (``core/vault_transfer.py``),
        der dasselbe Problem schon geloest hat: Eintragszahl, entpackte Groesse
        (Zip-Bombe), Symlinks und Sonderdateien, gesperrte Endungen, und der
        Riegel gegen Pfadausbruch (``../`` im Archiv, absolute Pfade).

        Gibt einen :class:`ImportBericht` zurueck — auch dann, wenn Eintraege
        uebersprungen wurden. Ein Import, der stillschweigend die Haelfte
        weglaesst, waere schlimmer als einer, der abbricht.
        """
        import io
        import posixpath
        import zipfile

        ziel = _validate_path(ziel_ordner)

        if len(archiv_bytes) > MAX_IMPORT_ARCHIV_BYTES:
            raise ValueError(
                f"Das Archiv ist groesser als {MAX_IMPORT_ARCHIV_BYTES // (1024 * 1024)} MB. "
                "Groessere Pakete kommen nicht durch den Reverse-Proxy."
            )

        try:
            archiv = zipfile.ZipFile(io.BytesIO(archiv_bytes))
        except zipfile.BadZipFile as e:
            raise ValueError(f"Kein lesbares ZIP-Archiv: {e}") from e

        eintraege = archiv.infolist()
        if len(eintraege) > MAX_IMPORT_EINTRAEGE:
            raise ValueError(
                f"Archiv hat zu viele Eintraege ({len(eintraege)} > {MAX_IMPORT_EINTRAEGE})"
            )

        entpackt = sum(e.file_size for e in eintraege)
        if entpackt > MAX_IMPORT_ENTPACKT_BYTES:
            raise ValueError(
                f"Archiv waere entpackt zu gross ({entpackt} > {MAX_IMPORT_ENTPACKT_BYTES} Bytes)"
            )

        bericht = ImportBericht()
        dateien: list[tuple[str, bytes]] = []

        for eintrag in eintraege:
            name = eintrag.filename
            if name.endswith("/"):
                continue  # Ordner entstehen beim Entpacken von selbst

            # Symlinks und Sonderdateien: das obere Nibble des externen
            # Attributs traegt den Dateityp, 0xA = Symlink. Ein Symlink im
            # Archiv kann aus dem Zielordner herauszeigen.
            if (eintrag.external_attr >> 28) not in (0x8, 0x0):
                bericht.uebersprungen.append(f"{name} (kein regulaerer Eintrag)")
                continue

            endung = os.path.splitext(name)[1].lower()
            if endung in BLOCKED_EXTENSIONS:
                bericht.uebersprungen.append(f"{name} (gesperrte Dateiendung)")
                continue

            if _ist_ausgenommen(name):
                # node_modules & Co. gehoeren nicht ins Paket. Kommen sie
                # trotzdem mit, bleiben sie draussen — sonst dauert das
                # Entpacken laenger als ein `npm install`.
                bericht.uebersprungen.append(f"{name} (wiederherstellbar)")
                continue

            # DER Riegel: normalisieren und pruefen, dass der Eintrag im
            # Zielordner bleibt. Faengt "../"-Ausbrueche und absolute Pfade.
            sauber = posixpath.normpath(name.replace("\\", "/")).lstrip("/")
            if sauber.startswith("../") or sauber == ".." or "\x00" in sauber:
                bericht.uebersprungen.append(f"{name} (Pfad zeigt aus dem Zielordner heraus)")
                continue

            with archiv.open(eintrag) as quelle:
                inhalt = quelle.read()
            dateien.append((sauber, inhalt))
            bericht.geschrieben += 1
            bericht.bytes_geschrieben += len(inhalt)

        bericht.uebersprungen_gesamt = len(bericht.uebersprungen)
        bericht.uebersprungen = bericht.uebersprungen[:50]

        if not dateien:
            raise ValueError("Das Archiv enthaelt keine uebernehmbaren Dateien.")

        bericht.ordner = _wurzelordner(name for name, _ in dateien)
        bericht.befunde = pruefe_app_paket([name for name, _ in dateien], archiv)

        # Ein einziges tar fuer alle Dateien — derselbe Weg wie beim
        # Datei-Upload. Verschachtelte Pfade sind hier erlaubt: die Sperre
        # dagegen sitzt in upload_files (_validate_filename), nicht im
        # Schreib-Helfer, und wir haben oben selbst geprueft.
        self.docker.write_files_in_container(container_id, ziel, dateien)

        logger.info(
            "[Dateien] Import nach %s: %d Dateien, %d Bytes, %d uebersprungen",
            scrub_log(ziel), bericht.geschrieben, bericht.bytes_geschrieben,
            bericht.uebersprungen_gesamt,
        )
        return bericht

    async def upload_files(
        self, container_id: str, target_path: str, files: list[tuple[str, bytes]]
    ) -> int:
        """Upload multiple files to a container directory.

        Args:
            container_id: Docker container ID
            target_path: Target directory in container (must be under /workspace)
            files: List of (filename, content_bytes) tuples

        Returns:
            Number of files uploaded
        """
        # Security: ensure target is under /workspace
        if not target_path.startswith("/workspace"):
            raise ValueError("Upload target must be under /workspace")

        # Validate each file
        total_size = 0
        for filename, content in files:
            _validate_filename(filename)

            if len(content) > MAX_FILE_SIZE_BYTES:
                raise ValueError(
                    f"File '{filename}' exceeds maximum size "
                    f"({len(content)} > {MAX_FILE_SIZE_BYTES} bytes)"
                )
            total_size += len(content)

        if total_size > MAX_UPLOAD_TOTAL_BYTES:
            raise ValueError(
                f"Total upload size exceeds limit "
                f"({total_size} > {MAX_UPLOAD_TOTAL_BYTES} bytes)"
            )

        safe_path = _validate_path(target_path)
        self.docker.exec_in_container(container_id, ["mkdir", "-p", safe_path])

        self.docker.write_files_in_container(container_id, target_path, files)
        logger.info(f"Uploaded {len(files)} files ({total_size} bytes) to {scrub_log(target_path)}")
        return len(files)

    def search_files(
        self, container_id: str, query: str, base: str = "/workspace", limit: int = 40
    ) -> list[dict]:
        """Find files/folders BY NAME (case-insensitive) under ``base`` (kept within
        /workspace). Args passed as a list — no shell interpretation/injection."""
        safe_base = _validate_path(base)
        q = (query or "").strip()
        if not q:
            return []
        exit_code, output = self.docker.exec_in_container(
            container_id,
            ["find", safe_base, "-maxdepth", "5", "-not", "-type", "l",
             "-iname", f"*{q}*", "-printf", "%y|%s|%p\n"],
        )
        entries: list[dict] = []
        for line in output.strip().split("\n"):
            if not line:
                continue
            parts = line.split("|", 2)
            if len(parts) != 3:
                continue
            file_type, size, full = parts
            entries.append({
                "name": full.rsplit("/", 1)[-1],
                "type": "directory" if file_type == "d" else "file",
                "size": int(size) if size.isdigit() else 0,
                "path": full,
            })
            if len(entries) >= limit:
                break
        return entries

    def get_file_info(self, container_id: str, file_path: str) -> dict:
        safe_path = _validate_path(file_path)
        exit_code, output = self.docker.exec_in_container(
            container_id, ["stat", "-c", "%s|%Y|%F", safe_path]
        )

        if exit_code != 0:
            raise FileNotFoundError(f"File not found: {file_path}")

        parts = output.strip().split("|")
        return {
            "size": int(parts[0]) if len(parts) > 0 else 0,
            "modified": int(parts[1]) if len(parts) > 1 else 0,
            "type": parts[2] if len(parts) > 2 else "unknown",
        }
