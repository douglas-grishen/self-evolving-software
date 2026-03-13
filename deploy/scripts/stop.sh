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
