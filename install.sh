#!/bin/bash
set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

REPO="sinas-platform/sinas"
INSTALL_DIR="${SINAS_DIR:-/opt/sinas}"
# Absolute from here on: the installer changes into it later, and reads its
# .env both before and after.
case "$INSTALL_DIR" in /*) ;; *) INSTALL_DIR="$PWD/${INSTALL_DIR#./}" ;; esac

# What to install. Plain runs install main's files with :latest images.
#   SINAS_VERSION=0.5.0-rc.1   a release or release candidate: that tag's files
#                              and images (IMAGE_TAG is written to .env)
#   SINAS_BRANCH=dev           a branch's files; images :dev for dev, :latest for
#                              main, otherwise set SINAS_IMAGE_TAG as well
#   SINAS_IMAGE_TAG=...        the image tag to run, overriding the above
# The choice is saved in .env (SINAS_SOURCE, IMAGE_TAG): a later run without
# any of these reuses it, so an update keeps files and images together.
#
# Which profile (a new install asks; the default is full):
#   SINAS_PROFILE=full         the full profile: Caddy (TLS), ClickHouse request
#                              logs, separate worker containers
#   SINAS_PROFILE=lite         Sinas lite: postgres, redis and one sinas container
#                              serving the API and console over plain HTTP on
#                              SINAS_PORT (default 8000); bring your own TLS proxy
#   SINAS_CODE_EXECUTION=y     lite: enable code execution, which mounts the host
#                              Docker socket (default n; n on a re-run turns it off)
# The profile is saved in .env (SINAS_PROFILE); a re-run keeps it and the
# installer does not switch an install to the other profile.
# Non-interactive installs (CI, agents): SINAS_NONINTERACTIVE=1 plus the
# answers as SINAS_* variables; see https://docs.sinas.co/getting-started/deployment
die() {
    echo -e "${RED}$*${NC}" >&2
    exit 1
}

# A value from an existing install's .env (empty if none).
saved() {
    [ -f "${INSTALL_DIR}/.env" ] || return 0
    grep "^$1=" "${INSTALL_DIR}/.env" | tail -n 1 | cut -d= -f2-
}

SOURCE_EXPLICIT=false
SOURCE_REUSED=""
if [ -n "$SINAS_VERSION" ] || [ -n "$SINAS_BRANCH" ] || [ -n "$SINAS_IMAGE_TAG" ]; then
    SOURCE_EXPLICIT=true
else
    SAVED_SOURCE=$(saved SINAS_SOURCE)
    case "$SAVED_SOURCE" in
        version:?*) SINAS_VERSION="${SAVED_SOURCE#version:}" ;;
        branch:?*) SINAS_BRANCH="${SAVED_SOURCE#branch:}" ;;
    esac
    if [ -n "$SAVED_SOURCE" ]; then
        SINAS_IMAGE_TAG=$(saved IMAGE_TAG)
        SOURCE_REUSED="$SAVED_SOURCE"
    fi
fi

SINAS_VERSION="${SINAS_VERSION#v}"
if [ -n "$SINAS_VERSION" ] && [ -n "$SINAS_BRANCH" ]; then
    die "Set SINAS_VERSION or SINAS_BRANCH, not both"
fi
if [ -n "$SINAS_VERSION" ]; then
    REF="$SINAS_VERSION"
    SOURCE="version:${REF}"
    IMAGE_TAG="${SINAS_IMAGE_TAG:-$SINAS_VERSION}"
else
    REF="${SINAS_BRANCH:-main}"
    case "$REF" in
        main) IMAGE_TAG="${SINAS_IMAGE_TAG:-}" ;;
        dev) IMAGE_TAG="${SINAS_IMAGE_TAG:-dev}" ;;
        *)
            IMAGE_TAG="${SINAS_IMAGE_TAG:-}"
            [ -n "$IMAGE_TAG" ] || die "SINAS_BRANCH=${REF} has no images of its own: set SINAS_IMAGE_TAG too (or use SINAS_VERSION for a release)"
            ;;
    esac
    SOURCE="branch:${REF}"
fi
RAW_URL="https://raw.githubusercontent.com/${REPO}/${REF}"

# The settings that select this source, to repeat in an update command.
if [ -n "$SINAS_VERSION" ]; then
    SOURCE_ENV="SINAS_VERSION=${REF}"
    [ "$IMAGE_TAG" = "$REF" ] || SOURCE_ENV="${SOURCE_ENV} SINAS_IMAGE_TAG=${IMAGE_TAG}"
else
    SOURCE_ENV="SINAS_BRANCH=${REF}"
    if [ -n "$IMAGE_TAG" ] && ! { [ "$REF" = dev ] && [ "$IMAGE_TAG" = dev ]; }; then
        SOURCE_ENV="${SOURCE_ENV} SINAS_IMAGE_TAG=${IMAGE_TAG}"
    fi
fi

NONINTERACTIVE=false
case "${SINAS_NONINTERACTIVE:-}" in
    1|true|TRUE|True|yes|YES|y|Y) NONINTERACTIVE=true ;;
esac

case "${SINAS_PROFILE:-}" in
    ""|full|lite) ;;
    *) die "SINAS_PROFILE must be full or lite (got '${SINAS_PROFILE}')" ;;
esac
case "${SINAS_CODE_EXECUTION:-}" in
    "") ;;
    1|true|TRUE|True|yes|YES|y|Y) SINAS_CODE_EXECUTION=y ;;
    0|false|FALSE|False|no|NO|n|N) SINAS_CODE_EXECUTION=n ;;
    *) die "SINAS_CODE_EXECUTION must be y or n (got '${SINAS_CODE_EXECUTION}')" ;;
esac

echo -e "${BLUE}"
echo "╔════════════════════════════════════════╗"
echo "║     SINAS Core Platform Installer      ║"
echo "╚════════════════════════════════════════╝"
echo -e "${NC}"
echo -e "${BLUE}Installing: ${REF} (images: ${IMAGE_TAG:-latest})${NC}"
if [ -n "$SOURCE_REUSED" ]; then
    echo -e "${YELLOW}  As this install was set up (SINAS_SOURCE=${SOURCE_REUSED} in .env). Set SINAS_VERSION or SINAS_BRANCH to change it.${NC}"
fi

# Detect OS
OS_TYPE="unknown"
if [[ "$OSTYPE" == "darwin"* ]]; then
    OS_TYPE="macos"
    echo -e "${YELLOW}Detected macOS${NC}"
elif [[ "$OSTYPE" == "linux-gnu"* ]]; then
    OS_TYPE="linux"
fi

# Check if running as root (Linux only)
if [[ "$OS_TYPE" == "linux" ]] && [[ $EUID -ne 0 ]]; then
   echo -e "${RED}This script must be run as root on Linux (use sudo)${NC}"
   exit 1
fi

# Check prerequisites
echo -e "${YELLOW}Checking prerequisites...${NC}"

# Check Docker
if ! command -v docker &> /dev/null; then
    if [[ "$OS_TYPE" == "macos" ]]; then
        echo -e "${RED}Docker not found. Please install Docker Desktop for Mac:${NC}"
        echo -e "${YELLOW}https://www.docker.com/products/docker-desktop/${NC}"
        exit 1
    else
        echo -e "${YELLOW}Docker not found. Installing...${NC}"
        curl -fsSL https://get.docker.com | sh
        systemctl start docker
        systemctl enable docker
        sleep 3
        echo -e "${GREEN}✓ Docker installed and started${NC}"
    fi
else
    echo -e "${GREEN}✓ Docker found${NC}"
    if [[ "$OS_TYPE" == "linux" ]]; then
        if ! systemctl is-active --quiet docker; then
            echo -e "${YELLOW}Starting Docker service...${NC}"
            systemctl start docker
            systemctl enable docker
            echo -e "${GREEN}✓ Docker service started${NC}"
        fi
    fi
fi

# Check Docker Compose
if ! docker compose version &> /dev/null; then
    if [[ "$OS_TYPE" == "macos" ]]; then
        echo -e "${RED}Docker Compose not found. Please ensure Docker Desktop is installed.${NC}"
        exit 1
    else
        echo -e "${YELLOW}Docker Compose not found. Installing...${NC}"
        apt-get update && apt-get install -y docker-compose-plugin
        echo -e "${GREEN}✓ Docker Compose installed${NC}"
    fi
else
    echo -e "${GREEN}✓ Docker Compose found${NC}"
fi

# Create install directory
echo ""
echo -e "${YELLOW}Setting up in ${INSTALL_DIR}...${NC}"
mkdir -p "${INSTALL_DIR}"
cd "${INSTALL_DIR}"

# Reopen stdin from /dev/tty so interactive prompts work when piped (curl | bash)
if [ "$NONINTERACTIVE" = false ]; then
    exec < /dev/tty || die "Cannot open terminal for interactive input. Run the script directly instead of piping, or set SINAS_NONINTERACTIVE=1 (see https://docs.sinas.co/getting-started/deployment)."
fi

# Profile. An existing install (it has a .env) keeps its own: SINAS_PROFILE
# in .env, or for one set up before that existed or by hand, lite when it has
# the lite compose file and no full one.
EXISTING_PROFILE=""
if [ -f .env ]; then
    EXISTING_PROFILE=$(saved SINAS_PROFILE)
    if [ -z "$EXISTING_PROFILE" ]; then
        if [ -f docker-compose.lite.yml ] && [ ! -f docker-compose.yml ]; then
            EXISTING_PROFILE=lite
        else
            EXISTING_PROFILE=full
        fi
    fi
fi
if [ -n "$EXISTING_PROFILE" ]; then
    if [ -n "$SINAS_PROFILE" ] && [ "$SINAS_PROFILE" != "$EXISTING_PROFILE" ]; then
        die "${INSTALL_DIR} has a ${EXISTING_PROFILE} install; the installer does not switch it to ${SINAS_PROFILE}. See https://docs.sinas.co/getting-started/lite#moving-to-the-full-profile"
    fi
    PROFILE="$EXISTING_PROFILE"
elif [ -n "$SINAS_PROFILE" ]; then
    PROFILE="$SINAS_PROFILE"
elif [ "$NONINTERACTIVE" = true ]; then
    PROFILE=full
else
    echo ""
    echo -e "${YELLOW}Profile:${NC}"
    echo "  1) Full — Caddy with automatic TLS for your domain, ClickHouse request logs,"
    echo "     separate worker containers. For production."
    echo "  2) Lite — postgres, redis and one sinas container serving the API and console"
    echo "     over plain HTTP; no TLS, request logs or code execution unless you add"
    echo "     them. For evaluation, demos and small single-host installs."
    echo ""
    read -p "Choose [1-2, default 1]: " PROFILE_CHOICE
    case "${PROFILE_CHOICE:-1}" in
        1) PROFILE=full ;;
        2) PROFILE=lite ;;
        *)
            echo -e "${RED}Invalid choice, defaulting to full${NC}"
            PROFILE=full
            ;;
    esac
fi
echo -e "${GREEN}✓ Profile: ${PROFILE}${NC}"

# Lite: code execution is the additive docker-compose.lite.exec.yml. Plain
# `docker compose` commands in the install directory pick up the files from
# COMPOSE_FILE in .env.
CODE_EXECUTION=n
COMPOSE_FILES=""
if [ "$PROFILE" = lite ]; then
    SAVED_COMPOSE_FILE=$(saved COMPOSE_FILE)
    if [ -n "$SINAS_CODE_EXECUTION" ]; then
        CODE_EXECUTION="$SINAS_CODE_EXECUTION"
    elif [ -n "$EXISTING_PROFILE" ]; then
        # Keep what the install runs now
        if [ -n "$SAVED_COMPOSE_FILE" ]; then
            case ":${SAVED_COMPOSE_FILE}:" in *:docker-compose.lite.exec.yml:*) CODE_EXECUTION=y ;; esac
        elif [ -f docker-compose.lite.exec.yml ]; then
            CODE_EXECUTION=y
        fi
    elif [ "$NONINTERACTIVE" = false ]; then
        echo ""
        echo "  Code execution runs functions and agent code in sandbox containers. In the"
        echo "  lite profile it mounts this host's Docker socket into the sinas container,"
        echo "  which gives that container root-equivalent control of the host."
        read -p "Enable code execution? [y/N]: " CODE_EXECUTION_INPUT
        if [[ "${CODE_EXECUTION_INPUT:-N}" =~ ^[Yy]$ ]]; then
            CODE_EXECUTION=y
        fi
    fi
    # The base file first, then any of the install's own files, then the
    # exec override when enabled.
    COMPOSE_FILES="docker-compose.lite.yml"
    IFS=: read -r -a SAVED_FILES <<< "$SAVED_COMPOSE_FILE"
    for f in "${SAVED_FILES[@]}"; do
        case "$f" in
            ""|docker-compose.lite.yml|docker-compose.lite.exec.yml) ;;
            *) COMPOSE_FILES="${COMPOSE_FILES}:${f}" ;;
        esac
    done
    if [ "$CODE_EXECUTION" = y ]; then
        COMPOSE_FILES="${COMPOSE_FILES}:docker-compose.lite.exec.yml"
        echo -e "${GREEN}✓ Code execution: on (host Docker socket mounted)${NC}"
    else
        echo -e "${GREEN}✓ Code execution: off${NC}"
    fi
fi

# Download required files from GitHub
echo -e "${YELLOW}Downloading configuration files...${NC}"

if [ "$PROFILE" = lite ]; then
    curl -fsSL "${RAW_URL}/docker-compose.lite.yml" -o docker-compose.lite.yml \
        || die "Could not download docker-compose.lite.yml for '${REF}'. Is it a published version (see https://github.com/${REPO}/releases) or a branch? The lite profile needs 0.4.0 or later."
    if [ "$CODE_EXECUTION" = y ]; then
        curl -fsSL "${RAW_URL}/docker-compose.lite.exec.yml" -o docker-compose.lite.exec.yml
    else
        rm -f docker-compose.lite.exec.yml
    fi
else
    curl -fsSL "${RAW_URL}/docker-compose.yml" -o docker-compose.yml \
        || die "Could not download docker-compose.yml for '${REF}'. Is it a published version (see https://github.com/${REPO}/releases) or a branch?"
    curl -fsSL "${RAW_URL}/Caddyfile" -o Caddyfile

    mkdir -p backend/clickhouse
    curl -fsSL "${RAW_URL}/backend/init-clickhouse.sql" -o backend/init-clickhouse.sql
    curl -fsSL "${RAW_URL}/backend/init-postgres.sh" -o backend/init-postgres.sh
    chmod +x backend/init-postgres.sh
    curl -fsSL "${RAW_URL}/backend/clickhouse/entrypoint-wrapper.sh" -o backend/clickhouse/entrypoint-wrapper.sh
    curl -fsSL "${RAW_URL}/backend/clickhouse/system-logs.xml" -o backend/clickhouse/system-logs.xml
    curl -fsSL "${RAW_URL}/backend/clickhouse/resource-limits.xml" -o backend/clickhouse/resource-limits.xml
    curl -fsSL "${RAW_URL}/backend/clickhouse/network.xml" -o backend/clickhouse/network.xml
    curl -fsSL "${RAW_URL}/backend/clickhouse/user-limits.xml" -o backend/clickhouse/user-limits.xml
    chmod +x backend/clickhouse/entrypoint-wrapper.sh
fi

echo -e "${GREEN}✓ Files downloaded${NC}"

# Non-interactive answers, from the environment (SINAS_NONINTERACTIVE=1)
noninteractive_answers() {
    DOMAIN="${SINAS_DOMAIN:-}"
    ACME_EMAIL="${SINAS_ACME_EMAIL:-}"
    SUPERADMIN_EMAIL="${SINAS_SUPERADMIN_EMAIL:-$ACME_EMAIL}"
    if [ "$PROFILE" = lite ]; then
        # No Caddy, so no certificates; no SMTP needed by default
        AUTH_MODE="${SINAS_AUTH_MODE:-password}"
        APP_PORT="${SINAS_PORT:-8000}"
        [ -n "$SUPERADMIN_EMAIL" ] || die "SINAS_NONINTERACTIVE: set SINAS_SUPERADMIN_EMAIL"
        valid_port "$APP_PORT" || die "SINAS_PORT must be a port number (got '${APP_PORT}')"
    else
        AUTH_MODE="${SINAS_AUTH_MODE:-otp}"
    fi
    SUPERADMIN_PASSWORD="${SINAS_SUPERADMIN_PASSWORD:-}"
    SMTP_HOST="${SINAS_SMTP_HOST:-}"
    SMTP_PORT="${SINAS_SMTP_PORT:-587}"
    SMTP_USER="${SINAS_SMTP_USER:-}"
    SMTP_PASSWORD="${SINAS_SMTP_PASSWORD:-}"
    SMTP_DOMAIN="${SINAS_SMTP_DOMAIN:-}"
    if [ "$PROFILE" = full ]; then
        [ -n "$DOMAIN" ] || die "SINAS_NONINTERACTIVE: set SINAS_DOMAIN"
        [ -n "$ACME_EMAIL" ] || die "SINAS_NONINTERACTIVE: set SINAS_ACME_EMAIL (email for SSL certificates)"
    fi
    case "$AUTH_MODE" in
        otp|password|password+otp) ;;
        *) die "SINAS_AUTH_MODE must be otp, password or password+otp (got '${AUTH_MODE}')" ;;
    esac
    if [[ "$AUTH_MODE" == *"password"* ]]; then
        if [ -n "$SUPERADMIN_PASSWORD" ] && [ ${#SUPERADMIN_PASSWORD} -lt 8 ]; then
            die "SINAS_SUPERADMIN_PASSWORD must be at least 8 characters (or empty for a setup link)"
        fi
    else
        SUPERADMIN_PASSWORD=""
    fi
    if [[ "$AUTH_MODE" == *"otp"* ]]; then
        if [ -z "$SMTP_HOST" ] || [ -z "$SMTP_USER" ] || [ -z "$SMTP_PASSWORD" ]; then
            die "SINAS_AUTH_MODE=${AUTH_MODE} sends login codes by email: set SINAS_SMTP_HOST, SINAS_SMTP_USER and SINAS_SMTP_PASSWORD (or use SINAS_AUTH_MODE=password)"
        fi
    fi
    if [ "$PROFILE" = lite ]; then
        echo -e "${GREEN}✓ Port: ${APP_PORT}, domain: ${DOMAIN:-(none)}, superadmin: ${SUPERADMIN_EMAIL}, auth mode: ${AUTH_MODE}${NC}"
    else
        echo -e "${GREEN}✓ Domain: ${DOMAIN}, superadmin: ${SUPERADMIN_EMAIL}, auth mode: ${AUTH_MODE}${NC}"
    fi
}

valid_port() {
    case "$1" in
        ""|*[!0-9]*) return 1 ;;
    esac
    [ "$1" -ge 1 ] && [ "$1" -le 65535 ]
}

# Set KEY=VALUE in .env, replacing an earlier value; .env stays private.
set_env() {
    (umask 077; { grep -v "^$1=" .env || true; echo "$1=$2"; } > .env.new)
    cat .env.new > .env && rm -f .env.new
}

echo ""
echo -e "${BLUE}═══════════════════════════════════════${NC}"
echo -e "${BLUE}  Configuration${NC}"
echo -e "${BLUE}═══════════════════════════════════════${NC}"
echo ""

FRESH_INSTALL=false
SERVICES_STARTED=false
if [ -f .env ]; then
    echo -e "${GREEN}✓ Existing .env file found — using current configuration${NC}"
    echo -e "${YELLOW}  (Delete .env and re-run to reconfigure)${NC}"
    # An update. An explicit choice is written to .env (an empty IMAGE_TAG
    # means latest); without one, .env's own choice was reused above.
    ENV_IMAGE_TAG=$(saved IMAGE_TAG)
    if [ "$SOURCE_EXPLICIT" = true ]; then
        if [ "$(saved SINAS_SOURCE)" != "$SOURCE" ] || [ "$ENV_IMAGE_TAG" != "$IMAGE_TAG" ]; then
            set_env SINAS_SOURCE "$SOURCE"
            set_env IMAGE_TAG "$IMAGE_TAG"
            echo -e "${GREEN}✓ SINAS_SOURCE=${SOURCE}, IMAGE_TAG=${IMAGE_TAG:-(latest)} written to .env${NC}"
        fi
    elif [ -z "$SOURCE_REUSED" ] && [ -n "$ENV_IMAGE_TAG" ]; then
        echo -e "${YELLOW}⚠ .env pins IMAGE_TAG=${ENV_IMAGE_TAG}, but these files are ${REF}'s."
        echo -e "  To install a version's files and images together, set SINAS_VERSION (e.g. SINAS_VERSION=${ENV_IMAGE_TAG}).${NC}"
    fi
    if [ "$PROFILE" = lite ]; then
        [ "$(saved SINAS_PROFILE)" = lite ] || set_env SINAS_PROFILE lite
        if [ "$(saved COMPOSE_FILE)" != "$COMPOSE_FILES" ]; then
            set_env COMPOSE_FILE "$COMPOSE_FILES"
            echo -e "${GREEN}✓ COMPOSE_FILE=${COMPOSE_FILES} written to .env${NC}"
        fi
    fi
else
    FRESH_INSTALL=true
    # Generate secure keys
    echo -e "${YELLOW}Generating secure keys...${NC}"
    SECRET_KEY=$(openssl rand -hex 32)
    ENCRYPTION_KEY=$(python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())" 2>/dev/null || echo "")

    if [ -z "$ENCRYPTION_KEY" ]; then
        # A Fernet key is 32 random bytes, urlsafe-base64 encoded.
        ENCRYPTION_KEY=$(openssl rand -base64 32 | tr '+/' '-_')
    fi

    echo -e "${GREEN}✓ Keys generated${NC}"
    echo ""

    # Database password
    DB_PASSWORD=$(openssl rand -base64 32 | tr -d "=+/" | cut -c1-32)
    echo -e "${GREEN}✓ Database password auto-generated${NC}"

    if [ "$NONINTERACTIVE" = true ]; then
        noninteractive_answers
    else
        echo -e "${BLUE}Please provide the following information:${NC}"
        echo ""

        if [ "$PROFILE" = lite ]; then
            # Superadmin email
            read -p "Superadmin email: " SUPERADMIN_EMAIL
            while [ -z "$SUPERADMIN_EMAIL" ]; do
                echo -e "${RED}Email is required${NC}"
                read -p "Superadmin email: " SUPERADMIN_EMAIL
            done

            # Port
            read -p "Port to serve Sinas on (plain HTTP) [8000]: " APP_PORT
            APP_PORT=${APP_PORT:-8000}
            while ! valid_port "$APP_PORT"; do
                echo -e "${RED}Enter a port number${NC}"
                read -p "Port [8000]: " APP_PORT
                APP_PORT=${APP_PORT:-8000}
            done

            # Public hostname (links in emails and setup links)
            echo ""
            echo "  If a TLS proxy will serve Sinas at https://<hostname>, enter that hostname:"
            echo "  links Sinas sends (setup links, emails) then use it. Leave empty to use"
            echo "  http://localhost:${APP_PORT}."
            read -p "Public hostname [empty = localhost]: " DOMAIN
            DEFAULT_AUTH_CHOICE=2
        else
            # Domain
            read -p "Domain name (e.g., sinas.example.com): " DOMAIN
            while [ -z "$DOMAIN" ]; do
                echo -e "${RED}Domain is required${NC}"
                read -p "Domain name: " DOMAIN
            done

            # Email for SSL
            read -p "Email for SSL certificates: " ACME_EMAIL
            while [ -z "$ACME_EMAIL" ]; do
                echo -e "${RED}Email is required${NC}"
                read -p "Email for SSL certificates: " ACME_EMAIL
            done

            # Superadmin email
            read -p "Superadmin email [default: $ACME_EMAIL]: " SUPERADMIN_EMAIL
            SUPERADMIN_EMAIL=${SUPERADMIN_EMAIL:-$ACME_EMAIL}
            DEFAULT_AUTH_CHOICE=1
        fi

        echo ""
        echo -e "${YELLOW}Authentication mode:${NC}"
        echo "  1) Email OTP            — emails a 6-digit code (requires SMTP)"
        echo "  2) Password             — password only (works airgapped, no SMTP)"
        echo "  3) Password + Email OTP — both required (most secure, requires SMTP)"
        echo ""
        read -p "Choose [1-3, default ${DEFAULT_AUTH_CHOICE}]: " AUTH_CHOICE
        AUTH_CHOICE=${AUTH_CHOICE:-$DEFAULT_AUTH_CHOICE}

        case "$AUTH_CHOICE" in
            1) AUTH_MODE="otp" ;;
            2) AUTH_MODE="password" ;;
            3) AUTH_MODE="password+otp" ;;
            *)
                if [ "$DEFAULT_AUTH_CHOICE" = 2 ]; then
                    echo -e "${RED}Invalid choice, defaulting to password${NC}"
                    AUTH_MODE="password"
                else
                    echo -e "${RED}Invalid choice, defaulting to OTP${NC}"
                    AUTH_MODE="otp"
                fi
                ;;
        esac
        echo -e "${GREEN}✓ Auth mode: $AUTH_MODE${NC}"

        # Password prompt (when auth mode includes password). Empty (recommended):
        # no password in .env; the end of the install prints a one-time setup link
        # with which the superadmin chooses their own.
        SUPERADMIN_PASSWORD=""
        if [[ "$AUTH_MODE" == *"password"* ]]; then
            echo ""
            echo "  Leave empty (recommended) to get a one-time setup link at the end of the"
            echo "  install. A password set here is pinned: changes made in the console"
            echo "  revert to it on every restart."
            read -s -p "Superadmin password [empty = setup link]: " SUPERADMIN_PASSWORD
            echo ""
            if [ -z "$SUPERADMIN_PASSWORD" ]; then
                echo -e "${GREEN}✓ Setup link will be printed at the end of the install${NC}"
            else
                while [ ${#SUPERADMIN_PASSWORD} -lt 8 ]; do
                    echo -e "${RED}Password must be at least 8 characters${NC}"
                    read -s -p "Superadmin password: " SUPERADMIN_PASSWORD
                    echo ""
                done
                echo -e "${GREEN}✓ Password set${NC}"
            fi
        fi

        # SMTP prompts (when auth mode includes OTP)
        SMTP_HOST=""
        SMTP_PORT=587
        SMTP_USER=""
        SMTP_PASSWORD=""
        SMTP_DOMAIN=""
        if [[ "$AUTH_MODE" == *"otp"* ]]; then
            echo ""
            echo -e "${YELLOW}SMTP Configuration (required for OTP emails):${NC}"
            echo "  SendGrid: smtp.sendgrid.net:587, user: apikey"
            echo "  Mailgun:  smtp.mailgun.org:587"
            echo "  AWS SES:  email-smtp.<region>.amazonaws.com:587"
            echo ""

            read -p "SMTP Host: " SMTP_HOST
            read -p "SMTP Port [587]: " SMTP_PORT_INPUT
            SMTP_PORT=${SMTP_PORT_INPUT:-587}
            read -p "SMTP Username: " SMTP_USER
            read -s -p "SMTP Password/API Key: " SMTP_PASSWORD
            echo ""

            while [ -z "$SMTP_HOST" ] || [ -z "$SMTP_USER" ] || [ -z "$SMTP_PASSWORD" ]; do
                echo -e "${RED}SMTP configuration is required for OTP login${NC}"
                read -p "SMTP Host: " SMTP_HOST
                read -p "SMTP Username: " SMTP_USER
                read -s -p "SMTP Password: " SMTP_PASSWORD
                echo ""
            done

            read -p "SMTP From domain (e.g., example.com): " SMTP_DOMAIN
        fi
    fi

    # Private before the secrets go in
    (umask 077; : > .env)
    if [ "$PROFILE" = lite ]; then
        cat > .env << EOF
# Sinas lite: postgres, redis and one sinas container (docker-compose.lite.yml).
# Plain docker compose commands in this directory use COMPOSE_FILE.
SINAS_PROFILE=lite
COMPOSE_FILE=$COMPOSE_FILES

# Security
SECRET_KEY=$SECRET_KEY
ENCRYPTION_KEY=$ENCRYPTION_KEY

# Database
DATABASE_PASSWORD=$DB_PASSWORD
DATABASE_USER=postgres
DATABASE_NAME=sinas

# Authentication
AUTH_MODE=$AUTH_MODE

# SMTP
SMTP_HOST=$SMTP_HOST
SMTP_PORT=$SMTP_PORT
SMTP_USER=$SMTP_USER
SMTP_PASSWORD=$SMTP_PASSWORD
SMTP_DOMAIN=$SMTP_DOMAIN

# Admin
SUPERADMIN_EMAIL=$SUPERADMIN_EMAIL
SUPERADMIN_PASSWORD=$SUPERADMIN_PASSWORD

# Where Sinas is served: plain HTTP on BACKEND_PORT. DOMAIN is the hostname a
# TLS proxy in front serves it at (links then use https://DOMAIN); empty means
# http://localhost:BACKEND_PORT. Set FORWARDED_ALLOW_IPS to that proxy's
# address as the sinas container sees it, for client IPs in rate limits.
BACKEND_PORT=$APP_PORT
DOMAIN=$DOMAIN

# What this install runs: where its files come from, and its images
# (empty = latest). A plain re-run of the installer reuses both.
SINAS_SOURCE=$SOURCE
IMAGE_TAG=$IMAGE_TAG

# Function Execution
FUNCTION_TIMEOUT=300
MAX_FUNCTION_MEMORY=512
ALLOW_PACKAGE_INSTALLATION=true

# Request logging is off: this profile has no ClickHouse. Set CLICKHOUSE_HOST
# (and CLICKHOUSE_PORT, _USER, _PASSWORD, _DATABASE) to a ClickHouse server to
# turn it on.
EOF
    else
        cat > .env << EOF
# Security
SECRET_KEY=$SECRET_KEY
ENCRYPTION_KEY=$ENCRYPTION_KEY

# Database
DATABASE_PASSWORD=$DB_PASSWORD
DATABASE_USER=postgres
DATABASE_HOST=postgres
DATABASE_PORT=5432
DATABASE_NAME=sinas

# Redis
REDIS_URL=redis://redis:6379/0

# Authentication
AUTH_MODE=$AUTH_MODE

# SMTP
SMTP_HOST=$SMTP_HOST
SMTP_PORT=$SMTP_PORT
SMTP_USER=$SMTP_USER
SMTP_PASSWORD=$SMTP_PASSWORD
SMTP_DOMAIN=$SMTP_DOMAIN

# Admin
SUPERADMIN_EMAIL=$SUPERADMIN_EMAIL
SUPERADMIN_PASSWORD=$SUPERADMIN_PASSWORD

# Domain & SSL
DOMAIN=$DOMAIN
ACME_EMAIL=$ACME_EMAIL

# What this install runs: where its files come from, and its images
# (empty = latest). A plain re-run of the installer reuses both.
SINAS_SOURCE=$SOURCE
IMAGE_TAG=$IMAGE_TAG

# Function Execution
FUNCTION_TIMEOUT=300
MAX_FUNCTION_MEMORY=512
ALLOW_PACKAGE_INSTALLATION=true

# ClickHouse
CLICKHOUSE_HOST=clickhouse
CLICKHOUSE_PORT=8123
CLICKHOUSE_USER=default
CLICKHOUSE_PASSWORD=
CLICKHOUSE_DATABASE=sinas
EOF
    fi

    chmod 600 .env
    echo -e "${GREEN}✓ .env file created${NC}"
fi

# Load DOMAIN from .env for the completion message
DOMAIN=$(grep "^DOMAIN=" .env 2>/dev/null | cut -d= -f2)
SUPERADMIN_EMAIL=$(grep "^SUPERADMIN_EMAIL=" .env 2>/dev/null | cut -d= -f2)
AUTH_MODE=$(grep "^AUTH_MODE=" .env 2>/dev/null | cut -d= -f2)
AUTH_MODE=${AUTH_MODE:-otp}

# The service that runs the API (and, in lite, everything else)
if [ "$PROFILE" = lite ]; then
    APP_SERVICE=sinas
    APP_PORT=$(saved BACKEND_PORT)
    APP_PORT=${APP_PORT:-8000}
    if [ -n "$DOMAIN" ] && [ "$DOMAIN" != localhost ] && [ "$DOMAIN" != 127.0.0.1 ]; then
        PUBLIC_URL="https://${DOMAIN}"
    else
        PUBLIC_URL="http://localhost:${APP_PORT}"
    fi
else
    APP_SERVICE=backend
fi

# Firewall (Linux only)
if [[ "$OS_TYPE" == "linux" ]]; then
    echo ""
    if [ "$NONINTERACTIVE" = true ]; then
        SETUP_FIREWALL=${SINAS_CONFIGURE_FIREWALL:-n}
    else
        read -p "Configure firewall (UFW)? [Y/n]: " SETUP_FIREWALL
        SETUP_FIREWALL=${SETUP_FIREWALL:-Y}
    fi

    if [[ "$SETUP_FIREWALL" =~ ^[Yy]$ ]]; then
        echo -e "${YELLOW}Configuring firewall...${NC}"
        if ! command -v ufw &> /dev/null; then
            apt-get install -y ufw
        fi
        ufw --force reset
        ufw default deny incoming
        ufw default allow outgoing
        ufw allow 22/tcp
        ufw allow 80/tcp
        ufw allow 443/tcp
        ufw --force enable
        echo -e "${GREEN}✓ Firewall configured${NC}"
    fi
fi

# Start services
echo ""
if [ "$NONINTERACTIVE" = true ]; then
    START_SERVICES=${SINAS_START:-y}
else
    read -p "Start SINAS now? [Y/n]: " START_SERVICES
    START_SERVICES=${START_SERVICES:-Y}
fi

STARTED=false
if [[ "$START_SERVICES" =~ ^[Yy]$ ]]; then
    STARTED=true
    if ! docker info &> /dev/null; then
        echo -e "${RED}Error: Docker daemon is not running${NC}"
        exit 1
    fi

    echo -e "${YELLOW}Pulling images...${NC}"
    docker compose pull || die "Pulling the images (tag: ${IMAGE_TAG:-latest}) failed. If that tag is not published yet, wait for its build or pick another SINAS_VERSION."

    echo -e "${YELLOW}Starting services...${NC}"
    docker compose up -d

    echo ""
    echo -e "${YELLOW}Waiting for services to be ready...${NC}"
    if [ "$PROFILE" = lite ]; then
        # Healthy once migrations ran and the API serves (first start: a minute or two)
        for _ in $(seq 1 60); do
            if curl -fsS -o /dev/null "http://localhost:${APP_PORT}/health" 2>/dev/null; then
                SERVICES_STARTED=true
                break
            fi
            sleep 3
        done
        if [ "$SERVICES_STARTED" = true ]; then
            echo -e "${GREEN}✓ Sinas is up on port ${APP_PORT}${NC}"
        else
            echo -e "${RED}⚠ Sinas did not answer on http://localhost:${APP_PORT}/health. Check: docker compose ps; docker compose logs sinas${NC}"
        fi
    else
        sleep 10

        if docker compose ps --status running --services 2>/dev/null | grep -qx backend; then
            echo -e "${GREEN}✓ Services started successfully${NC}"
            SERVICES_STARTED=true
        else
            echo -e "${RED}⚠ The backend is not running. Check: docker compose ps; docker compose logs backend${NC}"
        fi
    fi
fi

# First admin access: a fresh password-mode install without SUPERADMIN_PASSWORD
# gets a one-time setup link, minted once the backend has created the
# superadmin (it retries while the backend migrates and boots). Printed to
# this terminal only; nothing is written to disk or logs.
SETUP_LINK=""
SETUP_LINK_CMD="docker compose exec ${APP_SERVICE} python -m app.cli setup-link"
# The backend has no setup-link command (versions before 0.5.0 log the link)
SETUP_LINK_UNSUPPORTED=false
WANTS_SETUP_LINK=false
if [ "$FRESH_INSTALL" = true ] && [[ "$AUTH_MODE" == *"password"* ]] && [ -z "$SUPERADMIN_PASSWORD" ]; then
    WANTS_SETUP_LINK=true
fi
if [ "$WANTS_SETUP_LINK" = true ] && [ "$STARTED" = true ]; then
    echo -e "${YELLOW}Creating the superadmin's one-time setup link...${NC}"
    for _ in $(seq 1 36); do
        # 0: the command exists; 3: it does not; anything else: backend not up yet
        CLI_STATUS=0
        docker compose exec -T "$APP_SERVICE" python -c \
            "import importlib.util, sys; sys.exit(0 if importlib.util.find_spec('app.cli') else 3)" \
            >/dev/null 2>&1 || CLI_STATUS=$?
        if [ "$CLI_STATUS" -eq 3 ]; then
            SETUP_LINK_UNSUPPORTED=true
            break
        fi
        if [ "$CLI_STATUS" -eq 0 ]; then
            SETUP_LINK=$(docker compose exec -T "$APP_SERVICE" python -m app.cli setup-link 2>/dev/null | tail -n 1) && [ -n "$SETUP_LINK" ] && break
        fi
        SETUP_LINK=""
        sleep 5
    done
fi

# Done
echo ""
echo -e "${GREEN}"
echo "╔════════════════════════════════════════╗"
echo "║       Installation Complete!           ║"
echo "╚════════════════════════════════════════╝"
echo -e "${NC}"
echo ""
echo -e "${BLUE}Next steps:${NC}"
echo ""

# The superadmin's first sign-in, under the login step
print_setup_link() {
    if [ -n "$SETUP_LINK" ]; then
        echo ""
        echo -e "   ${YELLOW}Open this one-time link to set the superadmin's password and sign in"
        echo -e "   (single use, valid 24h; shown only here):${NC}"
        echo ""
        echo "   ${SETUP_LINK}"
        echo ""
        echo "   Lost or expired? Get a new one (the old one stops working):"
        echo "   cd ${INSTALL_DIR} && ${SETUP_LINK_CMD}"
    elif [ "$SETUP_LINK_UNSUPPORTED" = true ]; then
        echo ""
        echo -e "   ${RED}This backend (images: ${IMAGE_TAG:-latest}) has no setup-link command"
        echo -e "   (python -m app.cli setup-link arrived in 0.5.0). Its one-time setup link is"
        echo -e "   in the ${APP_SERVICE} logs instead:${NC}"
        echo "   cd ${INSTALL_DIR} && docker compose logs ${APP_SERVICE} | grep -A8 'SUPERADMIN SETUP'"
        echo "   Or set SUPERADMIN_PASSWORD in .env and run: docker compose up -d"
    elif [ "$WANTS_SETUP_LINK" = true ] && [ "$STARTED" = true ]; then
        echo ""
        echo -e "   ${RED}No setup link: the backend did not become ready. See why with:${NC}"
        echo "   cd ${INSTALL_DIR} && docker compose ps && docker compose logs --tail 100 ${APP_SERVICE}"
        echo "   curl -s http://localhost:${APP_PORT:-${BACKEND_PORT:-8000}}/health"
        echo "   Once it is healthy, get the link with:"
        echo "   cd ${INSTALL_DIR} && ${SETUP_LINK_CMD}"
    elif [ "$WANTS_SETUP_LINK" = true ]; then
        echo ""
        echo -e "   ${YELLOW}Set the superadmin's password with a one-time setup link. Once the"
        echo -e "   backend is up, run:${NC}"
        echo "   cd ${INSTALL_DIR} && ${SETUP_LINK_CMD}"
    fi
}

if [ "$PROFILE" = lite ]; then
    echo "1. Open the console (plain HTTP on port ${APP_PORT}):"
    echo "   http://localhost:${APP_PORT}/ui/"
    echo "   From another machine: http://<this-host>:${APP_PORT}/ui/"
    echo ""
    echo "2. TLS: Sinas lite serves plain HTTP. Before real use, put a TLS proxy in"
    echo "   front of port ${APP_PORT}, set DOMAIN (and FORWARDED_ALLOW_IPS) in .env, then"
    echo "   docker compose up -d. Links Sinas sends now use: ${PUBLIC_URL}"
    echo "   See https://docs.sinas.co/getting-started/lite#tls-and-the-public-url"
    echo ""
    echo "3. Verify:"
    echo "   curl http://localhost:${APP_PORT}/health"
    echo ""
    echo "4. Login:"
    echo "   Console:    ${PUBLIC_URL}/ui/"
    echo "   Superadmin: ${SUPERADMIN_EMAIL}"
    echo "   Auth mode:  ${AUTH_MODE}"
    if [ "$CODE_EXECUTION" = y ]; then
        echo "   Code execution: on (turn off: re-run the installer with SINAS_CODE_EXECUTION=n)"
    else
        echo "   Code execution: off (turn on: re-run the installer with SINAS_CODE_EXECUTION=y)"
    fi
    print_setup_link
else
    echo "1. Point your DNS:"
    echo "   ${DOMAIN} → $(curl -s ifconfig.me 2>/dev/null || echo '<your-server-ip>')"
    echo ""
    echo "2. SSL is automatic (Caddy + Let's Encrypt)"
    echo ""
    echo "3. Verify:"
    echo "   curl https://${DOMAIN}/health"
    echo ""
    echo "4. Login:"
    echo "   Console:    https://${DOMAIN}:51245/ui/"
    echo "   Superadmin: ${SUPERADMIN_EMAIL}"
    echo "   Auth mode:  ${AUTH_MODE}"
    print_setup_link
fi
echo ""
echo "5. Update (same source: ${SOURCE}, images: ${IMAGE_TAG:-latest}):"
echo "   curl -fsSL ${RAW_URL}/install.sh -o /tmp/sinas-install.sh \\"
echo "     && sudo ${SOURCE_ENV} bash /tmp/sinas-install.sh"
if [ -n "$SINAS_VERSION" ]; then
    echo "   To another version: curl -fsSL https://raw.githubusercontent.com/${REPO}/<version>/install.sh -o /tmp/sinas-install.sh \\"
    echo "     && sudo SINAS_VERSION=<version> bash /tmp/sinas-install.sh"
fi
echo ""
echo "6. Logs:"
echo "   cd ${INSTALL_DIR} && docker compose logs -f"
echo ""

# Say so in the exit status too (CI, agents) when the install did not
# finish: services were to start and did not, or a password-mode
# superadmin got no setup link. SINAS_START=n (configuration only) succeeds.
if [ "$PROFILE" = lite ] && [ "$STARTED" = true ] && [ "$SERVICES_STARTED" != true ]; then
    echo -e "${RED}Sinas is not answering on port ${APP_PORT}. Check: cd ${INSTALL_DIR} && docker compose ps && docker compose logs --tail 100 sinas${NC}" >&2
    exit 1
fi
if [ "$STARTED" = true ] && ! docker compose ps --status running --services 2>/dev/null | grep -qx "$APP_SERVICE"; then
    echo -e "${RED}The ${APP_SERVICE} service is not running. Check: cd ${INSTALL_DIR} && docker compose ps && docker compose logs --tail 100 ${APP_SERVICE}${NC}" >&2
    exit 1
fi
if [ "$WANTS_SETUP_LINK" = true ] && [ "$STARTED" = true ] && [ -z "$SETUP_LINK" ]; then
    echo -e "${RED}Install incomplete: no setup link for the superadmin (see step 4 above).${NC}" >&2
    exit 1
fi
