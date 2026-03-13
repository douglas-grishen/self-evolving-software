#!/bin/bash
# CodeDeploy hook: start all services after build
set -e

APP_DIR="/opt/self-evolving-software"
cd "$APP_DIR"

# Ensure .env exists
if [ ! -f "$APP_DIR/.env" ] && [ -f /home/ec2-user/.env ]; then
    cp /home/ec2-user/.env "$APP_DIR/.env"
fi

echo "Starting services..."
docker compose -f docker-compose.prod.yml up -d

# Wait for backend health check (up to 120s for first-time postgres init)
echo "Waiting for backend health check..."
for i in $(seq 1 60); do
    if curl -sf http://localhost:8000/health > /dev/null 2>&1; then
        echo "Backend is healthy!"
        exit 0
    fi
    sleep 2
done

echo "WARNING: Backend health check did not pass within 120 seconds."
echo "=== Container status ==="
docker compose -f docker-compose.prod.yml ps
echo "=== Backend logs ==="
docker compose -f docker-compose.prod.yml logs --tail=30 backend
echo "=== Postgres logs ==="
docker compose -f docker-compose.prod.yml logs --tail=30 postgres
exit 1
