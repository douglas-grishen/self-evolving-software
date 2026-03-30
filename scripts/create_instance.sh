#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
DEPLOY_ENV_PATH="${REPO_ROOT}/infra/deploy.env"

detect_origin_slug() {
    local remote_url slug
    if ! remote_url="$(git -C "${REPO_ROOT}" remote get-url origin 2>/dev/null)"; then
        return 1
    fi

    case "${remote_url}" in
        git@github.com:*)
            slug="${remote_url#git@github.com:}"
            ;;
        https://github.com/*)
            slug="${remote_url#https://github.com/}"
            ;;
        http://github.com/*)
            slug="${remote_url#http://github.com/}"
            ;;
        *)
            return 1
            ;;
    esac

    slug="${slug%.git}"
    if [[ "${slug}" != */* ]]; then
        return 1
    fi
    printf '%s\n' "${slug}"
}

detect_current_branch() {
    git -C "${REPO_ROOT}" branch --show-current 2>/dev/null || true
}

if [ -f "${DEPLOY_ENV_PATH}" ]; then
    set -a
    # shellcheck disable=SC1090
    . "${DEPLOY_ENV_PATH}"
    set +a
fi

INSTANCE_KEY="${INSTANCE_KEY:-}"
AWS_PROFILE="${AWS_PROFILE:-default}"
AWS_REGION="${AWS_REGION:-us-east-1}"
GITHUB_OWNER="${GITHUB_OWNER:-douglas-grishen}"
GITHUB_REPO="${GITHUB_REPO:-self-evolving-software}"
GITHUB_BRANCH="${GITHUB_BRANCH:-main}"
ORIGIN_SLUG="$(detect_origin_slug || true)"
CURRENT_BRANCH="$(detect_current_branch)"
CONNECTION_ARN="${CONNECTION_ARN:-}"
SSH_CIDR="${SSH_CIDR:-0.0.0.0/0}"
PUBLIC_HOST="${PUBLIC_HOST:-}"
APP_APP_NAME="${APP_APP_NAME:-Managed App}"
INIT_CONTRACTS=0
RUN_DEPLOY=0
FORCE=0
GITHUB_OWNER_EXPLICIT=0
GITHUB_REPO_EXPLICIT=0
GITHUB_BRANCH_EXPLICIT=0

if [ -n "${ORIGIN_SLUG}" ]; then
    if [ "${GITHUB_OWNER_EXPLICIT}" = "0" ]; then
        GITHUB_OWNER="${ORIGIN_SLUG%%/*}"
    fi
    if [ "${GITHUB_REPO_EXPLICIT}" = "0" ]; then
        GITHUB_REPO="${ORIGIN_SLUG#*/}"
    fi
fi

if [ -n "${CURRENT_BRANCH}" ]; then
    GITHUB_BRANCH="${CURRENT_BRANCH}"
fi

usage() {
    cat <<'EOF'
Usage:
  bash scripts/create_instance.sh --instance-key <key> --connection-arn <arn> [options]

Options:
  --instance-key <key>         Required. Lowercase instance identifier, e.g. market-radar
  --github-owner <owner>       Optional override. Default: current git origin owner or douglas-grishen
  --connection-arn <arn>       Required unless already present in infra/deploy.env
  --aws-profile <profile>      Default: current infra/deploy.env value or "default"
  --aws-region <region>        Default: current infra/deploy.env value or "us-east-1"
  --github-repo <repo>         Default: current git origin repo or self-evolving-software
  --github-branch <branch>     Default: current local git branch or main
  --ssh-cidr <cidr>            Default: 0.0.0.0/0
  --public-host <host>         Default: <instance-key>.local
  --app-name <name>            Default: Managed App
  --init-contracts             Copy contracts.example.yaml into the private overlay
  --deploy                     Run make cdk-deploy after preflight succeeds
  --force                      Overwrite an existing private overlay for the instance
  --help                       Show this message

This script:
  1. Creates a private overlay at instances/<instance-key>/instance.env
  2. Activates the instance in infra/deploy.env
  3. Runs make preflight-instance

It does not create a Purpose. The first Purpose must be defined from the UI
after the instance boots.
EOF
}

upsert_env_line() {
    local file_path="$1"
    local key="$2"
    local value="$3"
    local tmp_file

    mkdir -p "$(dirname "$file_path")"
    touch "$file_path"
    tmp_file="$(mktemp)"
    awk -v key="$key" -v value="$value" '
        BEGIN { updated = 0 }
        $0 ~ ("^[[:space:]]*" key "=") {
            print key "=" value
            updated = 1
            next
        }
        { print }
        END {
            if (!updated) {
                print key "=" value
            }
        }
    ' "$file_path" >"$tmp_file"
    mv "$tmp_file" "$file_path"
}

write_overlay() {
    local overlay_dir="$1"
    local db_name="$2"
    local compose_project="$3"

    mkdir -p "$overlay_dir"
    cat >"${overlay_dir}/instance.env" <<EOF
INSTANCE_KEY=${INSTANCE_KEY}
PUBLIC_HOST=${PUBLIC_HOST}
COMPOSE_PROJECT=${compose_project}
POSTGRES_DB=${db_name}
APP_APP_NAME='${APP_APP_NAME}'
EOF
}

while [ "$#" -gt 0 ]; do
    case "$1" in
        --instance-key)
            INSTANCE_KEY="$2"
            shift 2
            ;;
        --github-owner)
            GITHUB_OWNER="$2"
            GITHUB_OWNER_EXPLICIT=1
            shift 2
            ;;
        --connection-arn)
            CONNECTION_ARN="$2"
            shift 2
            ;;
        --aws-profile)
            AWS_PROFILE="$2"
            shift 2
            ;;
        --aws-region)
            AWS_REGION="$2"
            shift 2
            ;;
        --github-repo)
            GITHUB_REPO="$2"
            GITHUB_REPO_EXPLICIT=1
            shift 2
            ;;
        --github-branch)
            GITHUB_BRANCH="$2"
            GITHUB_BRANCH_EXPLICIT=1
            shift 2
            ;;
        --ssh-cidr)
            SSH_CIDR="$2"
            shift 2
            ;;
        --public-host)
            PUBLIC_HOST="$2"
            shift 2
            ;;
        --app-name)
            APP_APP_NAME="$2"
            shift 2
            ;;
        --init-contracts)
            INIT_CONTRACTS=1
            shift
            ;;
        --deploy)
            RUN_DEPLOY=1
            shift
            ;;
        --force)
            FORCE=1
            shift
            ;;
        --help|-h)
            usage
            exit 0
            ;;
        *)
            echo "Unknown option: $1" >&2
            usage >&2
            exit 1
            ;;
    esac
