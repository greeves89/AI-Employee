# shellcheck shell=bash
# Abbilder bereitstellen (#889) — gemeinsam fuer setup.sh und update.sh.
#
# Holt die fertigen Abbilder genau der Version in VERSION aus der Registry
# (AI_EMPLOYEE_REGISTRY, Standard ghcr.io/greeves89): die vier Compose-Dienste
# mit eigenem Dockerfile und das Agenten-Abbild, das kein Compose-Dienst ist und
# als ai-employee-agent:latest getaggt wird (darauf zeigt AGENT_IMAGE). Ist die
# Registry nicht erreichbar, die Version dort (noch) nicht gebaut oder lokales
# Bauen verlangt, wird wie bisher lokal gebaut.
#
# Traegt AI_EMPLOYEE_VERSION in .env ein: Code (git) und Abbild gehoeren zur
# selben Version, auch fuer ein spaeteres `docker compose up -d`.
#
#   abbilder_bereitstellen <lokal_bauen 0|1>
# Setzt ABBILD_QUELLE auf "registry" oder "lokal". Erwartet cwd = Repo-Wurzel.

ABBILD_DIENSTE="orchestrator frontend embedding-service stt-service"

# ---------------------------------------------------------------------------
# Signaturpruefung der geholten Abbilder (#938)
#
# Die Abbilder sind oeffentlich und wurden allein anhand des Versions-TAGS
# geholt: wer Konto oder Bau-Vorgang uebernimmt, kann unter einem gueltigen Tag
# ein fremdes Abbild ablegen. images.yml signiert deshalb jedes Manifest
# schluessellos ueber GitHub OIDC; hier wird geprueft, BEVOR ein Abbild benutzt
# wird. Schlaegt die Pruefung fehl, wird das geholte Abbild entfernt und lokal
# gebaut.
#
# cosign ist PFLICHT (seit 1.424): fehlt es, holt cosign_bereitstellen die
# festgelegte Fassung (Pruefsumme) nach ~/.ai-employee/bin. Gelingt das nicht,
# werden die Abbilder NICHT ungeprueft benutzt, sondern lokal gebaut — bis 1.423
# liefen sie dann ungeprueft, und nur ein Hinweis stand im Protokoll.
#   AI_EMPLOYEE_SIGNATUR_PRUEFEN=0        Pruefung bewusst abschalten
#   AI_EMPLOYEE_SIGNATUR_IDENTITAET=...   eigene Identitaet (eigener Spiegel)
#   AI_EMPLOYEE_SIGNATUR_AUSSTELLER=...   eigener OIDC-Aussteller
# ---------------------------------------------------------------------------
SIGNATUR_AUSSTELLER="https://token.actions.githubusercontent.com"
# Repo-Name im Zertifikat des Bau-Vorgangs. Ohne ihn wuerde JEDES andere Repo
# desselben Kontos mit einer images.yml als Unterzeichner durchgehen.
SIGNATUR_REPO="AI-Employee"

# Festgelegte cosign-Fassung (veroeffentlicht 06.08.2026) und ihre Pruefsummen
# aus cosign_checksums.txt des Release.
COSIGN_VERSION="v3.1.3"
COSIGN_SHA256_AMD64="4629c757b7618056f8ddd7e2625ae9fdd94c0372a65049520bc7d9df9efc7f71"
COSIGN_SHA256_ARM64="c5d324e091826b0d7a78eb16fef316450b4eb9aaec045611c08ba06f5e73220a"
COSIGN_QUELLE="https://github.com/sigstore/cosign/releases/download"

_sha256() {
    if command -v sha256sum >/dev/null 2>&1; then sha256sum "$1" | cut -d' ' -f1
    else shasum -a 256 "$1" | cut -d' ' -f1; fi
}

