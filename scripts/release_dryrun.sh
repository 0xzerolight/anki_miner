#!/usr/bin/env bash
#
# Release dry-run gate.
#
# Dispatches .github/workflows/release.yml against the CURRENT branch, which
# builds the REAL release matrix (PyInstaller + from-source Vulkan wheel +
# Inno/AppImage/deb/tar) and runs the bundle smokes — but by construction creates
# NO tag, NO GitHub Release, and NO PyPI upload (only a v* TAG push does that).
# After a green build it PROVES those negatives (release/ci-gate jobs skipped, no
# Release object, Vulkan smoke actually executed). Exits nonzero on any red.
# Re-run and fix until "RELEASE DRY-RUN GREEN" before cutting a release.
#
# Usage:
#   scripts/release_dryrun.sh [linux-windows|all|linux|windows|macos]
#     default: linux-windows  (cheap; covers 4 of 6 historical failure classes).
#     Run `all` ONCE, green, immediately before tagging — only `all` builds the
#     10x-billed mac legs that prove the mac runner labels still resolve.
#
# Requires: gh authenticated with scopes: actions:write + actions:read +
#           contents:read (classic `repo`). The contents/release scope is needed
#           by the `gh release list` and release-job assertions below.
#
# Preconditions (both must hold, they are DISTINCT):
#   (a) the dispatch-enabled release.yml must be on the DEFAULT branch (main) —
#       GitHub only honors workflow_dispatch for a workflow on the default branch;
#   (b) the branch under test must be PUSHED to origin — `gh workflow run --ref`
#       resolves server-side; a local-only branch 404s.
#
# Overlapping dispatches on the same branch are unsupported (concurrency queues
# them); wait for / cancel a prior run before re-dispatching.

set -euo pipefail

WORKFLOW="release.yml"
PLATFORMS="${1:-linux-windows}"
BRANCH="$(git rev-parse --abbrev-ref HEAD)"

case "$PLATFORMS" in
  linux-windows | all | linux | windows | macos) ;;
  *)
    echo "ERROR: unknown platforms '$PLATFORMS' (use: linux-windows|all|linux|windows|macos)" >&2
    exit 2
    ;;
esac

# Precondition (b): the ref must exist on origin (server-side resolution).
if ! git ls-remote --exit-code --heads origin "$BRANCH" >/dev/null 2>&1; then
  echo "ERROR: branch '$BRANCH' is not on origin. Push it first:" >&2
  echo "    git push -u origin $BRANCH" >&2
  exit 2
fi

echo "==> Release dry-run: workflow=$WORKFLOW branch=$BRANCH platforms=$PLATFORMS"

# Cutoff = newest existing dispatch run id on this branch (monotonic databaseId;
# empty -> 0). The run we create below is the single one with id > CUTOFF.
CUTOFF="$(gh run list --workflow="$WORKFLOW" --event=workflow_dispatch \
  --branch "$BRANCH" --limit 30 --json databaseId --jq '.[0].databaseId // 0')"

# Snapshot the newest release BEFORE dispatch — artifact-level proof that no
# Release object is created by the run (belt-and-suspenders over the job check).
REL_BEFORE="$(gh release list --limit 1 --json tagName --jq '.[0].tagName // ""' 2>/dev/null || echo "")"

# Dispatch. The ONLY declared input is `platforms`; passing any undeclared -f key
# (e.g. dry_run) is HTTP 422 and the run never launches.
if ! gh workflow run "$WORKFLOW" --ref "$BRANCH" -f platforms="$PLATFORMS"; then
  echo "ERROR: dispatch failed (gh workflow run returned nonzero)." >&2
  echo "       Is release.yml on the default branch, and is gh authed with actions:write?" >&2
  exit 2
fi

