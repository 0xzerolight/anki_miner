"""The --api mine call against the real pipeline: a subprocess, a seeded home, a fake Anki, real ffmpeg.

Marked ``network`` (loopback FakeAnkiConnect), not ``e2e``, so it runs in the
gate like tests/e2e/test_cli_mine.py.
"""

from __future__ import annotations

import dataclasses
import html
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from anki_miner.gui.utils.config_manager import GUIConfigManager
from anki_miner.gui.utils.service_factory import resolve_known_words_db_path
from anki_miner.utils.audio_track_detector import get_media_duration_seconds
from tests.e2e.app_config import build_app_config
from tests.e2e.config import E2EConfig
from tests.e2e.fixtures_dictionary import seed_offline_dict
from tests.e2e.fixtures_media import get_test_video

pytestmark = [
    pytest.mark.network,  # real loopback socket; suppresses the tripwire
    pytest.mark.skipif(
        shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None, reason="ffmpeg/ffprobe not on PATH"
    ),
]

REPO_ROOT = Path(__file__).resolve().parents[2]
DECK = E2EConfig().deck_name

#: The e2e home maps only word->Front and definition->Back (app_config._basic_note_fields);
#: the API run maps these too through its overlay, merged per key, so the sentence can be
#: read back and the picture/audio cuts (and media_missing) run for real.
_EXTRA_FIELDS = {"sentence": "Sentence", "picture": "Picture", "audio": "Audio"}


def _api(home: Path, *args: str) -> dict:
    env = {**os.environ, "ANKI_MINER_HOME": str(home), "QT_QPA_PLATFORM": "offscreen"}
    proc = subprocess.run(
        [sys.executable, "-m", "anki_miner", "--api", *args],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        timeout=600,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr.decode(errors="replace")
    [line] = proc.stdout.decode("ascii").splitlines()
    return json.loads(line)


@pytest.fixture
def home(isolated_home: Path, fake_anki):
    """A seeded home whose SAVED deck is wrong, so only the run file's overlay can make a run succeed."""
    pytest.importorskip("fugashi")
    seed_offline_dict(isolated_home / "dicts")
    config = build_app_config(
        E2EConfig(test_home=isolated_home, ankiconnect_url=fake_anki.url), isolated_home, bypass_known_words=True
    )
    GUIConfigManager.save_config(dataclasses.replace(config, anki_deck_name="Deck That Does Not Exist"))
    return isolated_home, fake_anki


def _srt(cues: list[tuple[float, float, str]]) -> str:
    def stamp(seconds: float) -> str:
        ms = round(seconds * 1000)
        return f"{ms // 3_600_000:02}:{ms // 60_000 % 60:02}:{ms // 1000 % 60:02},{ms % 1000:03}"

    return "\n".join(f"{i}\n{stamp(a)} --> {stamp(b)}\n{text}\n" for i, (a, b, text) in enumerate(cues, 1))


def _plain(field_html: str) -> str:
    return html.unescape(re.sub(r"<[^>]+>", "", field_html))


def _mine(home: Path, tmp_path: Path, runs_dir: Path, video: Path, subtitle: Path, words: list[dict]) -> dict:
    run_file = tmp_path / "run.json"
    run_file.write_text(
        json.dumps(
            {
                "schema": 1,
                "run_dir": str(runs_dir),
                "profile": None,
                "language": "ja",
                # merge on: line_merges, the auto stamp and _materialize_line_expansions run for real
                "config": {
                    "anki_deck_name": DECK,
                    "min_frequency_rank": 0,
                    "max_frequency_rank": 0,
                    "merge_incomplete_cues": True,
                    "allow_duplicate_cards": False,  # the re-mine below must come back duplicate
                    "anki_fields": _EXTRA_FIELDS,
                },
                "episodes": [
                    {
                        "run_id": "ep-01",
                        "video_file": str(video),
                        "subtitle_file": str(subtitle),
                        "tags": "job::1",
                        "words": words,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    return _api(home, "mine", str(run_file))


def test_api_mine_round_trip(home, tmp_path) -> None:
    test_home, fake = home
    fake.seed_model(E2EConfig().note_type, ["Front", "Back", *_EXTRA_FIELDS.values()])
    fields = {"word": "Front", **_EXTRA_FIELDS}
    video = tmp_path / "ep.mkv"
    shutil.copy(get_test_video(), video)
    assert (get_media_duration_seconds(video, "ffprobe") or 0) >= 5
    runs_dir = tmp_path / "runs"
    runs_dir.mkdir()
    # 本 and 買う sit on lines 0 and 2 (line 2 reads differently); 学校 on line 1 only.
    subtitle = tmp_path / "ep.ja.srt"
    subtitle.write_text(
        _srt(
            [
                (0.5, 1.5, "新しい本を買いました"),
                (1.7, 2.7, "今日は学校で勉強する"),
                (2.9, 3.9, "ねえ、新しい本を買いました"),
            ]
        ),
        encoding="utf-8",
    )
    words = [
        {"word": "本", "line_start": 2.9, "line_expansion": [0, 0]},
        {"word": "学校", "line_start": 1.7, "line_expansion": [0, 1]},
        {"word": "買う", "line_text": "ねえ", "line_expansion": [0, 0]},
        {"word": "本"},
        {"word": "notaword"},
    ]
    verdict = _mine(test_home, tmp_path, runs_dir, video, subtitle, words)
    assert verdict["ok"] is True and verdict["runs"][0]["file"] == "result-1.json", verdict
    result = json.loads((runs_dir / "ep-01" / "result-1.json").read_text(encoding="utf-8"))
    rows = result["words"]
    assert [(r["word"], r["status"]) for r in rows] == [
        ("本", "created"),
        ("学校", "created"),
        ("買う", "created"),
        ("本", "duplicate"),
        ("notaword", "not_found"),
    ], result
    assert [r["line_start"] for r in rows[:3]] == [2.9, 1.7, 2.9]
    assert all(r["media_missing"] == [] for r in rows[:3]) and result["media_store_failures"] == 0

    # The chosen line and the merge reach the notes themselves.
    notes = {_plain(n[fields["word"]]): n for n in fake.notes(DECK)}
    assert set(notes) == {"本", "学校", "買う"}
    for row in rows[:3]:
        assert _plain(notes[row["word"]][fields["sentence"]]) == row["sentence"]
    assert rows[0]["sentence"].startswith("ねえ") and rows[2]["sentence"].startswith("ねえ")
    assert "学校" in rows[1]["sentence"] and "ねえ" in rows[1]["sentence"]  # merged with the line after
    assert not (runs_dir / "ep-01" / "media").exists()
    assert (test_home / "anki_miner.api.log").exists()
    # The call touched neither the known-words nor the stats database.
    config = GUIConfigManager.load_config()
    assert not resolve_known_words_db_path(config).exists() and not config.stats_db_path.exists()

    # Mining the run again: Anki now has every word, and nothing new is written.
    verdict = _mine(test_home, tmp_path, runs_dir, video, subtitle, words[:3])
    assert verdict["runs"][0]["file"] == "result-2.json", verdict
    again = json.loads((runs_dir / "ep-01" / "result-2.json").read_text(encoding="utf-8"))
    assert [r["status"] for r in again["words"]] == ["duplicate"] * 3, again
    assert len(fake.notes(DECK)) == 3


def test_api_check_and_version(home) -> None:
    test_home, _fake = home
    assert _api(test_home, "version")["result"]["commands"][0] == "mine"
    check = _api(test_home, "check", "--language", "ja")
    assert {i["name"] for i in check["result"]["items"]} >= {"anki", "deck", "dictionary", "ffmpeg"}
