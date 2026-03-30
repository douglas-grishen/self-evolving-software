#!/usr/bin/env bash

set -euo pipefail

version_ok() {
  local candidate="$1"
  "$candidate" - <<'PY' >/dev/null 2>&1
import sys
raise SystemExit(0 if sys.version_info >= (3, 11) else 1)
PY
}

pick_python() {
  local candidate
  local script_dir
  local repo_root

  script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
  repo_root="$(cd "${script_dir}/.." && pwd)"

  if [[ -n "${PYTHON:-}" ]]; then
    if command -v "$PYTHON" >/dev/null 2>&1 && version_ok "$PYTHON"; then
      printf '%s\n' "$PYTHON"
      return 0
    fi
    echo "Configured PYTHON='$PYTHON' is not an available Python 3.11+ interpreter." >&2
    return 1
  fi

  if [[ -x "${repo_root}/.venv/bin/python" ]] && version_ok "${repo_root}/.venv/bin/python"; then
    printf '%s\n' "${repo_root}/.venv/bin/python"
    return 0
  fi

  for candidate in python3.11 python3 python; do
    if command -v "$candidate" >/dev/null 2>&1 && version_ok "$candidate"; then
      printf '%s\n' "$candidate"
      return 0
    fi
  done

  cat >&2 <<'EOF'
Python 3.11+ is required for this repository.

Install python3.11 or run commands with an explicit interpreter, for example:
  PYTHON=/path/to/python3.11 make setup
  PYTHON=/path/to/python3.11 make test-engine
EOF
  return 1
}

main() {
  local interpreter
  interpreter="$(pick_python)"
  exec "$interpreter" "$@"
}

main "$@"
