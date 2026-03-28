#!/bin/bash
# CodeDeploy hook: build Docker images after source is copied
#
# Two-layer architecture:
#   /opt/self-evolving-software/   Framework (this repo, from GitHub)
#   /opt/evolved-app/              Evolved code (local git, never pushed)
#
# On first deploy, we bootstrap /opt/evolved-app/ from the managed_app/ template.
# On subsequent deploys, evolved-app is preserved with all its local evolutions.
set -e

APP_DIR="/opt/self-evolving-software"
EVOLVED_DIR="/opt/evolved-app"

cd "$APP_DIR"

# Ensure .env exists (created by user data on first boot)
if [ ! -f "$APP_DIR/.env" ] && [ -f /home/ec2-user/.env ]; then
    cp /home/ec2-user/.env "$APP_DIR/.env"
fi

# Ensure git is installed on the host (needed for evolved-app local repo)
if ! command -v git &> /dev/null; then
    echo "Installing git..."
    yum install -y git 2>/dev/null || dnf install -y git 2>/dev/null || apt-get install -y git 2>/dev/null
fi

# ---------------------------------------------------------------------------
# Bootstrap evolved-app on first deploy
# ---------------------------------------------------------------------------
if [ ! -d "$EVOLVED_DIR/.git" ]; then
    echo "Bootstrapping evolved-app from managed_app template..."
    mkdir -p "$EVOLVED_DIR"

    # Copy the template app (backend + frontend)
    cp -r "$APP_DIR/managed_app/backend" "$EVOLVED_DIR/backend"
    cp -r "$APP_DIR/managed_app/frontend" "$EVOLVED_DIR/frontend"

    # Initialize local git repo (for history + rollback, never pushed)
    cd "$EVOLVED_DIR"
    git init
    git add -A
    git commit -m "initial: base template from managed_app"

    echo "Evolved-app bootstrapped at $EVOLVED_DIR"
    cd "$APP_DIR"
else
    echo "Evolved-app already exists at $EVOLVED_DIR — preserving local evolutions."
fi

# ---------------------------------------------------------------------------
# Build Docker images
# ---------------------------------------------------------------------------
# Re-sync framework-owned core API files on every deployment so instance-local
# drift cannot silently remove required platform routes.
CORE_FILES_MANIFEST="$APP_DIR/protected_framework_files.txt"
if [ -f "$CORE_FILES_MANIFEST" ]; then
    echo "Syncing framework-owned core backend files into evolved-app..."
    while IFS= read -r rel_path; do
        [ -z "$rel_path" ] && continue
        case "$rel_path" in
            \#*) continue ;;
        esac

        src="$APP_DIR/managed_app/$rel_path"
        dst="$EVOLVED_DIR/$rel_path"
        if [ ! -f "$src" ]; then
            continue
        fi

        mkdir -p "$(dirname "$dst")"
        cp "$src" "$dst"
    done < "$CORE_FILES_MANIFEST"
fi

echo "Building Docker images..."
docker compose -f docker-compose.prod.yml build --parallel
echo "Build complete."
