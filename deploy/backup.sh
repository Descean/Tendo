#!/bin/bash
# Tendo -- Backup automatique PostgreSQL
# Usage: bash deploy/backup.sh
# Cron recommande: 0 3 * * * /opt/tendo/deploy/backup.sh >> /var/log/tendo-backup.log 2>&1

set -euo pipefail

# Configuration
BACKUP_DIR="/opt/tendo/backups"
CONTAINER_NAME="tendo-db"
DB_NAME="tendo"
DB_USER="tendo"
MAX_BACKUPS=14  # Garde 14 jours de backups
DATE=$(date +%Y-%m-%d_%H-%M-%S)
BACKUP_FILE="$BACKUP_DIR/tendo_${DATE}.sql.gz"

# Creer le dossier de backup s'il n'existe pas
mkdir -p "$BACKUP_DIR"

echo "[Backup] Demarrage du backup PostgreSQL -- $DATE"

# Verifier que le container PostgreSQL tourne
if ! docker ps --format '{{.Names}}' | grep -q "$CONTAINER_NAME"; then
    echo "[Backup] ERREUR: Container $CONTAINER_NAME non trouve ou arrete"
    exit 1
fi

# Effectuer le dump compresse
echo "[Backup] Dump de la base $DB_NAME..."
docker exec "$CONTAINER_NAME" pg_dump -U "$DB_USER" -d "$DB_NAME" --clean --if-exists | gzip > "$BACKUP_FILE"

# Verifier que le fichier a ete cree et n'est pas vide
if [ ! -s "$BACKUP_FILE" ]; then
    echo "[Backup] ERREUR: Fichier backup vide ou non cree"
    rm -f "$BACKUP_FILE"
    exit 1
fi

BACKUP_SIZE=$(du -h "$BACKUP_FILE" | cut -f1)
echo "[Backup] Backup cree: $BACKUP_FILE ($BACKUP_SIZE)"

# Rotation: supprimer les backups plus anciens que MAX_BACKUPS
echo "[Backup] Rotation des anciens backups (garde $MAX_BACKUPS derniers)..."
BACKUP_COUNT=$(ls -1 "$BACKUP_DIR"/tendo_*.sql.gz 2>/dev/null | wc -l)
if [ "$BACKUP_COUNT" -gt "$MAX_BACKUPS" ]; then
    EXCESS=$((BACKUP_COUNT - MAX_BACKUPS))
    ls -1t "$BACKUP_DIR"/tendo_*.sql.gz | tail -n "$EXCESS" | xargs rm -f
    echo "[Backup] $EXCESS ancien(s) backup(s) supprime(s)"
fi

echo "[Backup] Termine avec succes -- $(date)"
