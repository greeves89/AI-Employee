# shellcheck shell=bash
# Gemeinsame Hilfen für backup.sh, restore.sh und setup.sh (#892).
#
# Hier steht EINE Liste, was gesichert wird — backup.sh und restore.sh lesen
# beide daraus, damit Sichern und Rückspielen nicht auseinanderlaufen.
# Bewusst ohne bash-4-Eigenheiten (läuft auch mit dem bash 3.2 von macOS).
# Wird von orchestrator/tests/test_datensicherung.py ohne Docker geprüft.

# Volumes des Compose-Projekts, die mitgesichert werden (ohne Projekt-Präfix).
# Die Datenbank fehlt bewusst: sie wird per pg_dump gesichert — ein tar der
# laufenden Datendateien wäre nicht konsistent. Modell-Caches
# (embedding_models, stt_models) lassen sich jederzeit neu laden.
SICHERUNG_PROJEKT_VOLUMES="redis_data feedback_data"

# Liest Volume-Namen von stdin (einer je Zeile) und gibt aus, was gesichert wird,
# je Zeile: "<archivname> <volumename>".
#
#   workspace-<id>        Arbeitsordner eines Agenten
#   claude-session-<id>   Sitzungen/Anmeldungen der Laufzeit eines Agenten
#   build-tools-<id>      installierte Build-Werkzeuge (Windows-Programme)
#   ai-employee-shared    gemeinsamer Ordner aller Agenten
#   <projekt>_redis_data, <projekt>_feedback_data
#
# Projektvolumes bekommen den projektneutralen Archivnamen "compose.<name>":
# so lässt sich eine Sicherung auch in eine Anlage mit anderem
# Verzeichnisnamen zurückspielen.
volumes_auswaehlen() {
    local projekt="$1" name kurz
    while IFS= read -r name || [ -n "$name" ]; do
        [ -z "$name" ] && continue
        # Nur Zeichen, die Docker in Volume-Namen erlaubt — alles andere wäre
        # entweder kein Volume oder ein Versuch, aus dem Sicherungsordner zu laufen.
        case "$name" in
            *[!A-Za-z0-9_.-]*) continue ;;
        esac
        case "$name" in
            workspace-?*|claude-session-?*|build-tools-?*|ai-employee-shared)
                echo "$name $name"
                continue
                ;;
        esac
        for kurz in $SICHERUNG_PROJEKT_VOLUMES; do
            if [ "$name" = "${projekt}_${kurz}" ]; then
                echo "compose.${kurz} ${name}"
            fi
        done
    done
}

# Archivname -> Volume-Name in der Ziel-Anlage.
volume_fuer_archiv() {
    local projekt="$1" archiv="$2"
    case "$archiv" in
        compose.*) echo "${projekt}_${archiv#compose.}" ;;
        *) echo "$archiv" ;;
    esac
}

# Einen Wert aus einer .env lesen, ohne sie auszuführen (`source .env` scheitert
# an Werten mit Leerzeichen oder $ und würde jede Zeile als Befehl ausführen).
env_wert() {
    local datei="$1" schluessel="$2"
    [ -f "$datei" ] || return 0
    # `|| true`: ein fehlender Schlüssel ist kein Fehler — unter `set -e -o
    # pipefail` bräche sonst schon das Nachsehen das ganze Skript ab.
    { grep -E "^${schluessel}=" "$datei" || true; } | tail -n 1 | cut -d'=' -f2- \
        | sed -e 's/^"\(.*\)"$/\1/' -e "s/^'\(.*\)'\$/\1/"
}

# Mindestlänge des Herzschlag-Schlüssels — kürzere nimmt der Orchestrator nicht
# an (orchestrator/app/config.py, BACKUP_TOKEN_MINDESTLAENGE).
BACKUP_TOKEN_MINDESTLAENGE=32

