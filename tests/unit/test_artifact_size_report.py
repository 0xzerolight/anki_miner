"""The release-artifact size gate's pure comparison half."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "artifact_size_report.py"
_spec = importlib.util.spec_from_file_location("artifact_size_report", _SCRIPT)
assert _spec is not None and _spec.loader is not None
sizes = importlib.util.module_from_spec(_spec)
sys.modules["artifact_size_report"] = sizes
_spec.loader.exec_module(sizes)


def test_growth_within_tolerance_reports_but_does_not_fail():
    lines, failures = sizes.compare({"AnkiMiner-Linux-x86_64": 105}, {"AnkiMiner-Linux-x86_64": 100}, 10.0)
    assert failures == []
    assert any("AnkiMiner-Linux-x86_64" in line for line in lines)


def test_growth_past_tolerance_fails_and_names_the_artifact():
    lines, failures = sizes.compare({"AnkiMiner-Linux-x86_64": 130}, {"AnkiMiner-Linux-x86_64": 100}, 10.0)
    assert len(failures) == 1
    assert "AnkiMiner-Linux-x86_64" in failures[0]
    assert lines


def test_a_new_artifact_is_reported_not_failed():
    lines, failures = sizes.compare({"AnkiMiner-macOS-arm64": 10}, {}, 10.0)
    assert failures == []
    assert any("new" in line for line in lines)


def test_a_missing_artifact_fails():
    _lines, failures = sizes.compare({}, {"AnkiMiner-Windows-x86_64": 100}, 10.0)
    assert len(failures) == 1
    assert "AnkiMiner-Windows-x86_64" in failures[0]


def test_an_unselected_leg_is_reported_not_failed():
    """A linux-windows dry run builds no mac leg; the baseline still lists it."""
    baseline = {"AnkiMiner-Linux-x86_64": 100, "AnkiMiner-macOS-arm64": 100}
    lines, failures = sizes.compare({"AnkiMiner-Linux-x86_64": 100}, baseline, 10.0, {"AnkiMiner-Linux-x86_64"})
    assert failures == []
    assert any("AnkiMiner-macOS-arm64" in line and "not built" in line for line in lines)


def test_a_selected_leg_with_no_artifact_still_fails():
    _lines, failures = sizes.compare({}, {"AnkiMiner-Windows-x86_64": 100}, 10.0, {"AnkiMiner-Windows-x86_64"})
    assert len(failures) == 1
    assert "AnkiMiner-Windows-x86_64" in failures[0]


def test_built_artifacts_reads_the_matrix_job_names():
    jobs = [
        "setup",
        "ci-gate",
        "build (ubuntu-22.04, AnkiMiner-Linux-x86_64, Linux-x86_64, .[asr], 1)",
        "build (windows-latest, AnkiMiner-Windows-x86_64, Windows-x86_64, .[asr], 1)",
        "release",
    ]
    names = ["AnkiMiner-Linux-x86_64", "AnkiMiner-Windows-x86_64", "AnkiMiner-macOS-arm64", "AnkiMiner-macOS-x86_64"]
    assert sizes.built_artifacts(jobs, names) == {"AnkiMiner-Linux-x86_64", "AnkiMiner-Windows-x86_64"}


def test_built_artifacts_reads_a_name_github_cut_short():
    jobs = ["build (macos-15-intel, AnkiMiner-macOS-x86_64, macOS-x86_64, ., 1, libmp3lame libopus libwebp lib..."]
    assert sizes.built_artifacts(jobs, ["AnkiMiner-macOS-x86_64", "AnkiMiner-macOS-arm64"]) == {
        "AnkiMiner-macOS-x86_64"
    }
