.PHONY: help venv setup dev dev-backend dev-engine stop \
        test test-backend test-engine test-infra \
        lint lint-fix \
        build build-backend build-frontend build-engine \
        cdk-bootstrap cdk-synth cdk-diff cdk-deploy preflight-instance \
        evolve evolve-dry evolve-continuous evolve-continuous-dry \
        backup-instance restore-instance \
        clean

SHELL := /bin/bash
REPO_ROOT := $(CURDIR)
PYTHON_RUNNER := $(REPO_ROOT)/scripts/run-python.sh
PYTHON_CMD := bash $(PYTHON_RUNNER)
PIP := $(PYTHON_CMD) -m pip
PYTEST := $(PYTHON_CMD) -m pytest
RUFF := $(PYTHON_CMD) -m ruff

# ─── Deploy configuration ────────────────────────────────────────────────────
# Personal deployment values live in infra/deploy.env (gitignored — never committed).
# Copy infra/deploy.env.example → infra/deploy.env and fill in your values.
# All of these can also be overridden via environment variables on the CLI.
-include infra/deploy.env
export

AWS_PROFILE    ?= default
AWS_REGION     ?= us-east-1
INSTANCE_KEY   ?= base
GITHUB_OWNER   ?= douglas-grishen
GITHUB_REPO    ?= self-evolving-software
GITHUB_BRANCH  ?= main
CONNECTION_ARN ?=
SSH_CIDR       ?= 0.0.0.0/0

# CDK context flags built from the variables above.
# Nothing personal is ever hardcoded in source files.
CDK_CONTEXT = \
  --context instance_key=$(INSTANCE_KEY) \
  --context github_owner=$(GITHUB_OWNER) \
  --context github_repo=$(GITHUB_REPO) \
  --context github_branch=$(GITHUB_BRANCH) \
  --context connection_arn=$(CONNECTION_ARN) \
  --context ssh_cidr=$(SSH_CIDR) \
  --context aws_profile=$(AWS_PROFILE)