# Den lokalen Schlüssel für den Herzschlag anlegen, falls er fehlt oder zu kurz
# ist (ein kurzer würde vom Orchestrator ohnehin ignoriert).
# Rückgabe 0 = neu angelegt, 1 = war schon da (oder keine .env).
backup_token_sicherstellen() {
    local datei="$1" token vorhanden
    [ -f "$datei" ] || return 1
    vorhanden=$(env_wert "$datei" BACKUP_STATUS_TOKEN)
    [ "${#vorhanden}" -ge "$BACKUP_TOKEN_MINDESTLAENGE" ] && return 1
    token=$(python3 -c 'import secrets; print(secrets.token_hex(32))' 2>/dev/null \
        || openssl rand -hex 32)
    [ -n "$token" ] || return 1
    if grep -q "^BACKUP_STATUS_TOKEN=" "$datei"; then
        sed -i.bak "s|^BACKUP_STATUS_TOKEN=.*|BACKUP_STATUS_TOKEN=${token}|" "$datei" && rm -f "${datei}.bak"
    else
        printf '\nBACKUP_STATUS_TOKEN=%s\n' "$token" >> "$datei"
    fi
    return 0
}

# Name des Compose-Projekts der laufenden Anlage. Volumes heißen
# "<projekt>_<name>"; das Projekt ist ohne `name:` in der Compose-Datei der
# Verzeichnisname — deshalb wird es am laufenden Postgres-Container abgelesen.
compose_projekt() {
    local install_dir="$1" projekt
    if [ -n "${COMPOSE_PROJECT_NAME:-}" ]; then
        echo "$COMPOSE_PROJECT_NAME"
        return
    fi
    projekt=$(docker inspect "${PG_CONTAINER:-ai-employee-postgres}" \
        --format '{{ index .Config.Labels "com.docker.compose.project" }}' 2>/dev/null || true)
    if [ -n "$projekt" ] && [ "$projekt" != "<no value>" ]; then
        echo "$projekt"
        return
    fi
    basename "$install_dir" | tr '[:upper:]' '[:lower:]'
}

# sha256 einer Datei — GNU (Linux) oder BSD (macOS).
pruefsumme() {
    if command -v sha256sum >/dev/null 2>&1; then
        sha256sum "$@"
    else
        shasum -a 256 "$@"
    fi
}

# Konfiguration (.env, orchestrator/data/) einpacken.
#
# Der Orchestrator legt orchestrator/data/ als root an, den Schlüssel mit 600 —
# als normaler Nutzer ist er nicht lesbar (erster echter Lauf auf einer Anlage:
# Sicherung brach genau beim Schlüssel ab). Dann liest ein Hilfscontainer wie bei
# den Volumes; das Archiv schreibt trotzdem die Shell, also mit unseren Rechten.
#   konfiguration_einpacken <archiv> <install_dir> <image> <teil>...
konfiguration_einpacken() {
    local archiv="$1" install_dir="$2" image="$3"
    shift 3
    if tar czf "$archiv" -C "$install_dir" "$@" 2>/dev/null; then
        return 0
    fi
    echo "Konfiguration nicht direkt lesbar (Schlüssel gehört root) — lese über Hilfscontainer" >&2
    docker run --rm -v "${install_dir}:/quelle:ro" "$image" tar czf - -C /quelle "$@" > "$archiv"
}

# Zustand eines Sicherungsordners — für restore.sh (was liegt hier?) und für die
# Aufbewahrung in backup.sh (was zählt als Sicherung?):
#   vollstaendig    MANIFEST mit format=2, kein ABGEBROCHEN
#   unvollstaendig  Lauf abgebrochen: ABGEBROCHEN liegt da (backup.sh schreibt es
#                   beim Abbruch), oder Teile des Formats 2 (postgres.sql.gz,
#                   konfiguration.tar.gz, volumes/) ohne MANIFEST — das entsteht
#                   erst ganz am Ende. Fehlt ABGEBROCHEN, kam der Lauf nicht mehr
#                   zum Aufräumen (kill, Stromausfall).
#   alt             Format vor #892 (postgres_<db>_<zeit>.sql.gz)
#   leer            nichts davon
sicherung_zustand() {
    local ordner="$1" alt
    if [ -f "${ordner}/ABGEBROCHEN" ]; then
        echo unvollstaendig
    elif [ -f "${ordner}/MANIFEST" ] && grep -qx "format=2" "${ordner}/MANIFEST"; then
        echo vollstaendig
    elif [ -e "${ordner}/postgres.sql.gz" ] || [ -e "${ordner}/konfiguration.tar.gz" ] \
            || [ -d "${ordner}/volumes" ] || [ -e "${ordner}/MANIFEST.tmp" ]; then
        echo unvollstaendig
    else
        alt=$(find "$ordner" -maxdepth 1 -name "postgres_*.sql.gz" 2>/dev/null | head -n 1)
        if [ -n "$alt" ]; then echo alt; else echo leer; fi
    fi
}