# cosign_bereitstellen — cosign im PATH, sonst die festgelegte Fassung holen.
# Ohne sudo: abgelegt unter ~/.ai-employee/bin, nur mit passender Pruefsumme.
cosign_bereitstellen() {
    command -v cosign >/dev/null 2>&1 && return 0
    local ziel arch summe datei
    ziel="${AI_EMPLOYEE_STATUS_DIR:-$HOME/.ai-employee}/bin"
    case "$(uname -s)/$(uname -m)" in
        Linux/x86_64|Linux/amd64) arch=amd64; summe="$COSIGN_SHA256_AMD64" ;;
        Linux/aarch64|Linux/arm64) arch=arm64; summe="$COSIGN_SHA256_ARM64" ;;
        *) return 1 ;;
    esac
    if [ -x "$ziel/cosign" ] && [ "$(_sha256 "$ziel/cosign")" = "$summe" ]; then
        PATH="$ziel:$PATH"; export PATH; return 0
    fi
    mkdir -p "$ziel" && chmod 700 "$ziel" || return 1
    datei="$ziel/cosign.neu"
    if ! curl -fsSL --max-time 180 -o "$datei" "$COSIGN_QUELLE/$COSIGN_VERSION/cosign-linux-$arch"; then
        rm -f "$datei"; return 1
    fi
    if [ "$(_sha256 "$datei")" != "$summe" ]; then
        echo "cosign-Download hat nicht die erwartete Pruefsumme — verworfen."
        rm -f "$datei"; return 1
    fi
    chmod 755 "$datei" && mv -f "$datei" "$ziel/cosign" || return 1
    PATH="$ziel:$PATH"; export PATH
    echo "cosign ${COSIGN_VERSION} nach ${ziel} geholt (Pruefsumme stimmt)."
}

# abbilder_env <NAME> — Wert der Variablen, ersatzweise aus .env.
#
# setup.sh und update.sh laden .env NICHT (das macht docker compose selbst).
# AI_EMPLOYEE_REGISTRY steht laut Handbuch aber genau dort: ohne diese Zeilen
# holt Compose vom Spiegel, waehrend dieses Skript die ghcr-Vorgabe pruefte —
# die Abbilder lagen dort nie, und jede Spiegel-Anlage baute ab sofort bei
# jedem Update alles selbst. Gelesen werden nur die drei eigenen Namen, nie
# ausgefuehrt.
abbilder_env() {
    local name="$1" wert
    wert="${!name:-}"
    if [ -z "$wert" ] && [ -f .env ]; then
        wert="$(sed -n "s/^[[:space:]]*${name}=//p" .env | tail -n1)"
        wert="${wert%\"}"; wert="${wert#\"}"
        wert="${wert%\'}"; wert="${wert#\'}"
    fi
    printf '%s' "$wert"
}

# abbilder_besitzer_zulaessig <registry> — Konto-Name aus ghcr.io/<konto>/...
abbilder_besitzer_zulaessig() {
    local besitzer="${1#ghcr.io/}"
    besitzer="${besitzer%%/*}"
    case "$besitzer" in
        ""|*[!A-Za-z0-9._-]*) return 1 ;;
        *) return 0 ;;
    esac
}

# abbilder_identitaet <registry> <version> -> erwartete Unterzeichner-Identitaet
#
# Besitzer kommt aus der Registry (ghcr.io/<besitzer>/...), damit ein Fork ohne
# Anpassung funktioniert. Gebunden sind Besitzer, Repo, Workflow UND der
# Versions-Tag: eine Signatur vom Hauptzweig, aus einem anderen Repo desselben
# Kontos oder von einer anderen Version zaehlt nicht. Nur der Name selbst ist
# gross-/kleinschreibungsfrei (ghcr.io schreibt klein, GitHub nicht) — der
# Dateiname images.yml NICHT, denn Images.yml waere eine andere Datei.
abbilder_identitaet() {
    local besitzer="${1#ghcr.io/}" version="$2"
    besitzer="${besitzer%%/*}"
    # Nur der Punkt ist hier ein Regexp-Zeichen; alles andere laesst
    # abbilder_besitzer_zulaessig gar nicht erst durch.
    besitzer="${besitzer//./\\.}"
    printf '^https://github\\.com/(?i:%s/%s)/\\.github/workflows/images\\.yml@refs/tags/v%s$' \
        "$besitzer" "$SIGNATUR_REPO" "${version//./\\.}"
}

# Ergebnis der letzten Pruefung fuer den Sicherheitsstatus (Admin → Betrieb). Der
# Orchestrator liest ~/.ai-employee als /host-auth mit. Eine Zeile: Ergebnis Version
# Zeitpunkt. Ergebnis: geprueft | cosign_fehlt | aus | ohne_version |
# ohne_identitaet | ungueltig.
abbilder_pruefung_merken() {
    local ordner="${AI_EMPLOYEE_STATUS_DIR:-${HOME:-/root}/.ai-employee}"
    mkdir -p "$ordner" 2>/dev/null || return 0
    printf '%s %s %s\n' "$1" "$2" "$(date +%s)" > "$ordner/signaturpruefung" 2>/dev/null || true
}

