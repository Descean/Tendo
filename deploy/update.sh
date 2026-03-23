#!/bin/bash
# Tendo — Script de mise à jour
# Usage: bash deploy/update.sh

set -euo pipefail

APP_DIR="/opt/tendo"
cd "$APP_DIR"

echo "🔄 Mise à jour de Tendo..."

# Pull les derniers changements
git pull origin main 2>/dev/null || echo "Pas de repo git — copie manuelle attendue"

# Rebuild et redémarrer
docker compose down
docker compose up -d --build

# Attendre le démarrage
echo "⏳ Attente du démarrage..."
sleep 5

# Vérifier la santé
if curl -sf http://localhost:8000/health > /dev/null; then
    echo "✅ Tendo est opérationnel !"
    curl -s http://localhost:8000/health | python3 -m json.tool
else
    echo "❌ Tendo ne répond pas — vérifiez les logs :"
    echo "   docker compose logs -f tendo"
fi
