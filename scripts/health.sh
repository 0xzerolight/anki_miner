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
# "and not e2e" is load-bearing: a CLI -m REPLACES the addopts -m "not e2e",
# so omitting it here re-included the explicit-activation-only e2e tests,
# which need a live Anki and mutate a real (test) deck.
run_hard     "pytest"     "${BIN}pytest" -m "not youtube and not asr and not e2e and not golden"
# Mirrors CI's dedicated `test-asr` job (ci.yml): the asr-marked suite runs
# separately from the main pytest step above, which excludes it. Needs the
# [asr]/[asr-vulkan] extras in the venv; without them the asr tests skip via
# their import seam rather than error, so this stays green either way. "and not
# e2e" is load-bearing for the same reason as the main step (a CLI -m REPLACES
# addopts' -m "not e2e").
run_hard     "pytest-asr" "${BIN}pytest" -m "asr and not e2e"
run_tolerant "vulture"    "vulture" "${BIN}vulture"
run_tolerant "shellcheck" "shellcheck" shellcheck packaging/appimage/build-appimage.sh scripts/bundle_smoke.sh scripts/release_preflight.sh scripts/release_dryrun.sh

echo "================ SUMMARY ================"
[ ${#skipped[@]} -gt 0 ] && echo "skipped: ${skipped[*]}"
if [ ${#failed[@]} -gt 0 ]; then
  echo "FAILED:  ${failed[*]}"
  exit 1
fi
echo "all green"
