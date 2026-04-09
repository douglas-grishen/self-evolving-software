#!/bin/bash
# CodeDeploy hook: stop running services before deployment
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=deploy/scripts/common.sh
. "$SCRIPT_DIR/common.sh"

load_instance_environment

if [ -f "$FRAMEWORK_ROOT/$COMPOSE_FILE" ]; then
    echo "Stopping services for compose project $COMPOSE_PROJECT..."
    compose_cmd down --timeout 30 || true
    echo "Services stopped."
else
    echo "No existing deployment found at $FRAMEWORK_ROOT, skipping stop."
fi

if [ -d "$FRAMEWORK_ROOT" ]; then
    echo "Cleaning previous framework tree..."
    find "$FRAMEWORK_ROOT" -mindepth 1 -maxdepth 1 ! -name ".env" -exec rm -rf {} +
    echo "Framework tree cleaned."
fi

if [ -d "$BUNDLE_ROOT" ]; then
    echo "Cleaning previous CodeDeploy bundle tree..."
    find "$BUNDLE_ROOT" -mindepth 1 -maxdepth 1 -exec rm -rf {} +
    echo "Bundle tree cleaned."
fi