done

if [ "${GITHUB_OWNER_EXPLICIT}" != "1" ] && [ -n "${ORIGIN_SLUG}" ]; then
    GITHUB_OWNER="${ORIGIN_SLUG%%/*}"
fi

if [ "${GITHUB_REPO_EXPLICIT}" != "1" ] && [ -n "${ORIGIN_SLUG}" ]; then
    GITHUB_REPO="${ORIGIN_SLUG#*/}"
fi

if [ "${GITHUB_BRANCH_EXPLICIT}" != "1" ] && [ -n "${CURRENT_BRANCH}" ]; then
    GITHUB_BRANCH="${CURRENT_BRANCH}"
fi

if [ -z "${INSTANCE_KEY}" ]; then
    echo "Missing required --instance-key." >&2
    exit 1
fi

if [ -z "${CONNECTION_ARN}" ]; then
    echo "Missing required --connection-arn (or set it in infra/deploy.env first)." >&2
    exit 1
fi

if [[ ! "${INSTANCE_KEY}" =~ ^[a-z0-9][a-z0-9-]*$ ]]; then
    echo "INSTANCE_KEY must match ^[a-z0-9][a-z0-9-]*$" >&2
    exit 1
fi

if [ "${INSTANCE_KEY}" = "base" ]; then
    echo "INSTANCE_KEY=base is reserved for disposable/local use. Pick a real instance key." >&2
    exit 1
fi

if [[ "${APP_APP_NAME}" == *"'"* ]]; then
    echo "APP_APP_NAME cannot contain single quotes in this script version." >&2
    exit 1
fi

if [ -z "${PUBLIC_HOST}" ]; then
    PUBLIC_HOST="${INSTANCE_KEY}.local"
fi

OVERLAY_DIR="${REPO_ROOT}/instances/${INSTANCE_KEY}"
COMPOSE_PROJECT="self-evolving-software-${INSTANCE_KEY}"
POSTGRES_DB="ses_${INSTANCE_KEY//-/_}"

if [ -e "${OVERLAY_DIR}" ] && [ "${FORCE}" != "1" ]; then
    echo "Private overlay already exists at ${OVERLAY_DIR}. Re-run with --force to overwrite it." >&2
    exit 1
fi

if [ "${FORCE}" = "1" ]; then
    rm -rf "${OVERLAY_DIR}"
fi

write_overlay "${OVERLAY_DIR}" "${POSTGRES_DB}" "${COMPOSE_PROJECT}"

if [ "${INIT_CONTRACTS}" = "1" ]; then
    cp "${REPO_ROOT}/contracts.example.yaml" "${OVERLAY_DIR}/contracts.yaml"
fi

if [ ! -f "${DEPLOY_ENV_PATH}" ]; then
    cat >"${DEPLOY_ENV_PATH}" <<'EOF'
# Local deployment configuration generated by scripts/create_instance.sh
# This file is gitignored.
EOF
fi

upsert_env_line "${DEPLOY_ENV_PATH}" "AWS_PROFILE" "${AWS_PROFILE}"
upsert_env_line "${DEPLOY_ENV_PATH}" "AWS_REGION" "${AWS_REGION}"
upsert_env_line "${DEPLOY_ENV_PATH}" "INSTANCE_KEY" "${INSTANCE_KEY}"
upsert_env_line "${DEPLOY_ENV_PATH}" "GITHUB_OWNER" "${GITHUB_OWNER}"
upsert_env_line "${DEPLOY_ENV_PATH}" "GITHUB_REPO" "${GITHUB_REPO}"
upsert_env_line "${DEPLOY_ENV_PATH}" "GITHUB_BRANCH" "${GITHUB_BRANCH}"
upsert_env_line "${DEPLOY_ENV_PATH}" "CONNECTION_ARN" "${CONNECTION_ARN}"
upsert_env_line "${DEPLOY_ENV_PATH}" "SSH_CIDR" "${SSH_CIDR}"

echo "Created private overlay at ${OVERLAY_DIR}"
echo "Activated ${INSTANCE_KEY} in ${DEPLOY_ENV_PATH}"
echo "Deploy source set to ${GITHUB_OWNER}/${GITHUB_REPO}@${GITHUB_BRANCH}"

(
    cd "${REPO_ROOT}"
    make preflight-instance
)

cat <<EOF

Instance scaffold complete.
Next steps:
  1. Deploy: make cdk-deploy
  2. Open the instance UI
  3. Define the first Purpose from the Welcome/Purpose screen
EOF

if [ "${RUN_DEPLOY}" = "1" ]; then
    (
        cd "${REPO_ROOT}"
        make cdk-deploy
    )
fi
