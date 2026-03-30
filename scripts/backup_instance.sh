#!/usr/bin/env bash
set -euo pipefail

INSTANCE_KEY="${INSTANCE_KEY:-base}"
FRAMEWORK_ROOT="${FRAMEWORK_ROOT:-/opt/self-evolving-software}"
EVOLVED_APP_ROOT="${EVOLVED_APP_ROOT:-/opt/evolved-app}"
INSTANCE_STATE_ROOT="${INSTANCE_STATE_ROOT:-$EVOLVED_APP_ROOT/.instance-state}"
PGDATA_ROOT="${PGDATA_ROOT:-/mnt/pgdata/data}"
BACKUP_ROOT="${BACKUP_ROOT:-$PWD/backups}"
TIMESTAMP="$(date -u +"%Y%m%dT%H%M%SZ")"
OUTPUT_DIR="${1:-$BACKUP_ROOT/$INSTANCE_KEY/$TIMESTAMP}"

mkdir -p "$OUTPUT_DIR"

write_manifest() {
    cat >"$OUTPUT_DIR/manifest.env" <<EOF
INSTANCE_KEY=$INSTANCE_KEY
FRAMEWORK_ROOT=$FRAMEWORK_ROOT
EVOLVED_APP_ROOT=$EVOLVED_APP_ROOT
INSTANCE_STATE_ROOT=$INSTANCE_STATE_ROOT
PGDATA_ROOT=$PGDATA_ROOT
CREATED_AT_UTC=$TIMESTAMP
EOF
}

archive_dir() {
    local source_dir="$1"
    local archive_name="$2"

    if [ ! -d "$source_dir" ]; then
        echo "Skipping missing directory: $source_dir"
        return 0
    fi

    local parent_dir
    local base_name
    parent_dir="$(dirname "$source_dir")"
    base_name="$(basename "$source_dir")"
    tar -czf "$OUTPUT_DIR/$archive_name" -C "$parent_dir" "$base_name"
    echo "Created $OUTPUT_DIR/$archive_name"
}

write_manifest
archive_dir "$EVOLVED_APP_ROOT" "evolved-app.tar.gz"
archive_dir "$INSTANCE_STATE_ROOT" "instance-state.tar.gz"
archive_dir "$PGDATA_ROOT" "pgdata.tar.gz"

echo "Backup bundle written to $OUTPUT_DIR"
