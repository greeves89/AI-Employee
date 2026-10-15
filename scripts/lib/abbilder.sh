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

abbilder_bereitstellen() {
    local lokal_bauen="${1:-0}" version registry
    version="$(tr -d '[:space:]' < VERSION 2>/dev/null || true)"
    version="${version:-latest}"
    registry="${AI_EMPLOYEE_REGISTRY:-ghcr.io/greeves89}"
    export AI_EMPLOYEE_VERSION="$version"
    # Fuer den lokalen Bau: Version ins Frontend-Bundle (update-banner.tsx).
    export APP_VERSION="$version"
    if [ -f .env ]; then
        if grep -q '^AI_EMPLOYEE_VERSION=' .env; then
            sed -i.bak "s/^AI_EMPLOYEE_VERSION=.*/AI_EMPLOYEE_VERSION=${version}/" .env && rm -f .env.bak
        else
            printf 'AI_EMPLOYEE_VERSION=%s\n' "$version" >> .env
        fi
    fi

    ABBILD_QUELLE="lokal"
    if [ "$lokal_bauen" != "1" ]; then
        echo "Hole fertige Abbilder ${version} aus ${registry} ..."
        # shellcheck disable=SC2086  # bewusst getrennte Liste
        if docker compose pull -q $ABBILD_DIENSTE \
            && docker pull -q "${registry}/ai-employee-agent:${version}" >/dev/null; then
            docker tag "${registry}/ai-employee-agent:${version}" ai-employee-agent:latest
            ABBILD_QUELLE="registry"
            return 0
        fi
        echo "Abbilder ${version} nicht abrufbar (Registry nicht erreichbar oder Version noch nicht gebaut) — baue lokal."
    fi
    docker build -t ai-employee-agent:latest ./agent
    docker compose build
}