# Resolve the run just created: the single run with databaseId > CUTOFF, at ANY
# status (a fast setup-failure can complete before the first poll — a
# queued/in_progress filter would miss it and hide a red run).
echo "==> Waiting for the dispatched run to register..."
RUN_ID=""
for _ in $(seq 1 30); do
  RUN_ID="$(gh run list --workflow="$WORKFLOW" --event=workflow_dispatch \
    --branch "$BRANCH" --limit 30 --json databaseId \
    --jq "[.[] | select(.databaseId > ${CUTOFF})] | sort_by(.databaseId) | (.[-1].databaseId // empty)")"
  [ -n "$RUN_ID" ] && break
  sleep 3
done
if [ -z "$RUN_ID" ]; then
  echo "ERROR: dispatched run never registered (cutoff=$CUTOFF)." >&2
  echo "       Inspect: gh run list --workflow=$WORKFLOW --event=workflow_dispatch --branch $BRANCH" >&2
  exit 2
fi
echo "==> Watching run $RUN_ID: $(gh run view "$RUN_ID" --json url --jq .url)"

# Block until the run finishes; --exit-status makes gh exit nonzero on failure
# (works even if the run already completed by the time we attach).
WATCH_RC=0
gh run watch "$RUN_ID" --exit-status --interval 15 || WATCH_RC=$?

if [ "$WATCH_RC" -ne 0 ]; then
  echo "############################################"
  echo "RELEASE DRY-RUN FAILED (run $RUN_ID)"
  echo "############################################"
  gh run view "$RUN_ID" --log-failed || true
  exit 1
fi

# ---------------------------------------------------------------------------
# Build was green. Prove the negatives (a green build is necessary but NOT
# sufficient — a dry-run that silently reached the release path, or silently
# SKIPPED the Vulkan smoke, would also look green).
# ---------------------------------------------------------------------------
echo "==> Build green. Verifying the dry-run created no release path..."

JOBS_JSON="$(gh run view "$RUN_ID" --json jobs,conclusion)"

# (1) release + ci-gate jobs must EXIST and be 'skipped'. An ABSENT job (removed
#     or refactored to a step guard) must FAIL, not vacuously pass an empty select.
assert_job_skipped() {
  local name="$1"
  if ! echo "$JOBS_JSON" | jq -e --arg n "$name" '
        (.jobs | map(select(.name == $n)) | length == 1) and
        (any(.jobs[]; .name == $n and .conclusion == "skipped"))' >/dev/null; then
    echo "ERROR: job '$name' was not present-and-skipped in the dispatch run" >&2
    echo "       (a dry-run must never reach '$name')." >&2
    exit 1
  fi
  echo "    job '$name' present and skipped."
}
assert_job_skipped "release"
assert_job_skipped "ci-gate"

# (2) No GitHub Release object appeared.
REL_AFTER="$(gh release list --limit 1 --json tagName --jq '.[0].tagName // ""' 2>/dev/null || echo "")"
if [ "$REL_AFTER" != "$REL_BEFORE" ]; then
  echo "ERROR: a GitHub Release appeared during the dry-run (before='$REL_BEFORE' after='$REL_AFTER')." >&2
  exit 1
fi
echo "    no GitHub Release created (newest tag unchanged: '${REL_BEFORE:-<none>}')."

# (3) Vulkan smoke ACTUALLY EXECUTED on the linux/windows legs. Defends the
#     historical empty-ternary bug: a green run that SKIPPED the smoke looks
#     identical to one that ran it. bundle_smoke.sh prints
#     BUNDLED_WHISPERCPP_VULKAN_LOADABLE_PASS only on the ran-and-passed path and
#     "SKIP whispercpp-vulkan" only on skip; assert PASS present + SKIP absent,
#     scoped to each built leg (gh run view --log interleaves all legs).
LOG_DIR="$(mktemp -d)"
trap 'rm -rf "$LOG_DIR"' EXIT
gh run view "$RUN_ID" --log >"$LOG_DIR/full.log" 2>/dev/null || true

assert_vulkan_ran() { # $1 = os label present in the leg's job name, e.g. ubuntu-22.04
  local os="$1"
  # Only assert for a build leg that was actually part of this run's selection.
  if ! echo "$JOBS_JSON" | jq -e --arg os "$os" \
    'any(.jobs[]; (.name | startswith("build")) and (.name | contains($os)))' >/dev/null; then
    return 0 # leg not in this selection; nothing to assert
  fi
  local leg
  leg="$(grep -F "$os" "$LOG_DIR/full.log" || true)"
  if echo "$leg" | grep -q "SKIP whispercpp-vulkan"; then
    echo "ERROR: Vulkan smoke was SKIPPED on the '$os' leg (expected to execute)." >&2
    exit 1
  fi
  if ! echo "$leg" | grep -q "BUNDLED_WHISPERCPP_VULKAN_LOADABLE_PASS"; then
    echo "ERROR: Vulkan smoke pass-marker absent for the '$os' leg (smoke may not have executed)." >&2
    exit 1
  fi
  echo "    Vulkan smoke executed+passed on '$os'."
}
assert_vulkan_ran "ubuntu-22.04"
assert_vulkan_ran "windows-latest"

echo "############################################"
echo "RELEASE DRY-RUN GREEN (run $RUN_ID, platforms=$PLATFORMS)"
echo "  build matrix + bundle smokes passed; release + ci-gate jobs skipped;"
echo "  no GitHub Release created; Vulkan smoke verified executed."
echo "############################################"
