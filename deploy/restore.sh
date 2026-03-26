#!/bin/bash
# Tendo -- Restauration de backup PostgreSQL
# Usage: bash deploy/restore.sh [fichier_backup]
# Exemple: bash deploy/restore.sh /opt/tendo/backups/tendo_2026-03-26_03-00-00.sql.gz

set -euo pipefail

BACKUP_DIR="/opt/tendo/backups"
CONTAINER_NAME="tendo-db"
DB_NAME="tendo"
DB_USER="tendo"

# Si aucun fichier specifie, utiliser le plus recent
if [ $# -eq 0 ]; then
    BACKUP_FILE=$(ls -1t "$BACKUP_DIR"/tendo_*.sql.gz 2>/dev/null | head -1)
    if [ -z "$BACKUP_FILE" ]; then
        echo "[Restore] ERREUR: Aucun backup trouve dans $BACKUP_DIR"
        exit 1
    fi
    echo "[Restore] Aucun fichier specifie, utilisation du plus recent:"
    echo "          $BACKUP_FILE"
else
    BACKUP_FILE="$1"
fi

if [ ! -f "$BACKUP_FILE" ]; then
    echo "[Restore] ERREUR: Fichier non trouve: $BACKUP_FILE"
    exit 1
fi

BACKUP_SIZE=$(du -h "$BACKUP_FILE" | cut -f1)
echo "[Restore] Fichier: $BACKUP_FILE ($BACKUP_SIZE)"
echo ""
echo "ATTENTION: Cette operation va REMPLACER toutes les donnees actuelles !"
echo "Tapez 'OUI' pour confirmer:"
read -r CONFIRM

if [ "$CONFIRM" != "OUI" ]; then
    echo "[Restore] Annule."
    exit 0
fi

echo "[Restore] Arret de l'application Tendo..."
docker stop tendo-api 2>/dev/null || true

echo "[Restore] Restauration en cours..."
gunzip -c "$BACKUP_FILE" | docker exec -i "$CONTAINER_NAME" psql -U "$DB_USER" -d "$DB_NAME"

echo "[Restore] Redemarrage de Tendo..."
docker start tendo-api

sleep 5
if curl -sf http://localhost:8000/health > /dev/null 2>&1; then
    echo "[Restore] Restauration terminee. Tendo est operationnel."
else
    echo "[Restore] Tendo ne repond pas. Verifiez les logs: docker compose logs -f tendo"
fi