# ─── Help ───────────────────────────────────────────────────────────────────
help: ## Show this help message
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-24s\033[0m %s\n", $$1, $$2}'

# ─── Setup ──────────────────────────────────────────────────────────────────
venv: ## Create or refresh the local Python 3.11+ virtual environment
	@if [ ! -x "$(REPO_ROOT)/.venv/bin/python" ]; then \
		echo "==> Creating .venv with Python 3.11+..."; \
		bash $(PYTHON_RUNNER) -m venv "$(REPO_ROOT)/.venv"; \
	fi

setup: venv ## First-time project setup (install all dependencies)
	@echo "==> Installing backend dependencies..."
	cd managed_app/backend && $(PIP) install -e ".[dev]"
	@echo "==> Installing frontend dependencies..."
	cd managed_app/frontend && npm install
	@echo "==> Installing evolving engine dependencies..."
	cd evolving_engine && $(PIP) install -e ".[dev]"
	@echo "==> Installing CDK dependencies..."
	cd infra && $(PIP) install -r requirements.txt
	@echo "==> Setup complete!"

# ─── Development ────────────────────────────────────────────────────────────
dev: ## Start the full development stack (postgres + backend + frontend)
	docker compose up --build

dev-backend: ## Start backend + postgres only
	docker compose up --build postgres backend

dev-engine: ## Start the evolving engine (requires ANTHROPIC_API_KEY)
	docker compose --profile engine up --build engine

stop: ## Stop all running containers
	docker compose --profile engine down

# ─── Testing ────────────────────────────────────────────────────────────────
test: ## Run all test suites
	@echo "==> Running backend tests..."
	cd managed_app/backend && $(PYTEST) tests/ -v
	@echo "==> Running engine tests..."
	cd evolving_engine && $(PYTEST) tests/ -v
	@echo "==> Running infrastructure tests..."
	$(PYTHON_CMD) -m pytest infra/tests -v
	@echo "==> All tests passed!"

test-backend: ## Run backend tests only
	cd managed_app/backend && $(PYTEST) tests/ -v

test-engine: ## Run engine tests only
	cd evolving_engine && $(PYTEST) tests/ -v

test-infra: ## Run infrastructure and deploy-flow tests only
	$(PYTHON_CMD) -m pytest infra/tests -v

# ─── Linting ────────────────────────────────────────────────────────────────
lint: ## Run linters on all Python code
	@echo "==> Linting backend..."
	cd managed_app/backend && $(RUFF) check app/ tests/
	@echo "==> Linting engine..."
	cd evolving_engine && $(RUFF) check engine/ tests/
	@echo "==> All clean!"

lint-fix: ## Auto-fix linting issues
	cd managed_app/backend && $(RUFF) check --fix app/ tests/
	cd evolving_engine && $(RUFF) check --fix engine/ tests/

# ─── Build ──────────────────────────────────────────────────────────────────
build: ## Build all Docker images
	docker compose build

build-backend: ## Build backend image only
	docker build -t managed-app-backend managed_app/backend

build-frontend: ## Build frontend image only
	docker build -t managed-app-frontend managed_app/frontend

build-engine: ## Build engine image only
	docker build -t evolving-engine evolving_engine

# ─── Infrastructure (AWS CDK) ────────────────────────────────────────────────
# All commands use --profile $(AWS_PROFILE) (reads from infra/deploy.env).
# All personal values come from CDK_CONTEXT, never from source files.
#
# First-time setup:
#   1. cp infra/deploy.env.example infra/deploy.env   (fill in your values)
#   2. make cdk-bootstrap
#   3. make cdk-deploy

cdk-bootstrap: ## Bootstrap CDK toolkit in your AWS account (first time only)
	@echo "==> Bootstrapping CDK with profile '$(AWS_PROFILE)' in region '$(AWS_REGION)'..."
	cd infra && cdk bootstrap \
	  --profile $(AWS_PROFILE) \
	  aws://$(shell aws sts get-caller-identity --profile $(AWS_PROFILE) --query Account --output text)/$(AWS_REGION)

cdk-synth: ## Synthesize CloudFormation templates (no AWS calls)
	cd infra && cdk synth --profile $(AWS_PROFILE) $(CDK_CONTEXT)

cdk-diff: ## Show pending infrastructure changes
	cd infra && cdk diff --profile $(AWS_PROFILE) $(CDK_CONTEXT)

cdk-deploy: preflight-instance ## Deploy all stacks to AWS
	@echo "==> Deploying to AWS (profile: $(AWS_PROFILE), region: $(AWS_REGION))..."
	@[ -n "$(GITHUB_OWNER)" ] || (echo "ERROR: GITHUB_OWNER is not set. Edit infra/deploy.env."; exit 1)
	@[ -n "$(CONNECTION_ARN)" ] || (echo "ERROR: CONNECTION_ARN is not set. Edit infra/deploy.env."; exit 1)
	cd infra && cdk deploy --all \
	  --profile $(AWS_PROFILE) \
	  $(CDK_CONTEXT) \
	  --require-approval broadening

preflight-instance: ## Validate deploy inputs and tracked seeds before creating a new instance
	$(PYTHON_CMD) scripts/preflight_instance.py

cdk-destroy: ## Destroy all stacks (WARNING: deletes all cloud resources)
	cd infra && cdk destroy --all \
	  --profile $(AWS_PROFILE) \
	  $(CDK_CONTEXT) \
	  --force

# ─── Engine CLI ─────────────────────────────────────────────────────────────
evolve: ## Triggered mode — single evolution (usage: make evolve REQ="Add products CRUD")
	cd evolving_engine && $(PYTHON_CMD) -m engine "$(REQ)"

evolve-dry: ## Triggered mode — dry run (usage: make evolve-dry REQ="Add products CRUD")
	cd evolving_engine && $(PYTHON_CMD) -m engine --dry-run "$(REQ)"

evolve-continuous: ## Continuous mode — autonomous MAPE-K loop (Ctrl+C to stop)
	cd evolving_engine && $(PYTHON_CMD) -m engine --continuous

evolve-continuous-dry: ## Continuous mode — observe and plan but never deploy
	cd evolving_engine && $(PYTHON_CMD) -m engine --continuous --dry-run

# ─── Instance Operations ────────────────────────────────────────────────────
backup-instance: ## Create a backup bundle of the live instance state (set BACKUP_DIR=... to override)
	bash scripts/backup_instance.sh $(BACKUP_DIR)

restore-instance: ## Restore a backup bundle into the live instance (usage: make restore-instance BACKUP_DIR=/path/to/backup FORCE=1)
	@[ -n "$(BACKUP_DIR)" ] || (echo "ERROR: BACKUP_DIR is required."; exit 1)
	@[ "$(FORCE)" = "1" ] || (echo "ERROR: Set FORCE=1 to acknowledge destructive restore."; exit 1)
	FORCE=1 bash scripts/restore_instance.sh "$(BACKUP_DIR)"

# ─── Cleanup ────────────────────────────────────────────────────────────────
clean: ## Remove build artifacts, caches, and temp files
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name .pytest_cache -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name .ruff_cache -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name node_modules -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name dist -exec rm -rf {} + 2>/dev/null || true
	rm -rf managed_app/frontend/dist
	docker compose --profile engine down -v --remove-orphans 2>/dev/null || true
	@echo "==> Clean!"
