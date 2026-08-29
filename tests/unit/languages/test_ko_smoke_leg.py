"""The ko leg rides Stage 2B's opt-in BUNDLE_SMOKE_LANGS loop; no ko-only leg."""

from pathlib import Path

import pytest

from anki_miner.gui import app as app_module

ROOT = Path(__file__).resolve().parents[3]


def test_the_shared_loop_is_opt_in_and_language_generic() -> None:
    text = (ROOT / "scripts" / "bundle_smoke.sh").read_text(encoding="utf-8")
    assert "BUNDLE_SMOKE_LANGS" in text
    assert 'ANKI_MINER_SMOKE="$lang"' in text
    # A per-language leg would re-break the invocation-count test 2B.10 protected.
    assert "ANKI_MINER_SMOKE=ko" not in text


def test_the_release_workflow_requests_the_ko_leg() -> None:
    workflow = (ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
    requested = [line.split(":", 1)[1].split() for line in workflow.splitlines() if "BUNDLE_SMOKE_LANGS:" in line]
    assert requested, "release.yml must request the language smoke legs"
    assert any("ko" in group for group in requested)


def test_ko_has_a_bundled_smoke_line() -> None:
    assert "ko" in app_module._LANGUAGE_SMOKE_LINES


def test_the_ko_leg_passes_in_process(capsys) -> None:
    pytest.importorskip("kiwipiepy")
    assert app_module._run_language_bundled_smoke("ko") == 0
    assert "BUNDLED_SMOKE_PASS" in capsys.readouterr().out
