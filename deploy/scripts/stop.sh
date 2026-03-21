#!/bin/bash
# CodeDeploy hook: stop running services before deployment
set -e

APP_DIR="/opt/self-evolving-software"

if [ -f "$APP_DIR/docker-compose.prod.yml" ]; then
    echo "Stopping services..."
    cd "$APP_DIR"
    docker compose -f docker-compose.prod.yml down --timeout 30 || true
    echo "Services stopped."
else
    echo "No existing deployment found, skipping stop."
fi

# CodeDeploy copies the new bundle into APP_DIR with fileExistsBehavior=DISALLOW.
# If the new revision adds files that are not present in the old tree, the Install
# phase fails before our AfterInstall hooks run. Clear the previous framework tree
# up front while preserving the instance-local .env backup path used by install.sh.
if [ -d "$APP_DIR" ]; then
    echo "Cleaning previous framework tree..."
    find "$APP_DIR" -mindepth 1 -maxdepth 1 ! -name ".env" -exec rm -rf {} +
    echo "Framework tree cleaned."
fi
