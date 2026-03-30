# Contributing to self-evolving-software

Thank you for your interest in contributing! This document describes the process for proposing changes, reporting issues, and submitting pull requests.

## Table of Contents

- [Code of Conduct](#code-of-conduct)
- [Getting Started](#getting-started)
- [How to Contribute](#how-to-contribute)
  - [Reporting Bugs](#reporting-bugs)
  - [Suggesting Features](#suggesting-features)
  - [Submitting Pull Requests](#submitting-pull-requests)
- [Development Workflow](#development-workflow)
- [Commit Message Convention](#commit-message-convention)
- [Code Style](#code-style)

---

## Code of Conduct

This project follows the [Contributor Covenant Code of Conduct](CODE_OF_CONDUCT.md). By participating, you agree to uphold these standards. Please report unacceptable behavior to the maintainers.

---

## Getting Started

1. **Fork** the repository on GitHub.
2. **Clone** your fork locally:
   ```bash
   git clone https://github.com/<your-username>/self-evolving-software.git
   cd self-evolving-software
   ```
3. **Create a branch** for your contribution:
   ```bash
   git checkout -b feat/my-feature
   ```
4. Make your changes, commit, and push.
5. Open a **pull request** against the `main` branch.

---

## How to Contribute

### Reporting Bugs

Before filing a bug, please search [existing issues](https://github.com/douglas-grishen/self-evolving-software/issues) to avoid duplicates.

When opening a bug report, include:
- A clear, descriptive title
- Steps to reproduce the issue
- Expected vs actual behavior
- Environment details (OS, runtime version, etc.)
- Any relevant logs or screenshots

Use the [bug report template](.github/ISSUE_TEMPLATE/bug_report.md).

### Suggesting Features

Feature requests are welcome. Please:
- Check if the idea already exists in [open issues](https://github.com/douglas-grishen/self-evolving-software/issues).
- Open a new issue using the [feature request template](.github/ISSUE_TEMPLATE/feature_request.md).
- Describe the problem you're solving and why this feature would be valuable.

### Submitting Pull Requests

1. Ensure your branch is up to date with `main`:
   ```bash
   git fetch origin
   git rebase origin/main
   ```
2. Run the test suite and verify everything passes before opening a PR.
3. Fill out the [pull request template](.github/PULL_REQUEST_TEMPLATE.md).
4. Link any related issues in the PR description (e.g., `Closes #42`).
5. Be responsive to review feedback.

PRs that add new features should also include:
- Tests covering the new behavior
- Documentation updates if applicable

---

## Development Workflow

Use Python 3.11+ for every Python-facing command in this repository. The
preferred entrypoint is `bash scripts/run-python.sh`, which will fail fast if
your active interpreter is too old. `make setup` bootstraps `./.venv` first,
and subsequent Python commands prefer that virtual environment automatically.

```bash
# Install dependencies
PYTHON=/path/to/python3.11 make setup

# Run tests
PYTHON=/path/to/python3.11 make test

# Run deploy/infrastructure flow tests only
PYTHON=/path/to/python3.11 make test-infra

# Validate deploy inputs before creating a new instance
PYTHON=/path/to/python3.11 make preflight-instance

# Lint and format
PYTHON=/path/to/python3.11 make lint
```

---

## Commit Message Convention

This project follows [Conventional Commits](https://www.conventionalcommits.org/):

```
<type>(<scope>): <short summary>

[optional body]

[optional footer]
```

**Types:**

| Type       | Description                                      |
|------------|--------------------------------------------------|
| `feat`     | A new feature                                    |
| `fix`      | A bug fix                                        |
| `docs`     | Documentation only changes                       |
| `style`    | Formatting, missing semicolons, etc. (no logic)  |
| `refactor` | Code change that is neither a fix nor a feature  |
| `test`     | Adding or updating tests                         |
| `chore`    | Build process, tooling, dependency updates       |

**Examples:**

```
feat(feedback-loop): add reinforcement signal capture
fix(analyzer): handle empty AST edge case
docs: update architecture section in README
```

---

## Code Style

- Keep changes focused and minimal — one concern per PR.
- Write self-documenting code; add comments only where the logic is non-obvious.
- Follow the conventions already present in the codebase.

---

Thank you for helping make this project better!
