#!/usr/bin/env bash
# Full health stack — run before claiming any task done (see CLAUDE.md "Definition of Done").
# Runs every check, never stops on first failure, prints a summary, exits nonzero if any
# HARD step failed. Hard steps gate "done": black, ruff, mypy, pytest. Tolerant steps
# (vulture, shellcheck) only warn if their binary is missing.
set -uo pipefail

cd "$(dirname "$0")/.." || exit 2

# Prefer the project venv binaries; fall back to PATH.
BIN=""
[ -x ".venv/bin/python" ] && BIN=".venv/bin/"

failed=()
skipped=()

run_hard() {  # name, cmd...
  local name="$1"; shift
  echo "=== ${name} ==="
  if "$@"; then
    echo "PASS ${name}"
  else
    echo "FAIL ${name}"
    failed+=("${name}")
  fi
  echo
}

run_tolerant() {  # name, binary, cmd...
  local name="$1" bin="$2"; shift 2
  echo "=== ${name} ==="
  if ! command -v "${bin}" >/dev/null 2>&1 && [ ! -x ".venv/bin/${bin}" ]; then
    echo "SKIP ${name} (${bin} not found)"
    skipped+=("${name}")
    echo
    return
  fi
  if "$@"; then
    echo "PASS ${name}"
  else
    echo "FAIL ${name}"
    failed+=("${name}")
  fi
  echo
}

run_hard     "black"      "${BIN}black" --check .
run_hard     "ruff"       "${BIN}ruff" check .
run_hard     "mypy"       "${BIN}mypy" anki_miner
run_hard     "pytest"     "${BIN}pytest" -m "not youtube"
run_tolerant "vulture"    "vulture" "${BIN}vulture"
run_tolerant "shellcheck" "shellcheck" shellcheck packaging/appimage/build-appimage.sh

echo "================ SUMMARY ================"
[ ${#skipped[@]} -gt 0 ] && echo "skipped: ${skipped[*]}"
if [ ${#failed[@]} -gt 0 ]; then
  echo "FAILED:  ${failed[*]}"
  exit 1
fi
echo "all green"
