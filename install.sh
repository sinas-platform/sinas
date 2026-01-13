#!/bin/bash
set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

echo -e "${BLUE}"
echo "╔════════════════════════════════════════╗"
echo "║   SINAS Agent Platform Installer      ║"
echo "╚════════════════════════════════════════╝"
echo -e "${NC}"

# Check if running as root
if [[ $EUID -ne 0 ]]; then
   echo -e "${RED}This script must be run as root (use sudo)${NC}"
   exit 1
fi

# Check prerequisites
echo -e "${YELLOW}Checking prerequisites...${NC}"

# Check Docker
if ! command -v docker &> /dev/null; then
    echo -e "${YELLOW}Docker not found. Installing...${NC}"
    curl -fsSL https://get.docker.com | sh
    echo -e "${GREEN}✓ Docker installed${NC}"
else
    echo -e "${GREEN}✓ Docker found${NC}"
fi

# Check Docker Compose
if ! docker compose version &> /dev/null; then
    echo -e "${YELLOW}Docker Compose not found. Installing...${NC}"
    apt-get update
    apt-get install -y docker-compose-plugin
    echo -e "${GREEN}✓ Docker Compose installed${NC}"
else
    echo -e "${GREEN}✓ Docker Compose found${NC}"
fi

echo ""
echo -e "${BLUE}═══════════════════════════════════════${NC}"
echo -e "${BLUE}  Configuration Setup${NC}"
echo -e "${BLUE}═══════════════════════════════════════${NC}"
echo ""

# Generate secure keys
echo -e "${YELLOW}Generating secure keys...${NC}"
SECRET_KEY=$(openssl rand -hex 32)
ENCRYPTION_KEY=$(python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())" 2>/dev/null || echo "")

if [ -z "$ENCRYPTION_KEY" ]; then
    echo -e "${YELLOW}Installing cryptography for key generation...${NC}"
    apt-get install -y python3-pip
    pip3 install cryptography
    ENCRYPTION_KEY=$(python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())")
fi

echo -e "${GREEN}✓ Keys generated${NC}"
echo ""

# Prompt for required values
echo -e "${BLUE}Please provide the following information:${NC}"
echo ""

# Domain
read -p "Domain name (e.g., api.example.com): " DOMAIN
while [ -z "$DOMAIN" ]; do
    echo -e "${RED}Domain is required${NC}"
    read -p "Domain name (e.g., api.example.com): " DOMAIN
done

# Email for SSL
read -p "Email for SSL certificates (e.g., admin@example.com): " ACME_EMAIL
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
echo -e "${YELLOW}SMTP Configuration (for OTP emails):${NC}"
echo "Common providers:"
echo "  - SendGrid: smtp.sendgrid.net:587, user: apikey"
echo "  - Mailgun: smtp.mailgun.org:587"
echo "  - AWS SES: email-smtp.region.amazonaws.com:587"
echo ""

read -p "SMTP Host (e.g., smtp.sendgrid.net): " SMTP_HOST
read -p "SMTP Port [default: 587]: " SMTP_PORT
SMTP_PORT=${SMTP_PORT:-587}

read -p "SMTP Username (e.g., apikey): " SMTP_USER
read -s -p "SMTP Password/API Key: " SMTP_PASSWORD
echo ""

while [ -z "$SMTP_HOST" ] || [ -z "$SMTP_USER" ] || [ -z "$SMTP_PASSWORD" ]; do
    echo -e "${RED}SMTP configuration is required for OTP login${NC}"
    read -p "SMTP Host: " SMTP_HOST
    read -p "SMTP Username: " SMTP_USER
    read -s -p "SMTP Password: " SMTP_PASSWORD
    echo ""
done

# SMTP Domain (for email from address)
SMTP_DOMAIN=$(echo "$DOMAIN" | sed 's/api\.//')