# abbilder_signatur_pruefen <registry> <version> <dienst...>
# 0 = alle geprueft oder bewusst uebersprungen, 1 = mindestens eine ungueltig
_abbilder_signatur_pruefen() {
    local registry="$1" version="$2" identitaet aussteller dienst ref digest
    shift 2
    # Nur ausdrueckliches Aus schaltet aus. "true"/"ja" hiess hier sonst
    # stillschweigend "keine Pruefung" — ein Sicherheitsschalter, dessen
    # Ein-Schreibweisen Aus bedeuten.
    case "$(abbilder_env AI_EMPLOYEE_SIGNATUR_PRUEFEN)" in
        0|false|False|nein|no|off|aus) ABBILD_SIGNATUR=aus; return 0 ;;
    esac
    if [ "$version" = "latest" ]; then
        echo "Hinweis: ohne VERSION-Datei keine versionsgebundene Signaturpruefung (#938)."
        ABBILD_SIGNATUR=ohne_version; return 0
    fi
    identitaet="$(abbilder_env AI_EMPLOYEE_SIGNATUR_IDENTITAET)"
    if [ -z "$identitaet" ]; then
        case "$registry" in
            ghcr.io/*)
               # Der Besitzer wandert in ein Regexp. Nur zulaessige GitHub-Namen
               # durchlassen, statt zu hoffen, dass niemand Metazeichen in
               # AI_EMPLOYEE_REGISTRY schreibt.
               if ! abbilder_besitzer_zulaessig "$registry"; then
                   echo "Hinweis: ${registry} ergibt keinen gueltigen Konto-Namen — Signaturen werden NICHT geprueft (#938)."
                   ABBILD_SIGNATUR=ohne_identitaet; return 0
               fi
               identitaet="$(abbilder_identitaet "$registry" "$version")" ;;
            # Eigener Spiegel ohne hinterlegte Identitaet: die ghcr-Signatur
            # liegt dort nicht. Ein Abbruch wuerde jede Spiegel-Anlage zum
            # Dauer-Lokalbau verurteilen — also Hinweis statt Fehlschlag.
            *) echo "Hinweis: Signaturen von ${registry} werden NICHT geprueft — AI_EMPLOYEE_SIGNATUR_IDENTITAET ist nicht gesetzt (#938)."
               ABBILD_SIGNATUR=ohne_identitaet; return 0 ;;
        esac
    fi
    if ! cosign_bereitstellen; then
        echo "cosign fehlt und liess sich nicht holen — die Abbilder werden NICHT ungeprueft benutzt, sondern lokal gebaut (#938)."
        echo "         Bewusst ohne Pruefung: AI_EMPLOYEE_SIGNATUR_PRUEFEN=0 in der .env."
        ABBILD_SIGNATUR=cosign_fehlt; return 1
    fi
    aussteller="$(abbilder_env AI_EMPLOYEE_SIGNATUR_AUSSTELLER)"
    aussteller="${aussteller:-$SIGNATUR_AUSSTELLER}"
    for dienst in "$@"; do
        ref="${registry}/ai-employee-${dienst}:${version}"
        # Den geholten Digest pruefen, nicht den Tag: so ist das geprueft, was
        # hier liegt, und nicht, worauf der Tag zwischenzeitlich zeigt.
        digest="$(docker image inspect --format '{{range .RepoDigests}}{{println .}}{{end}}' "$ref" 2>/dev/null \
            | grep -F "${ref%:*}@" | head -n1)"
        if [ -z "$digest" ]; then
            echo "Kein Digest fuer ${ref} — Signatur nicht pruefbar, baue lokal (#938)."
            ABBILD_SIGNATUR=ungueltig; return 1
        fi
        # -a dienst=...: ohne die Zusage ist nur der Digest signiert, nicht
        # WOFUER. Sonst liesse sich ein signiertes Manifest samt Signatur auf
        # den Tag eines anderen Dienstes kopieren — der Agent liefe dann als
        # Orchestrator, mit dessen Zugaengen.
        if ! cosign verify \
                --certificate-oidc-issuer "$aussteller" \
                --certificate-identity-regexp "$identitaet" \
                -a "dienst=${dienst}" \
                "$digest" >/dev/null; then
            echo "Signatur von ${digest} ist NICHT gueltig oder nicht abrufbar — Abbild wird nicht benutzt, baue lokal (#938)."
            echo "         Erwartet: ${identitaet} (Aussteller ${aussteller}, dienst=${dienst})"
            ABBILD_SIGNATUR=ungueltig; return 1
        fi
    done
    echo "Signaturen aller Abbilder ${version} geprueft (cosign)."
    ABBILD_SIGNATUR=geprueft; return 0
}

# abbilder_signatur_pruefen <registry> <version> <dienst...> — wie oben, merkt das Ergebnis.
abbilder_signatur_pruefen() {
    local rc
    ABBILD_SIGNATUR=""
    _abbilder_signatur_pruefen "$@"
    rc=$?
    abbilder_pruefung_merken "${ABBILD_SIGNATUR:-unbekannt}" "$2"
    return $rc
}

# abbilder_verwerfen <registry> <version> <dienst...>
#
# docker compose pull legt die Abbilder unter genau den Referenzen ab, die
# docker-compose.yml als image: fuehrt. Ein abgelehntes Abbild bliebe sonst
# liegen, und ein spaeteres `docker compose up -d` von Hand wuerde es starten —
# der lokale Bau ueberschreibt nur, wenn er auch durchlaeuft.
abbilder_verwerfen() {
    local registry="$1" version="$2" dienst
    shift 2
    for dienst in "$@"; do
        docker image rm -f "${registry}/ai-employee-${dienst}:${version}" >/dev/null 2>&1 || true
    done
}

# abbilder_env_setzen <NAME> <wert> — Schluessel in .env eintragen/ersetzen.
#
# Nur Namen und Werte ohne Sonderzeichen; beides kommt hier aus VERSION bzw. aus
# festen Zeichenketten, nie von aussen.
abbilder_env_setzen() {
    local name="$1" wert="$2"
    [ -f .env ] || return 0
    if grep -q "^${name}=" .env; then
        sed -i.bak "s|^${name}=.*|${name}=${wert}|" .env && rm -f .env.bak
    else
        printf '%s=%s\n' "$name" "$wert" >> .env
    fi
}

abbilder_bereitstellen() {
    local lokal_bauen="${1:-0}" version registry
    version="$(tr -d '[:space:]' < VERSION 2>/dev/null || true)"
    version="${version:-latest}"
    registry="$(abbilder_env AI_EMPLOYEE_REGISTRY)"
    registry="${registry:-ghcr.io/greeves89}"
    export AI_EMPLOYEE_VERSION="$version"
    # Fuer den lokalen Bau: Version ins Frontend-Bundle (update-banner.tsx).
    export APP_VERSION="$version"
    abbilder_env_setzen AI_EMPLOYEE_VERSION "$version"

    ABBILD_QUELLE="lokal"
    if [ "$lokal_bauen" != "1" ]; then
        echo "Hole fertige Abbilder ${version} aus ${registry} ..."
        # shellcheck disable=SC2086  # bewusst getrennte Liste
        if docker compose pull -q $ABBILD_DIENSTE \
            && docker pull -q "${registry}/ai-employee-agent:${version}" >/dev/null; then
            # Erst pruefen, dann benutzen (#938).
            # shellcheck disable=SC2086  # bewusst getrennte Liste
            if abbilder_signatur_pruefen "$registry" "$version" $ABBILD_DIENSTE agent; then
                docker tag "${registry}/ai-employee-agent:${version}" ai-employee-agent:latest
                # Ein frueherer Vermerk gilt nicht mehr: diese Version ist geprueft.
                abbilder_env_setzen AI_EMPLOYEE_ABBILD_ABGELEHNT ""
                ABBILD_QUELLE="registry"
                return 0
            fi
            # shellcheck disable=SC2086  # bewusst getrennte Liste
            abbilder_verwerfen "$registry" "$version" $ABBILD_DIENSTE agent
            # Dauerhaft festhalten, WELCHE Version durchgefallen ist (#889):
            # der Orchestrator holt das Agenten-Abbild sonst selbst nach, wenn es
            # lokal fehlt — und damit kaeme genau das verworfene Abbild wieder
            # herein, sobald ein "docker image prune" den lokalen Bau wegnimmt.
            abbilder_env_setzen AI_EMPLOYEE_ABBILD_ABGELEHNT "$version"
        else
            echo "Abbilder ${version} nicht abrufbar (Registry nicht erreichbar oder Version noch nicht gebaut) — baue lokal."
        fi
    fi
    docker build -t ai-employee-agent:latest ./agent
    docker compose build
}

# abbilder_aufraeumen <version...> — eigene Abbilder älterer Versionen löschen.
#
# Jedes Update holt die Abbilder der neuen Version (das Agenten-Abbild allein
# hat über 6 GB) und ließ die alten liegen. Auf einer Anlage mit vielen Updates
# lagen so rund 20 Versionen je Abbild — über 100 GB, bis die Platte voll war
# und die Sicherung vor dem nächsten Update scheiterte (29.10.).
#
# Behalten werden die übergebenen Versionen (die neue und die vorige für den
# Rücksprung) und alles, was ein Container noch benutzt: ``docker image rm``
# OHNE ``-f`` lehnt solche Abbilder ab. Dazu Build-Cache älter als drei Tage
# (Agenten-Apps, lokale Bauten). Daten (Volumes) werden nie angefasst.
#   AI_EMPLOYEE_ABBILDER_AUFRAEUMEN=0   abschalten
#
# Zusammenspiel mit dem Orchestrator, der das Agenten-Abbild selbst holt (#889):
#  - Angefasst werden NUR Registry-Namen (<registry>/ai-employee-<dienst>:<tag>).
#    Der lokale Name ai-employee-agent:latest, auf den AGENT_IMAGE zeigt und mit
#    dem neue Agenten starten, bleibt immer stehen. ``docker image rm`` auf einen
#    Registry-Namen nimmt bei einem Abbild mit mehreren Namen nur diesen einen
#    weg — das Abbild hinter ai-employee-agent:latest bleibt also auch dann,
#    wenn es aus einer älteren Version stammt. Der Orchestrator findet es und
#    holt nichts nach.
#  - Der Orchestrator holt nur <registry>/ai-employee-agent:<VERSION> — genau die
#    neue Version, die hier behalten wird. Ein laufendes Holen verliert also
#    weder seinen Namen noch wird es vom ``image prune`` erfasst.
#  - Eine Version mit verworfener Signatur (AI_EMPLOYEE_ABBILD_ABGELEHNT) wird
#    NICHT behalten, auch wenn sie die neue ist: hätte ``abbilder_verwerfen``
#    einen ihrer Registry-Namen nicht entfernen können, räumt dieser Lauf ihn
#    weg. Der lokale Ersatzbau unter ai-employee-agent:latest bleibt (s. o.).
abbilder_aufraeumen() {
    local registry dienst tag behalten abgelehnt geloescht=0
    [ "$(abbilder_env AI_EMPLOYEE_ABBILDER_AUFRAEUMEN)" = "0" ] && return 0
    registry="$(abbilder_env AI_EMPLOYEE_REGISTRY)"
    registry="${registry:-ghcr.io/greeves89}"
    abgelehnt="$(abbilder_env AI_EMPLOYEE_ABBILD_ABGELEHNT)"
    behalten=" $* "
    for dienst in $ABBILD_DIENSTE agent; do
        for tag in $(docker images "${registry}/ai-employee-${dienst}" --format '{{.Tag}}' 2>/dev/null); do
            [ "$tag" = "<none>" ] && continue
            if [ -z "$abgelehnt" ] || [ "$tag" != "$abgelehnt" ]; then
                case "$behalten" in *" ${tag} "*) continue ;; esac
            fi
            if docker image rm "${registry}/ai-employee-${dienst}:${tag}" >/dev/null 2>&1; then
                geloescht=$((geloescht + 1))
            fi
        done
    done
    docker image prune -f >/dev/null 2>&1 || true
    docker builder prune -f --filter until=72h >/dev/null 2>&1 || true
    echo "Alte Abbilder entfernt: ${geloescht} (behalten:${behalten}und alles, was ein Container nutzt)"
}
