#!/usr/bin/env python3
"""Release-artifact size report and gate.

Bundling a second and third tokenizer is the change most likely to make the
installers unacceptable, and artifact size is invisible until a user downloads
one. This reads the sizes GitHub already recorded for a run's artifacts (no
download), prints them beside a committed baseline, and fails when one grew
past the tolerance. Stage 2 writes the baseline; Stage 3 is gated by it.

Usage:
  artifact_size_report.py --run-id <id> [--repo owner/name] [--write-baseline]
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
BASELINE = REPO_ROOT / "scripts" / "artifact_size_baseline.json"
#: Percent an artifact may grow over the baseline before this fails. Generous
#: on purpose: it catches a bundled model, not a dependency patch release.
TOLERANCE_PCT = 10.0


def compare(observed: dict[str, int], baseline: dict[str, int], tolerance_pct: float) -> tuple[list[str], list[str]]:
    """Return (report_lines, failures). An unknown artifact is news, not a fault."""
    lines: list[str] = []
    failures: list[str] = []
    for name in sorted(set(observed) | set(baseline)):
        now = observed.get(name)
        was = baseline.get(name)
        if now is None:
            failures.append(f"{name}: absent from this run (baseline {was} bytes)")
            continue
        if was is None:
            lines.append(f"{name}: {now} bytes (new — not in the baseline)")
            continue
        delta = (now - was) / was * 100 if was else 0.0
        lines.append(f"{name}: {now} bytes ({delta:+.1f}% vs {was})")
        if delta > tolerance_pct:
            failures.append(f"{name}: grew {delta:+.1f}% (tolerance {tolerance_pct:.1f}%)")
    return lines, failures


def fetch_sizes(repo: str, run_id: str) -> dict[str, int]:
    """Artifact name → size in bytes, from the run's metadata. Nothing is downloaded.

    ``--jq`` rather than json.loads over the raw body: with ``--paginate`` gh
    concatenates one JSON document per page, which is not parseable as a whole.
    """
    raw = subprocess.run(
        [
            "gh",
            "api",
            "--paginate",
            f"repos/{repo}/actions/runs/{run_id}/artifacts",
            "--jq",
            ".artifacts[] | [.name, .size_in_bytes] | @tsv",
        ],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    sizes: dict[str, int] = {}
    for row in raw.splitlines():
        if not row.strip():
            continue
        name, _tab, size = row.partition("\t")
        # Log bundles are not shipped to users and vary run to run.
        if name.endswith("-installer-smoke-logs"):
            continue
        sizes[name] = int(size)
    return sizes


def main(argv: list[str] | None = None) -> int:
    import json

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--repo", default="0xzerolight/anki_miner")
    parser.add_argument("--write-baseline", action="store_true")
    args = parser.parse_args(argv)

    observed = fetch_sizes(args.repo, args.run_id)
    if not observed:
        print("ERROR: no artifacts found for that run", file=sys.stderr)
        return 1
    if args.write_baseline:
        BASELINE.write_text(json.dumps(dict(sorted(observed.items())), indent=2) + "\n", encoding="utf-8")
        print(f"Baseline written: {BASELINE}")
        return 0

    baseline = json.loads(BASELINE.read_text(encoding="utf-8")) if BASELINE.exists() else {}
    lines, failures = compare(observed, baseline, TOLERANCE_PCT)
    for line in lines:
        print(f"  {line}")
    if not baseline:
        print("ARTIFACT_SIZE_REPORT_OK (no baseline yet — run with --write-baseline)")
        return 0
    for failure in failures:
        print(f"ERROR: {failure}", file=sys.stderr)
    if failures:
        return 1
    print("ARTIFACT_SIZE_REPORT_OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
