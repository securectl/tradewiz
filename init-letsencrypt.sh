#!/bin/bash
# ═══════════════════════════════════════════════════════════════
# Let's Encrypt SSL Certificate Bootstrap Script
# Domain: tradewiz.market + www.tradewiz.market
# ═══════════════════════════════════════════════════════════════
set -e

DOMAINS=(tradewiz.market www.tradewiz.market)
DATA_PATH="./certbot"
EMAIL="${LETSENCRYPT_EMAIL:-admin@tradewiz.market}"
STAGING=${LETSENCRYPT_STAGING:-0}  # Set to 1 for testing

RSA_KEY_SIZE=4096
COMPOSE="docker compose"

# Check if docker compose is available
if ! $COMPOSE version &> /dev/null; then
    COMPOSE="docker-compose"
fi

echo "══════════════════════════════════════════"
echo " Let's Encrypt SSL Setup"
echo " Domains: ${DOMAINS[*]}"
echo " Email: $EMAIL"
echo " Staging: $STAGING"
echo "══════════════════════════════════════════"

# Create required directories
mkdir -p "$DATA_PATH/conf/live/tradewiz.market"
mkdir -p "$DATA_PATH/www"

# Check for existing certificates
if [ -d "$DATA_PATH/conf/live/tradewiz.market" ] && [ -f "$DATA_PATH/conf/live/tradewiz.market/fullchain.pem" ]; then
    read -p "Existing certificates found. Replace? (y/N) " decision
    if [ "$decision" != "Y" ] && [ "$decision" != "y" ]; then
        echo "Keeping existing certificates."
        exit 0
    fi
fi

# Download recommended TLS parameters
if [ ! -f "$DATA_PATH/conf/options-ssl-nginx.conf" ]; then
    echo "Downloading recommended TLS parameters..."
    curl -s https://raw.githubusercontent.com/certbot/certbot/master/certbot-nginx/certbot_nginx/_internal/tls_configs/options-ssl-nginx.conf \
        > "$DATA_PATH/conf/options-ssl-nginx.conf"
fi
if [ ! -f "$DATA_PATH/conf/ssl-dhparams.pem" ]; then
    echo "Downloading DH parameters..."
    curl -s https://raw.githubusercontent.com/certbot/certbot/master/certbot/certbot/ssl-dhparams.pem \
        > "$DATA_PATH/conf/ssl-dhparams.pem"
fi

# Create dummy certificate for nginx to start
echo "Creating dummy certificate for nginx startup..."
openssl req -x509 -nodes -newkey rsa:$RSA_KEY_SIZE -days 1 \
    -keyout "$DATA_PATH/conf/live/tradewiz.market/privkey.pem" \
    -out "$DATA_PATH/conf/live/tradewiz.market/fullchain.pem" \
    -subj "/CN=localhost" 2>/dev/null

# Start nginx with dummy cert
echo "Starting nginx..."
$COMPOSE up -d nginx
sleep 5

# Delete dummy certificate
echo "Removing dummy certificate..."
rm -rf "$DATA_PATH/conf/live/tradewiz.market"

# Request real certificate
echo "Requesting Let's Encrypt certificate..."

# Build domain args
DOMAIN_ARGS=""
for domain in "${DOMAINS[@]}"; do
    DOMAIN_ARGS="$DOMAIN_ARGS -d $domain"
done

# Set staging flag
STAGING_ARG=""
if [ "$STAGING" = "1" ]; then
    STAGING_ARG="--staging"
    echo "  (Using staging server for testing)"
fi

$COMPOSE run --rm --entrypoint "certbot certonly \
    --webroot \
    --webroot-path=/var/www/certbot \
    $STAGING_ARG \
    --email $EMAIL \
    --rsa-key-size $RSA_KEY_SIZE \
    --agree-tos \
    --no-eff-email \
    --force-renewal \
    $DOMAIN_ARGS" certbot

echo ""
echo "Reloading nginx with real certificate..."
$COMPOSE exec nginx nginx -s reload

echo ""
echo "══════════════════════════════════════════"
echo " SSL setup complete!"
echo " https://tradewiz.market"
echo " https://www.tradewiz.market"
echo "══════════════════════════════════════════"
echo ""
echo "Starting all services..."
$COMPOSE up -d
