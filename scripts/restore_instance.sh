#!/usr/bin/env bash
set -euo pipefail

if [ "${FORCE:-0}" != "1" ]; then
    echo "Refusing to restore without FORCE=1."
    exit 1
fi

BACKUP_DIR="${1:-}"
if [ -z "$BACKUP_DIR" ]; then
    echo "Usage: FORCE=1 $0 /path/to/backup-dir"
    exit 1
fi

if [ ! -d "$BACKUP_DIR" ]; then
    echo "Backup directory not found: $BACKUP_DIR"
    exit 1
fi

FRAMEWORK_ROOT="${FRAMEWORK_ROOT:-/opt/self-evolving-software}"
EVOLVED_APP_ROOT="${EVOLVED_APP_ROOT:-/opt/evolved-app}"
INSTANCE_STATE_ROOT="${INSTANCE_STATE_ROOT:-$EVOLVED_APP_ROOT/.instance-state}"
PGDATA_ROOT="${PGDATA_ROOT:-/mnt/pgdata/data}"
COMPOSE_FILE="${COMPOSE_FILE:-docker-compose.prod.yml}"
COMPOSE_PROJECT="${COMPOSE_PROJECT:-self-evolving-software}"
STOP_STACK="${STOP_STACK:-1}"

restore_archive() {
    local archive_path="$1"
    local target_dir="$2"

    if [ ! -f "$archive_path" ]; then
        echo "Skipping missing archive: $archive_path"
        return 0
    fi

    mkdir -p "$(dirname "$target_dir")"
    rm -rf "$target_dir"
    tar -xzf "$archive_path" -C "$(dirname "$target_dir")"
    echo "Restored $archive_path"
}

if [ "$STOP_STACK" = "1" ] && [ -f "$FRAMEWORK_ROOT/$COMPOSE_FILE" ]; then
    docker compose -p "$COMPOSE_PROJECT" -f "$FRAMEWORK_ROOT/$COMPOSE_FILE" down --timeout 30 || true
fi

restore_archive "$BACKUP_DIR/evolved-app.tar.gz" "$EVOLVED_APP_ROOT"
restore_archive "$BACKUP_DIR/instance-state.tar.gz" "$INSTANCE_STATE_ROOT"
restore_archive "$BACKUP_DIR/pgdata.tar.gz" "$PGDATA_ROOT"

if [ -d "$PGDATA_ROOT" ]; then
    chown -R 999:999 "$PGDATA_ROOT" || true
fi

echo "Restore complete."
