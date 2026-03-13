#!/bin/bash
# CodeDeploy hook: start all services after build
set -e

APP_DIR="/opt/self-evolving-software"
cd "$APP_DIR"

echo "Starting services..."
docker compose -f docker-compose.prod.yml up -d

# Wait for backend health check
echo "Waiting for backend health check..."
for i in $(seq 1 30); do
    if curl -sf http://localhost:8000/health > /dev/null 2>&1; then
        echo "Backend is healthy."
        exit 0
    fi
    sleep 2
done

echo "WARNING: Backend health check did not pass within 60 seconds."
docker compose -f docker-compose.prod.yml logs --tail=50
exit 1
