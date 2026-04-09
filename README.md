# self-evolving-software

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Contributions Welcome](https://img.shields.io/badge/contributions-welcome-brightgreen.svg)](CONTRIBUTING.md)
[![Code of Conduct](https://img.shields.io/badge/code%20of%20conduct-contributor%20covenant-ff69b4.svg)](CODE_OF_CONDUCT.md)

An experimental framework for building software systems that can analyze their own behavior, generate improvements, and iteratively modify their architecture through AI-driven feedback loops — enabling continuous adaptation, optimization, and autonomous evolution.

> **Status:** Early-stage / experimental. APIs and architecture are subject to change.

---

## Table of Contents

- [Overview](#overview)
- [Architecture](#architecture)
- [Project Structure](#project-structure)
- [Getting Started](#getting-started)
- [Usage](#usage)
- [How It Works](#how-it-works)
- [Infrastructure](#infrastructure)
- [Contributing](#contributing)
- [Security](#security)
- [License](#license)

---

## Overview

This project implements a **MAPE-K** (Monitor, Analyze, Plan, Execute, Knowledge) architecture with a strict separation of concerns between two planes:

1. **Operational Plane** — The product runtime in `managed_app/`: a standard web application built with React, FastAPI, and PostgreSQL
2. **Evolution Plane** — The adaptive control system in `evolving_engine/`: a multi-agent AI engine that analyzes the runtime and codebase, generates improvements, validates them in a sandbox, and deploys them autonomously

The engine acts as an autonomous development team: it reads your requirements, understands the codebase, writes the code, tests it, and ships it.

---

## Architecture

This system implements the **MAPE-K** pattern inside the **Evolution Plane**. The Evolution Plane observes the **Operational Plane** at runtime, detects anomalies, generates fixes, validates them in a sandbox, and deploys them continuously.

```
  User request (optional)              Runtime anomaly (autonomous)
          │                                      │
          └──────────────────┬───────────────────┘
                             ▼
┌────────────────────────────────────────────────────────────────────┐
│  EVOLUTION PLANE                 (evolution-plane Docker network)   │
│                                                                    │
│  ┌─────────────────────────────────────────────────────────────┐  │
│  │  Continuous MAPE-K loop                                     │  │
│  │                                                             │  │
│  │  ┌──────────┐  ┌─────────────┐  ┌──────────┐               │  │
│  │  │ MONITOR  │→ │   ANALYZE   │→ │   PLAN   │               │  │
│  │  │Observer  │  │ DataManager │  │  Leader  │               │  │
│  │  └────┬─────┘  └─────────────┘  └────┬─────┘               │  │
│  │       │ (polls every N sec)           │                     │  │
│  │       │ via control-plane             ▼                     │  │
│  │       │                       ┌──────────────┐              │  │
│  │       │                 ┌────>│   EXECUTE    │              │  │
│  │       │                 │     │  Generator   │              │  │
│  │       │              retry    └──────┬───────┘              │  │
│  │       │             (max 3)          ▼                      │  │
│  │       │                       ┌──────────────┐              │  │
│  │       │              FAIL ────│  KNOWLEDGE   │──── PASS     │  │
│  │       │                       │  Validator   │         │    │  │
│  │       │                       └──────────────┘         │    │  │
│  │       │                                                ▼    │  │
│  │       │                                           Deployer  │  │
│  └───────┼────────────────────────────────────────────────────-┘  │
│          │  Self-modification: engine can evolve its own code      │
│          │  Interfaces: file system · Docker socket · Git · Bedrock│
└──────────┼─────────────────────────────────────────────────────────┘
           │  control-plane Docker network
           │  GET /api/v1/monitor/{health,metrics,errors,schema}
┌──────────┼─────────────────────────────────────────────────────────┐
│  OPERATIONAL PLANE              (operational-plane Docker network)  │
│                                                                    │
│  ┌────────────┐  ┌────────────────────────┐  ┌─────────────────┐  │
│  │ PostgreSQL │↔ │ Backend  (FastAPI)      │↔ │ Frontend        │──┼──► Users
│  │  (EBS)     │  │ /api/v1/*              │  │ Nginx + React   │  │
│  │            │  │ /api/v1/monitor/*  ◄───┼──┘ proxy :80       │  │
│  └────────────┘  └────────────────────────┘  └─────────────────┘  │
└────────────────────────────────────────────────────────────────────┘
```

### Two operating modes

| Mode | Command | Description |
|------|---------|-------------|
| **Triggered** | `python -m engine "..."` | Single evolution from a user request |
| **Continuous** | `python -m engine --continuous` | Autonomous loop: monitor → detect → evolve |

### Agent Responsibilities

| Component | Role | MAPE-K Phase |
|-----------|------|-------------|
| **RuntimeObserver** | Polls health, metrics, errors, schema via control-plane every N seconds | **M**onitor |
| **DataManager** | Scans repo, builds token-efficient JSON map of both codebases | **A**nalyze |
| **Leader** | Converts request + runtime evidence into a structured evolution plan | **P**lan |
| **Generator** | Calls Claude/Bedrock API to generate code (SQL, Python, TypeScript) | **E**xecute |
| **Validator** | Runs 3-stage Docker sandbox (lint + build + tests), scores risk | **E**xecute + **K**nowledge |

### Self-modification

The engine mounts the **full repository** and can write to both `managed_app/` and `evolving_engine/`. When the Leader determines the engine itself needs improvement (better prompts, new analysis heuristics, improved validation strategies), it targets `evolving_engine/` and the same pipeline applies.

### Network topology

| Network | Connected services | Purpose |
|---------|-------------------|---------|
| `operational-plane` | postgres ↔ backend ↔ frontend | Operational Plane traffic |
| `control-plane` | backend ↔ engine | Evolution Plane observation of runtime contracts |
| `evolution-plane` | engine | Evolution Plane internals: LLM calls, Docker sandbox, Git, AWS APIs |

During rollout the runtime observer still recognizes legacy subsystem labels (`managed-system`, `autonomic-manager`), but current deployments use plane-oriented names.

---

## Project Structure

```
self-evolving-software/
│
│── managed_app/                    # ── OPERATIONAL PLANE ───────────────────
│   ├── frontend/                   # React + TypeScript (Vite), Nginx
│   ├── backend/                    # FastAPI + SQLAlchemy + Alembic
│   └── docker-compose.yml          # Standalone dev compose
│
│── evolving_engine/                # ── EVOLUTION PLANE ─────────────────────
│   ├── engine/
│   │   ├── agents/                 # Leader, DataManager, Generator, Validator
│   │   ├── providers/              # Anthropic Claude, Amazon Bedrock
│   │   ├── sandbox/                # Docker sandbox, CodeBuild sandbox
│   │   ├── repo/                   # Repository scanner + map builder
│   │   ├── deployer/               # Git ops + CI/CD pipeline trigger
│   │   ├── models/                 # Pydantic models (context, repo map)
│   │   ├── orchestrator.py         # State machine driving the MAPE-K loop
│   │   └── config.py               # Engine settings
│   └── tests/
│
│── infra/                          # ── AWS CDK INFRASTRUCTURE ──────────────
│   ├── stacks/                     # Network, EC2, Pipeline
│   └── app.py                      # CDK entrypoint
│
│── deploy/                         # ── CODEDEPLOY HOOKS ────────────────────
│   ├── appspec.yml                 # CodeDeploy application spec
│   └── scripts/                    # stop.sh, install.sh, start.sh
│
├── framework_invariants.yaml       # Shared platform/safety rules for all instances
├── contracts.example.yaml          # Example runtime contracts for mounted apps
├── instances/                      # Optional private overlays (kept generic in OSS)
├── docker-compose.yml              # Local dev (both subsystems, separate nets)
├── docker-compose.prod.yml         # Production (EC2, MAPE-K network isolation)
├── Makefile                        # Common commands
└── .env.example                    # Environment template
```

---

## Getting Started

### Prerequisites

- Python 3.11+
- Node.js 22+
- Docker and Docker Compose
- An Anthropic API key ([console.anthropic.com](https://console.anthropic.com))

The repository resolves Python through `scripts/run-python.sh` and prefers `./.venv/bin/python` when that virtual environment exists. If your default `python` is older, set `PYTHON` explicitly:

```bash
PYTHON=/path/to/python3.11 make setup
```

### Quick Start

```bash
# Clone the repository
git clone https://github.com/douglas-grishen/self-evolving-software.git
cd self-evolving-software

# Copy and configure environment variables
cp .env.example .env
# Edit .env and add your ENGINE_ANTHROPIC_API_KEY

# Create the local .venv and install all dependencies
make setup

# Start the development stack
make dev
```

The managed app will be available at:
- **Frontend:** http://localhost:5173
- **Backend:** http://localhost:8000
- **API docs:** http://localhost:8000/docs

For production-style deployments and new instances, run the preflight first:

```bash
make preflight-instance
```

### Running the Engine

```bash
# Dry run (validate without deploying)
make evolve-dry REQ="Add a products table with name, price, and description"

# Full evolution (validate + deploy)
make evolve REQ="Add a products CRUD with API endpoints and React component"
```

---

## Usage

### CLI — Triggered mode (single evolution)

```bash
cd evolving_engine

# Dry run — generates and validates code, skips deployment
bash ../scripts/run-python.sh -m engine --dry-run "Add user authentication with JWT tokens"

# Full run — generates, validates, commits, and triggers pipeline
bash ../scripts/run-python.sh -m engine "Add a /api/v1/products endpoint with CRUD operations"
```

### CLI — Continuous mode (autonomous MAPE-K loop)

```bash
cd evolving_engine

# Start the autonomous loop (polls every 60s by default)
bash ../scripts/run-python.sh -m engine --continuous

# Override the monitoring interval
bash ../scripts/run-python.sh -m engine --continuous --interval 30

# Observe and plan but never deploy (useful for testing)
bash ../scripts/run-python.sh -m engine --continuous --dry-run
```

In continuous mode the engine runs indefinitely. Each iteration it polls the Operational Plane, evaluates runtime health, detects anomalies, and, if something needs fixing, autonomously generates, validates, and deploys the solution.

### Makefile shortcuts

```bash
# Triggered
make evolve-dry REQ="Add a products table with name, price, and description"
make evolve     REQ="Add a products CRUD with API endpoints and React component"

# Continuous
make evolve-continuous
```

### Programmatic

```python
import asyncio
from engine.orchestrator import Orchestrator
from engine.models.evolution import EvolutionSource

async def main():
    orchestrator = Orchestrator()

    # Triggered mode
    ctx = await orchestrator.run(
        "Add a products table with name, price, and stock quantity",
        dry_run=True,
    )
    print(f"Status: {ctx.status.value}")

    # Continuous mode (blocks until stopped)
    await orchestrator.run_continuous()

asyncio.run(main())
```

---

## How It Works

### Evolution Pipeline (State Machine)

```
RECEIVED → Leader → ANALYZING → DataManager → GENERATING → Generator →
VALIDATING → Validator → DEPLOYING → Deployer → COMPLETED
```

1. **RECEIVED** — A request arrives (from user or from the runtime monitor)
2. **Leader Agent** — Interprets the request + runtime evidence, produces a structured `EvolutionPlan`
3. **Data Manager Agent** — Scans the target codebase (`managed_app/` or `evolving_engine/`) and builds a `RepoMap`
4. **Code Generator Agent** — Calls Claude/Bedrock with the plan + repo map + any error feedback, generates source files
5. **Code Validator Agent** — Copies files to a Docker sandbox, runs linting + build + tests
6. **Deployer** — Commits to a feature branch, pushes, and triggers AWS CodePipeline

If validation fails, the pipeline retries up to 3 times, feeding sandbox error logs back to the Generator.

### Continuous MAPE-K Loop

```
while running:
    snapshot  = observer.observe()         # MONITOR  — poll health/metrics/errors
    anomalies = detect_anomalies(snapshot) # ANALYZE  — is something wrong?
    for anomaly in anomalies:
        plan = leader.plan(anomaly)        # PLAN     — what should change?
        code = generator.generate(plan)    # EXECUTE  — write the code
        ok   = validator.validate(code)    # VALIDATE — does it work?
        if ok: deployer.deploy(code)       # DEPLOY   — ship it
    sleep(interval)                        # KNOWLEDGE — repeat and learn
```

### Sandbox Validation (3 Stages)

| Stage | What it does | Risk weight |
|-------|-------------|-------------|
| Static Analysis | `ruff check` (Python), `tsc --noEmit` (TS) | +0.2 |
| Build Test | `docker build` each modified service | +0.5 |
| Integration Test | Full stack up + `pytest` + HTTP health checks | +0.3 |

---

## Infrastructure

Production runs on a **single EC2 instance** via Docker Compose. Both planes are deployed as containers on isolated Docker networks.

The open-source baseline ships shared [framework_invariants.yaml](framework_invariants.yaml),
[genesis.yaml](genesis.yaml), and [contracts.example.yaml](contracts.example.yaml).
New instances do not receive a tracked business Purpose at deploy time; the first
Purpose must be defined from the UI after bootstrap.

The `instances/` folder is reserved for optional private overlays and should remain generic in the open-source repo. If you keep a local overlay under `instances/<instance_key>/`, it can define instance naming, public host, Genesis overrides, runtime contracts, and optional bootstrap files for the instance-local operational plane.

Recommended hardening flow before a real instance:

```bash
bash scripts/create_instance.sh --instance-key <new-key> --connection-arn <arn>
make preflight-instance
make test-infra
```

`create_instance.sh` defaults to the current git `origin` and current local
branch when it writes `infra/deploy.env`, and the EC2 bootstrap now waits for
CodeDeploy before any framework code is promoted onto the host. That prevents a
new instance from starting on stale code that lives only in `main` or in an
older bootstrap checkout.

Operational guidance for contracts, backups/restores, and external alerts lives
in [docs/instance-operations.md](docs/instance-operations.md).

| Stack | Resources |
|-------|-----------|
| **NetworkStack** | VPC (1 AZ, public subnet), security group (HTTP/HTTPS/SSH) |
| **Ec2Stack** | EC2 t3.medium, Elastic IP, EBS GP3 (pgdata), IAM role, CodeDeploy |
| **PipelineStack** | CodePipeline: GitHub Source → CodeDeploy to EC2 |

```
Deployment flow:
  GitHub push → CodePipeline → CodeDeploy → EC2
                                  ├── stop.sh:    docker compose down
                                  ├── install.sh: docker compose build
                                  └── start.sh:   docker compose up -d
```

```bash
# Synthesize CloudFormation templates
make cdk-synth

# Deploy to AWS
make cdk-deploy
```

---

## Contributing

Contributions are welcome! Please read [CONTRIBUTING.md](CONTRIBUTING.md) for details on the development process, commit conventions, and how to submit pull requests.

For questions or discussion, open an [issue](https://github.com/douglas-grishen/self-evolving-software/issues).

---

## Security

If you discover a security vulnerability, please follow the responsible disclosure process in [SECURITY.md](SECURITY.md). Do **not** open a public issue.

---

## License

This project is licensed under the [MIT License](LICENSE).

Copyright (c) 2026 Douglas Rodriguez