# Ist ein gzip-Archiv heil? Ein beim Abbruch abgerissenes Archiv fällt hier auf —
# in einer unvollständigen Sicherung gibt es keine Prüfsummen, die das täten.
archiv_heil() {
    gzip -t "$1" >/dev/null 2>&1
}

# Gegenstück: Konfiguration zurücklegen — direkt, sonst über einen Hilfscontainer,
# der root-eigene Dateien (Schlüssel) überschreiben darf und Besitzer erhält.
#   konfiguration_auspacken <archiv> <install_dir> <image>
konfiguration_auspacken() {
    local archiv="$1" install_dir="$2" image="$3"
    if tar xzf "$archiv" -C "$install_dir" 2>/dev/null; then
        return 0
    fi
    echo "Konfiguration nicht direkt schreibbar (Schlüssel gehört root) — lege über Hilfscontainer zurück" >&2
    docker run --rm -i -v "${install_dir}:/ziel" "$image" tar xzf - -C /ziel < "$archiv"
}

# Freier Platz dort, wo Docker seine Volumes ablegt (Kilobyte).
platz_frei_kb() {
    local ort
    ort=$(docker info --format '{{.DockerRootDir}}' 2>/dev/null || true)
    [ -d "${ort:-}" ] || ort="/"
    df -Pk "$ort" 2>/dev/null | awk 'NR==2 {print $4}'
}

# Bedarf einer Sicherung beim Zurückspielen (Kilobyte, entpackt).
# Neue Sicherungen schreiben ihn ins MANIFEST; bei älteren wird geschätzt:
# Archive entpacken sich erfahrungsgemäß auf das Drei- bis Vierfache, ein
# Datenbank-Dump auf das Fünf- bis Sechsfache.
#   platz_bedarf_kb <sicherungsordner> <datenbankdatei> <nur_datenbank true|false>
platz_bedarf_kb() {
    local ordner="$1" db_datei="$2" nur_db="$3" entpackt="" datenbank="" kb
    if [ -f "${ordner}/MANIFEST" ]; then
        entpackt=$(grep -E '^entpackt_kb=[0-9]+$' "${ordner}/MANIFEST" | head -n 1 | cut -d= -f2 || true)
        datenbank=$(grep -E '^datenbank_kb=[0-9]+$' "${ordner}/MANIFEST" | head -n 1 | cut -d= -f2 || true)
    fi
    if [ -z "$datenbank" ] || [ "$datenbank" = "0" ]; then
        kb=$(du -k "$db_datei" 2>/dev/null | cut -f1)
        datenbank=$(( ${kb:-0} * 6 ))
    fi
    if [ "$nur_db" = "true" ]; then
        entpackt=0
    elif [ -z "$entpackt" ]; then
        kb=$(du -sk "${ordner}/volumes" 2>/dev/null | cut -f1)
        entpackt=$(( ${kb:-0} * 4 ))
    fi
    echo $(( entpackt + datenbank ))
}

# Abbruch, wenn der Platz nicht reicht — bevor irgendetwas angehalten oder
# überschrieben wird. 2 GB Reserve, damit die Platte nicht randvoll läuft.
#   platz_pruefen <sicherungsordner> <datenbankdatei> <nur_datenbank> <ignorieren>
platz_pruefen() {
    local bedarf frei reserve=2097152
    bedarf=$(platz_bedarf_kb "$1" "$2" "$3")
    frei=$(platz_frei_kb)
    frei="${frei:-0}"
    echo "Platzbedarf: etwa $(( (bedarf + 1048575) / 1048576 )) GB, frei: $(( frei / 1048576 )) GB."
    if [ $(( bedarf + reserve )) -gt "$frei" ]; then
        if [ "$4" = "true" ]; then
            echo "WARNUNG: Der Platz reicht voraussichtlich nicht — fortgesetzt wegen --platz-ignorieren."
            return 0
        fi
        echo "FEHLER: Zu wenig freier Platz für diese Sicherung — nichts verändert." >&2
        echo "        Platz schaffen oder (wenn bestehende Arbeitsordner ohnehin ersetzt werden und" >&2
        echo "        dadurch Platz frei wird) mit --platz-ignorieren wiederholen." >&2
        exit 1
    fi
}
