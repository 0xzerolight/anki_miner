"""The command line against the real pipeline: a subprocess, a seeded home, a fake Anki.

Marked ``network`` (loopback FakeAnkiConnect), not ``e2e``, so it runs in the
gate like the other FakeAnkiConnect process tests.
"""

from __future__ import annotations

import dataclasses
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from anki_miner.gui.utils.config_manager import GUIConfigManager
from tests.e2e.app_config import build_app_config
from tests.e2e.config import E2EConfig
from tests.e2e.fixtures_dictionary import seed_offline_dict
from tests.e2e.fixtures_media import get_test_video
from tests.e2e.fixtures_subtitle import get_test_srt

pytestmark = pytest.mark.network  # real loopback socket; suppresses the tripwire

REPO_ROOT = Path(__file__).resolve().parents[2]
DECK = E2EConfig().deck_name


def _run(home: Path, *args: str) -> tuple[int, list[dict]]:
    env = {**os.environ, "ANKI_MINER_HOME": str(home), "QT_QPA_PLATFORM": "offscreen"}
    proc = subprocess.run(
        [sys.executable, "-m", "anki_miner", *args],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        timeout=600,
        check=False,
    )
    events = [json.loads(line) for line in proc.stdout.decode("ascii").splitlines()]
    assert events, proc.stderr.decode(errors="replace")
    return proc.returncode, events


@pytest.fixture
def home(isolated_home: Path, fake_anki):
    """A seeded home whose SAVED deck is wrong, so only --deck can make a run succeed."""
    pytest.importorskip("fugashi")
    seed_offline_dict(isolated_home / "dicts")
    config = build_app_config(
        E2EConfig(test_home=isolated_home, ankiconnect_url=fake_anki.url), isolated_home, bypass_known_words=True
    )
    GUIConfigManager.save_config(
        dataclasses.replace(config, anki_deck_name="Deck That Does Not Exist", reading_min_occurrence=1)
    )
    return isolated_home, fake_anki


def test_reading_mines_subtitle_into_fake_anki(home) -> None:
    test_home, fake = home
    code, events = _run(test_home, "mine", "reading", str(get_test_srt()), "--deck", DECK)
    result = events[-1]
    assert code == 0, result
    assert events[0]["event"] == "start" and result["event"] == "result"
    assert result["status"] == "success"
    assert result["cards_created"] > 0
    assert fake.note_count(DECK) == result["cards_created"]
    assert result["items"][0]["note_ids"]


def test_saved_deck_missing_is_setup_error(home) -> None:
    test_home, fake = home
    code, events = _run(test_home, "mine", "reading", str(get_test_srt()))
    assert code == 4 and events[-1]["status"] == "setup_error"
    assert fake.note_count() == 0


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not on PATH")
def test_pairs_mines_video_episode(home) -> None:
    test_home, fake = home
    code, events = _run(
        test_home, "mine", "pairs", "--pair", str(get_test_video()), str(get_test_srt()), "--deck", DECK
    )
    assert code == 0, events[-1]
    assert events[-1]["cards_created"] == fake.note_count(DECK) > 0
    stages = [e for e in events if e["event"] == "stage"]
    assert stages and all(e["item"] == 0 for e in stages)
