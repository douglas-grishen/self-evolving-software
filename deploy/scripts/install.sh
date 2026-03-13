#!/bin/bash
# CodeDeploy hook: build Docker images after source is copied
set -e

APP_DIR="/opt/self-evolving-software"
cd "$APP_DIR"

# Ensure .env exists (created by user data on first boot)
if [ ! -f "$APP_DIR/.env" ] && [ -f /home/ec2-user/.env ]; then
    cp /home/ec2-user/.env "$APP_DIR/.env"
fi

echo "Building Docker images..."
docker compose -f docker-compose.prod.yml build --parallel
echo "Build complete."
