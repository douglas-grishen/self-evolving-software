#!/bin/bash
# CodeDeploy hook: promote the fetched bundle into the instance-specific
# framework root, then build Docker images for that instance.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=deploy/scripts/common.sh
. "$SCRIPT_DIR/common.sh"

load_instance_environment
ensure_framework_env_file

echo "Promoting bundle from $BUNDLE_ROOT to $FRAMEWORK_ROOT..."
mkdir -p "$FRAMEWORK_ROOT"
cp -a "$BUNDLE_ROOT"/. "$FRAMEWORK_ROOT"/

cd "$FRAMEWORK_ROOT"

echo "Neutralizing any legacy Purpose seeds from the framework bundle..."
rm -f "$FRAMEWORK_ROOT/purpose.yaml"
rm -f "$EVOLVED_APP_ROOT/.engine-state/purpose.yaml"

if ! command -v git &> /dev/null; then
    echo "Installing git..."
    yum install -y git 2>/dev/null || dnf install -y git 2>/dev/null || apt-get install -y git 2>/dev/null
fi

if [ ! -d "$EVOLVED_APP_ROOT/.git" ]; then
    echo "Bootstrapping evolved app for instance '$INSTANCE_KEY'..."
    mkdir -p "$EVOLVED_APP_ROOT"
    cp -r "$FRAMEWORK_ROOT/managed_app/backend" "$EVOLVED_APP_ROOT/backend"
    cp -r "$FRAMEWORK_ROOT/managed_app/frontend" "$EVOLVED_APP_ROOT/frontend"

    if [ -d "$FRAMEWORK_ROOT/$INSTANCE_OVERLAY_PATH/seed/operational-plane" ]; then
        cp -a "$FRAMEWORK_ROOT/$INSTANCE_OVERLAY_PATH/seed/operational-plane"/. "$EVOLVED_APP_ROOT"/
    fi

    cd "$EVOLVED_APP_ROOT"
    git init
    git config user.name "Self-Evolving Software"
    git config user.email "noreply@self-evolving.local"
    git add -A
    git commit -m "initial: base template from operational plane"
    cd "$FRAMEWORK_ROOT"
else
    echo "Evolved app already exists at $EVOLVED_APP_ROOT — preserving local evolutions."
fi

mkdir -p "$INSTANCE_STATE_ROOT" "$PURPOSE_HISTORY_PATH"
if [ ! -f "$GENESIS_PATH" ] && [ -f "$GENESIS_SEED_PATH" ]; then
    echo "Seeding instance genesis from $GENESIS_SEED_PATH..."
    cp "$GENESIS_SEED_PATH" "$GENESIS_PATH"
fi
if [ ! -f "$RUNTIME_CONTRACTS_PATH" ] && [ -f "$RUNTIME_CONTRACTS_SEED_PATH" ]; then
    echo "Seeding instance runtime contracts from $RUNTIME_CONTRACTS_SEED_PATH..."
    cp "$RUNTIME_CONTRACTS_SEED_PATH" "$RUNTIME_CONTRACTS_PATH"
fi

echo "Syncing shared shell manifests into evolved app..."
install -D -m 0644 "$FRAMEWORK_ROOT/managed_app/backend/app/main.py" \
  "$EVOLVED_APP_ROOT/backend/app/main.py"
install -D -m 0644 "$FRAMEWORK_ROOT/managed_app/backend/app/api/v1/__init__.py" \
  "$EVOLVED_APP_ROOT/backend/app/api/v1/__init__.py"
install -D -m 0644 "$FRAMEWORK_ROOT/managed_app/backend/app/config.py" \
  "$EVOLVED_APP_ROOT/backend/app/config.py"
install -D -m 0644 "$FRAMEWORK_ROOT/managed_app/backend/pyproject.toml" \
  "$EVOLVED_APP_ROOT/backend/pyproject.toml"
install -D -m 0644 "$FRAMEWORK_ROOT/managed_app/frontend/package.json" \
  "$EVOLVED_APP_ROOT/frontend/package.json"
install -D -m 0644 "$FRAMEWORK_ROOT/managed_app/frontend/package-lock.json" \
  "$EVOLVED_APP_ROOT/frontend/package-lock.json"

echo "Building Docker images for compose project $COMPOSE_PROJECT..."
compose_cmd build --parallel
echo "Build complete."
