#!/bin/bash

resolve_bundle_root() {
    local script_dir
    script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
    cd "$script_dir/../.." && pwd
}

load_instance_environment() {
    BUNDLE_ROOT="${BUNDLE_ROOT:-$(resolve_bundle_root)}"

    INSTANCE_KEY="${INSTANCE_KEY:-base}"
    INSTANCE_OVERLAY_PATH="${INSTANCE_OVERLAY_PATH:-instances/${INSTANCE_KEY}}"

    if [ -f "$BUNDLE_ROOT/$INSTANCE_OVERLAY_PATH/instance.env" ]; then
        set -a
        . "$BUNDLE_ROOT/$INSTANCE_OVERLAY_PATH/instance.env"
        set +a
    fi

    if [ -f /home/ec2-user/.env ]; then
        set -a
        . /home/ec2-user/.env
        set +a
    fi

    FRAMEWORK_ROOT="${FRAMEWORK_ROOT:-/opt/self-evolving-software}"
    EVOLVED_APP_ROOT="${EVOLVED_APP_ROOT:-/opt/evolved-app}"
    INSTANCE_STATE_ROOT="${INSTANCE_STATE_ROOT:-$EVOLVED_APP_ROOT/.instance-state}"
    INSTANCE_OVERLAY_PATH="${INSTANCE_OVERLAY_PATH:-instances/${INSTANCE_KEY}}"
    PURPOSE_PATH="${PURPOSE_PATH:-$INSTANCE_STATE_ROOT/purpose.yaml}"
    PURPOSE_HISTORY_PATH="${PURPOSE_HISTORY_PATH:-$INSTANCE_STATE_ROOT/purpose_history}"
    GENESIS_SEED_PATH="${GENESIS_SEED_PATH:-$FRAMEWORK_ROOT/genesis.yaml}"
    GENESIS_PATH="${GENESIS_PATH:-$INSTANCE_STATE_ROOT/genesis.yaml}"
    RUNTIME_CONTRACTS_SEED_PATH="${RUNTIME_CONTRACTS_SEED_PATH:-$FRAMEWORK_ROOT/contracts.example.yaml}"
    RUNTIME_CONTRACTS_PATH="${RUNTIME_CONTRACTS_PATH:-$INSTANCE_STATE_ROOT/contracts.yaml}"
    USAGE_STATE_PATH="${USAGE_STATE_PATH:-$INSTANCE_STATE_ROOT/usage.json}"
    COMPOSE_FILE="${COMPOSE_FILE:-docker-compose.prod.yml}"
    COMPOSE_PROJECT="${COMPOSE_PROJECT:-self-evolving-software}"
    PUBLIC_HOST="${PUBLIC_HOST:-localhost}"
    POSTGRES_DB="${POSTGRES_DB:-managed_app}"
    APP_APP_NAME="${APP_APP_NAME:-Managed App}"
}

ensure_framework_env_file() {
    mkdir -p "$FRAMEWORK_ROOT"
    if [ ! -f "$FRAMEWORK_ROOT/.env" ] && [ -f /home/ec2-user/.env ]; then
        cp /home/ec2-user/.env "$FRAMEWORK_ROOT/.env"
    fi
}

compose_cmd() {
    docker compose -p "$COMPOSE_PROJECT" -f "$FRAMEWORK_ROOT/$COMPOSE_FILE" "$@"
}
