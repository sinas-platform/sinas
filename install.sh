#!/bin/bash
set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

REPO="pulsr-one/SINAS"
BRANCH="main"
RAW_URL="https://raw.githubusercontent.com/${REPO}/${BRANCH}"
INSTALL_DIR="${SINAS_DIR:-/opt/sinas}"

echo -e "${BLUE}"
echo "╔════════════════════════════════════════╗"
echo "║     SINAS Core Platform Installer      ║"
echo "╚════════════════════════════════════════╝"
echo -e "${NC}"

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

# Download required files from GitHub
echo -e "${YELLOW}Downloading configuration files...${NC}"

curl -fsSL "${RAW_URL}/docker-compose.yml" -o docker-compose.yml
curl -fsSL "${RAW_URL}/Caddyfile" -o Caddyfile

mkdir -p backend/clickhouse
curl -fsSL "${RAW_URL}/backend/init-clickhouse.sql" -o backend/init-clickhouse.sql
curl -fsSL "${RAW_URL}/backend/clickhouse/entrypoint-wrapper.sh" -o backend/clickhouse/entrypoint-wrapper.sh
curl -fsSL "${RAW_URL}/backend/clickhouse/system-logs.xml" -o backend/clickhouse/system-logs.xml
curl -fsSL "${RAW_URL}/backend/clickhouse/resource-limits.xml" -o backend/clickhouse/resource-limits.xml
curl -fsSL "${RAW_URL}/backend/clickhouse/network.xml" -o backend/clickhouse/network.xml
curl -fsSL "${RAW_URL}/backend/clickhouse/user-limits.xml" -o backend/clickhouse/user-limits.xml
chmod +x backend/clickhouse/entrypoint-wrapper.sh

echo -e "${GREEN}✓ Files downloaded${NC}"

# Configuration
echo ""
echo -e "${BLUE}═══════════════════════════════════════${NC}"
echo -e "${BLUE}  Configuration${NC}"
echo -e "${BLUE}═══════════════════════════════════════${NC}"
echo ""

if [ -f .env ]; then
    echo -e "${GREEN}✓ Existing .env file found — using current configuration${NC}"
    echo -e "${YELLOW}  (Delete .env and re-run to reconfigure)${NC}"
else
    # Generate secure keys
    echo -e "${YELLOW}Generating secure keys...${NC}"
    SECRET_KEY=$(openssl rand -hex 32)
    ENCRYPTION_KEY=$(python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())" 2>/dev/null || echo "")

    if [ -z "$ENCRYPTION_KEY" ]; then
        echo -e "${YELLOW}Installing cryptography for key generation...${NC}"
        if [[ "$OS_TYPE" == "linux" ]]; then
            apt-get install -y python3-pip 2>/dev/null
        fi
        pip3 install cryptography 2>/dev/null
        ENCRYPTION_KEY=$(python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())")
    fi

    echo -e "${GREEN}✓ Keys generated${NC}"
    echo ""

    echo -e "${BLUE}Please provide the following information:${NC}"
    echo ""

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

    # Database password
    DB_PASSWORD=$(openssl rand -base64 32 | tr -d "=+/" | cut -c1-32)
    echo -e "${GREEN}✓ Database password auto-generated${NC}"

    echo ""
    echo -e "${YELLOW}SMTP Configuration (required for login emails):${NC}"
    echo "  SendGrid: smtp.sendgrid.net:587, user: apikey"
    echo "  Mailgun:  smtp.mailgun.org:587"
    echo "  AWS SES:  email-smtp.<region>.amazonaws.com:587"
    echo ""

    read -p "SMTP Host: " SMTP_HOST
    read -p "SMTP Port [587]: " SMTP_PORT
    SMTP_PORT=${SMTP_PORT:-587}
    read -p "SMTP Username: " SMTP_USER
    read -s -p "SMTP Password/API Key: " SMTP_PASSWORD
    echo ""

    while [ -z "$SMTP_HOST" ] || [ -z "$SMTP_USER" ] || [ -z "$SMTP_PASSWORD" ]; do
        echo -e "${RED}SMTP configuration is required${NC}"
        read -p "SMTP Host: " SMTP_HOST
        read -p "SMTP Username: " SMTP_USER
        read -s -p "SMTP Password: " SMTP_PASSWORD
        echo ""
    done

    read -p "SMTP From domain (e.g., example.com): " SMTP_DOMAIN

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

# SMTP
SMTP_HOST=$SMTP_HOST
SMTP_PORT=$SMTP_PORT
SMTP_USER=$SMTP_USER
SMTP_PASSWORD=$SMTP_PASSWORD
SMTP_DOMAIN=$SMTP_DOMAIN

# Admin
SUPERADMIN_EMAIL=$SUPERADMIN_EMAIL

# Domain & SSL
DOMAIN=$DOMAIN
ACME_EMAIL=$ACME_EMAIL

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

    echo -e "${GREEN}✓ .env file created${NC}"
fi

# Load DOMAIN from .env for the completion message
DOMAIN=$(grep "^DOMAIN=" .env 2>/dev/null | cut -d= -f2)
SUPERADMIN_EMAIL=$(grep "^SUPERADMIN_EMAIL=" .env 2>/dev/null | cut -d= -f2)

# Firewall (Linux only)
if [[ "$OS_TYPE" == "linux" ]]; then
    echo ""
    read -p "Configure firewall (UFW)? [Y/n]: " SETUP_FIREWALL
    SETUP_FIREWALL=${SETUP_FIREWALL:-Y}

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
read -p "Start SINAS now? [Y/n]: " START_SERVICES
START_SERVICES=${START_SERVICES:-Y}

if [[ "$START_SERVICES" =~ ^[Yy]$ ]]; then
    if ! docker info &> /dev/null; then
        echo -e "${RED}Error: Docker daemon is not running${NC}"
        exit 1
    fi

    echo -e "${YELLOW}Pulling images...${NC}"
    docker compose pull

    echo -e "${YELLOW}Starting services...${NC}"
    docker compose up -d

    echo ""
    echo -e "${YELLOW}Waiting for services to be ready...${NC}"
    sleep 10

    if docker ps | grep -q sinas; then
        echo -e "${GREEN}✓ Services started successfully${NC}"
    else
        echo -e "${RED}⚠ Services may have failed. Check: docker compose logs${NC}"
    fi
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
echo "1. Point your DNS:"
echo "   ${DOMAIN} → $(curl -s ifconfig.me 2>/dev/null || echo '<your-server-ip>')"
echo ""
echo "2. SSL is automatic (Caddy + Let's Encrypt)"
echo ""
echo "3. Verify:"
echo "   curl https://${DOMAIN}/health"
echo ""
echo "4. Login:"
echo "   https://${DOMAIN}/docs → POST /auth/login"
echo "   Superadmin: ${SUPERADMIN_EMAIL}"
echo ""
echo "5. Update:"
echo "   cd ${INSTALL_DIR} && curl -fsSL ${RAW_URL}/install.sh | bash"
echo ""
echo "6. Logs:"
echo "   cd ${INSTALL_DIR} && docker compose logs -f"
echo ""
