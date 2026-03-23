#!/bin/bash
# ═══════════════════════════════════════════════════════════════
# Tendo — Script de déploiement VPS (Ubuntu 22.04+)
# ═══════════════════════════════════════════════════════════════
# Usage: curl -sSL https://raw.githubusercontent.com/.../setup_vps.sh | bash
# Ou : bash deploy/setup_vps.sh
#
# Ce script installe et configure :
# - Docker + Docker Compose
# - Nginx reverse proxy avec SSL (Let's Encrypt)
# - Tendo (docker-compose)
# ═══════════════════════════════════════════════════════════════

set -euo pipefail

DOMAIN="${DOMAIN:-tendo.shiftup.bj}"
EMAIL="${EMAIL:-jgnancadja@gmail.com}"
APP_DIR="/opt/tendo"

echo "═══════════════════════════════════════"
echo "  Tendo — Déploiement sur VPS"
echo "  Domaine: $DOMAIN"
echo "═══════════════════════════════════════"

# ── 1. Mise à jour système ──
echo "[1/6] Mise à jour du système..."
apt-get update -qq && apt-get upgrade -y -qq

# ── 2. Docker ──
echo "[2/6] Installation de Docker..."
if ! command -v docker &>/dev/null; then
    curl -fsSL https://get.docker.com | sh
    systemctl enable docker
    systemctl start docker
fi

if ! command -v docker-compose &>/dev/null && ! docker compose version &>/dev/null; then
    apt-get install -y docker-compose-plugin
fi

# ── 3. Nginx + Certbot ──
echo "[3/6] Installation de Nginx + Certbot..."
apt-get install -y nginx certbot python3-certbot-nginx

# ── 4. Configuration Nginx ──
echo "[4/6] Configuration Nginx..."
cat > /etc/nginx/sites-available/tendo <<NGINX
server {
    listen 80;
    server_name $DOMAIN;

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_http_version 1.1;
        proxy_set_header Upgrade \$http_upgrade;
        proxy_set_header Connection 'upgrade';
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
        proxy_cache_bypass \$http_upgrade;
        proxy_read_timeout 300;
        proxy_connect_timeout 300;
    }
}
NGINX

ln -sf /etc/nginx/sites-available/tendo /etc/nginx/sites-enabled/
rm -f /etc/nginx/sites-enabled/default
nginx -t && systemctl reload nginx

# ── 5. SSL ──
echo "[5/6] Configuration SSL (Let's Encrypt)..."
certbot --nginx -d "$DOMAIN" --non-interactive --agree-tos -m "$EMAIL" || {
    echo "⚠️  SSL échoué — vérifiez que le DNS pointe vers ce serveur"
    echo "    Vous pouvez réessayer avec: certbot --nginx -d $DOMAIN"
}

# ── 6. Déploiement Tendo ──
echo "[6/6] Déploiement de Tendo..."
mkdir -p "$APP_DIR"

if [ ! -f "$APP_DIR/.env" ]; then
    echo "⚠️  Créez le fichier $APP_DIR/.env avec vos variables d'environnement"
    echo "    Puis lancez: cd $APP_DIR && docker compose up -d"
else
    cd "$APP_DIR"
    docker compose up -d --build
fi

echo ""
echo "═══════════════════════════════════════"
echo "  ✅ Installation terminée !"
echo ""
echo "  Prochaines étapes :"
echo "  1. Copiez vos fichiers dans $APP_DIR/"
echo "  2. Créez $APP_DIR/.env"
echo "  3. cd $APP_DIR && docker compose up -d"
echo "  4. Testez: curl https://$DOMAIN/health"
echo "  5. Mettez à jour le webhook Meta:"
echo "     https://$DOMAIN/webhook/whatsapp"
echo "═══════════════════════════════════════"
