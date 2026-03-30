#!/bin/bash
# CodeDeploy hook: start all services after build
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=deploy/scripts/common.sh
. "$SCRIPT_DIR/common.sh"

load_instance_environment
ensure_framework_env_file

cd "$FRAMEWORK_ROOT"

echo "Starting services for compose project $COMPOSE_PROJECT..."
compose_cmd up -d

# Wait for backend health check (up to 120s for first-time postgres init)
echo "Waiting for backend health check..."
for i in $(seq 1 60); do
    if curl -sf http://localhost/health > /dev/null 2>&1; then
        echo "Backend is healthy!"
        exit 0
    fi
    sleep 2
done

echo "WARNING: Backend health check did not pass within 120 seconds."
echo "=== Container status ==="
compose_cmd ps
echo "=== Backend logs ==="
compose_cmd logs --tail=30 backend
echo "=== Postgres logs ==="
compose_cmd logs --tail=30 postgres
exit 1