# Optional: LLM Provider API Keys
echo ""
echo -e "${YELLOW}LLM Provider API Keys (optional, can configure later):${NC}"
read -p "OpenAI API Key (press Enter to skip): " OPENAI_API_KEY
read -p "Anthropic API Key (press Enter to skip): " ANTHROPIC_API_KEY

# Create .env file
echo ""
echo -e "${YELLOW}Creating .env file...${NC}"

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
CADDY_DOMAIN=$DOMAIN
ACME_EMAIL=$ACME_EMAIL

# LLM Providers (optional - can configure via API later)
OPENAI_API_KEY=${OPENAI_API_KEY:-}
ANTHROPIC_API_KEY=${ANTHROPIC_API_KEY:-}

# Function Execution
FUNCTION_TIMEOUT=300
MAX_FUNCTION_MEMORY=512
ALLOW_PACKAGE_INSTALLATION=true

# ClickHouse (optional)
CLICKHOUSE_HOST=clickhouse
CLICKHOUSE_PORT=9000
CLICKHOUSE_USER=default
CLICKHOUSE_PASSWORD=
CLICKHOUSE_DB=sinas_logs
EOF

echo -e "${GREEN}✓ .env file created${NC}"

# Setup firewall (optional)
echo ""
read -p "Configure firewall (UFW)? [Y/n]: " SETUP_FIREWALL
SETUP_FIREWALL=${SETUP_FIREWALL:-Y}

if [[ "$SETUP_FIREWALL" =~ ^[Yy]$ ]]; then
    echo -e "${YELLOW}Configuring firewall...${NC}"

    # Check if UFW is installed
    if ! command -v ufw &> /dev/null; then
        apt-get install -y ufw
    fi

    # Configure UFW
    ufw --force reset
    ufw default deny incoming
    ufw default allow outgoing
    ufw allow 22/tcp   # SSH
    ufw allow 80/tcp   # HTTP
    ufw allow 443/tcp  # HTTPS
    ufw --force enable

    echo -e "${GREEN}✓ Firewall configured${NC}"
fi

# Start services
echo ""
read -p "Start SINAS services now? [Y/n]: " START_SERVICES
START_SERVICES=${START_SERVICES:-Y}

if [[ "$START_SERVICES" =~ ^[Yy]$ ]]; then
    echo -e "${YELLOW}Starting services...${NC}"
    docker compose up -d

    echo ""
    echo -e "${YELLOW}Waiting for services to be ready...${NC}"
    sleep 10

    # Check if services are running
    if docker ps | grep -q sinas-app; then
        echo -e "${GREEN}✓ Services started successfully${NC}"
    else
        echo -e "${RED}⚠ Services may have failed to start. Check logs with: docker compose logs${NC}"
    fi
fi

# Show completion message
echo ""
echo -e "${GREEN}"
echo "╔════════════════════════════════════════╗"
echo "║   Installation Complete!               ║"
echo "╚════════════════════════════════════════╝"
echo -e "${NC}"
echo ""
echo -e "${BLUE}Next steps:${NC}"
echo ""
echo "1. Update DNS:"
echo "   Add A record: $DOMAIN → $(curl -s ifconfig.me)"
echo ""
echo "2. Wait for SSL certificate (1-2 minutes)"
echo "   Caddy will automatically provision Let's Encrypt SSL"
echo ""
echo "3. Verify installation:"
echo "   curl https://$DOMAIN/health"
echo ""
echo "4. View API docs:"
echo "   https://$DOMAIN/docs"
echo ""
echo "5. Monitor logs:"
echo "   docker compose logs -f"
echo ""
echo -e "${YELLOW}Superadmin credentials:${NC}"
echo "   Email: $SUPERADMIN_EMAIL"
echo "   Login: https://$DOMAIN/docs → /auth/login"
echo "   (OTP will be sent to your email)"
echo ""
echo -e "${BLUE}Configuration saved to: .env${NC}"
echo ""
