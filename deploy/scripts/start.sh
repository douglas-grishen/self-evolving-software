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
HEALTH_URLS=(
    "http://localhost/api/v1/health"
    "http://localhost/health"
)

for i in $(seq 1 60); do
    for url in "${HEALTH_URLS[@]}"; do
        if curl -sf "$url" > /dev/null 2>&1; then
            echo "Backend is healthy via $url"
            exit 0
        fi
    done
    sleep 2
done

echo "WARNING: Backend health check did not pass within 120 seconds."
echo "Checked URLs:"
printf ' - %s\n' "${HEALTH_URLS[@]}"
echo "=== Container status ==="
docker compose -f docker-compose.prod.yml ps
echo "=== Backend logs ==="
docker compose -f docker-compose.prod.yml logs --tail=30 backend
echo "=== Postgres logs ==="
docker compose -f docker-compose.prod.yml logs --tail=30 postgres
exit 1
