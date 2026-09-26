"""The --api two-call flow against the real pipeline: a subprocess, a seeded home, a fake Anki, real ffmpeg.

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
from tests.e2e.fixtures_subtitle import get_test_srt

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


def _prepare(home: Path, runs_dir: Path, run_id: str, video: Path, subtitle: Path) -> dict:
    run_file = runs_dir.parent / f"{run_id}.run.json"
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
                    "deduplicate_sentences": False,
                    "anki_fields": _EXTRA_FIELDS,
                },
                "episodes": [
                    {"run_id": run_id, "video_file": str(video), "subtitle_file": str(subtitle), "tags": "job::1"}
                ],
            }
        ),
        encoding="utf-8",
    )
    verdict = _api(home, "prepare", str(run_file))
    assert verdict["ok"] is True and verdict["runs"][0]["file"] == "candidates.json", verdict
    return json.loads((runs_dir / run_id / "candidates.json").read_text(encoding="utf-8"))


def _srt(cues: list[tuple[float, float, str]]) -> str:
    def stamp(seconds: float) -> str:
        ms = round(seconds * 1000)
        return f"{ms // 3_600_000:02}:{ms // 60_000 % 60:02}:{ms // 1000 % 60:02},{ms % 1000:03}"

    return "\n".join(f"{i}\n{stamp(a)} --> {stamp(b)}\n{text}\n" for i, (a, b, text) in enumerate(cues, 1))


def _plain(field_html: str) -> str:
    return html.unescape(re.sub(r"<[^>]+>", "", field_html))


def test_api_prepare_commit_round_trip(home, tmp_path) -> None:
    test_home, fake = home
    fake.seed_model(E2EConfig().note_type, ["Front", "Back", *_EXTRA_FIELDS.values()])
    fields = {"word": "Front", **_EXTRA_FIELDS}
    video = tmp_path / "ep.mkv"
    shutil.copy(get_test_video(), video)
    assert (get_media_duration_seconds(video, "ffprobe") or 0) >= 5
    runs_dir = tmp_path / "runs"
    runs_dir.mkdir()

    # A throwaway prepare on the fixture subtitle yields two lines whose words the seeded dictionary defines.
    scout = _prepare(test_home, runs_dir, "scout", video, Path(get_test_srt()))
    first = scout["candidates"][0]
    other = next(c for c in scout["candidates"] if c["line"] != first["line"])
    a_text, b_text = scout["lines"][first["line"]][3], scout["lines"][other["line"]][3]

    # `first` sits on lines 0 and 2 (line 2 reads differently), `other` on line 1, all inside the video.
    subtitle = tmp_path / "ep.ja.srt"
    subtitle.write_text(_srt([(0.5, 1.5, a_text), (1.7, 2.7, b_text), (2.9, 3.9, "ねえ、" + a_text)]), encoding="utf-8")
    doc = _prepare(test_home, runs_dir, "ep-01", video, subtitle)
    assert fake.note_count() == 0  # prepare writes nothing to Anki
    lines = doc["lines"]
    by_form = {c["mined_form"]: c for c in doc["candidates"]}
    assert 2 in by_form[first["mined_form"]]["sentence_candidates"] and lines[2][3] != lines[0][3]

    commit_file = tmp_path / "commit.json"
    commit_file.write_text(
        json.dumps(
            {
                "schema": 1,
                "run_dir": str(runs_dir),
                "runs": [
                    {
                        "run_id": "ep-01",
                        "words": [
                            {"mined_form": first["mined_form"], "line": 2, "line_expansion": [0, 0]},
                            {"mined_form": other["mined_form"], "line": 1, "line_expansion": [0, 1]},
                            {"mined_form": "notaword"},
                        ],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    verdict = _api(test_home, "commit", str(commit_file))
    assert verdict["ok"] is True and verdict["runs"][0]["file"] == "result-1.json", verdict
    result = json.loads((runs_dir / "ep-01" / "result-1.json").read_text(encoding="utf-8"))
    rows = {w["mined_form"]: w for w in result["words"]}
    assert rows["notaword"]["status"] == "not_found"
    assert rows[first["mined_form"]]["status"] == rows[other["mined_form"]]["status"] == "created", result
    assert rows[other["mined_form"]]["line_range"] == [1, 2]
    # the per-word cut record ran for real: both clips and pictures were cut
    assert rows[first["mined_form"]]["media_missing"] == rows[other["mined_form"]]["media_missing"] == []
    assert result["media_store_failures"] == 0

    # Review focus 6: the chosen line and the merge reach the note itself.
    notes = {_plain(n[fields["word"]]): n for n in fake.notes(DECK)}
    assert set(notes) == {first["mined_form"], other["mined_form"]}
    first_sentence = _plain(notes[first["mined_form"]][fields["sentence"]])
    assert first_sentence == lines[2][3] == rows[first["mined_form"]]["sentence"]
    assert _plain(notes[other["mined_form"]][fields["sentence"]]) == rows[other["mined_form"]]["sentence"]
    assert rows[other["mined_form"]]["sentence"] == f"{lines[1][3]} {lines[2][3]}"
    assert not (runs_dir / "ep-01" / "media").exists()
    assert (test_home / "anki_miner.api.log").exists()
    # Review focus 4: neither call touched the known-words or stats databases.
    config = GUIConfigManager.load_config()
    assert not resolve_known_words_db_path(config).exists() and not config.stats_db_path.exists()

    subtitle.write_text(subtitle.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    verdict = _api(test_home, "commit", str(commit_file))
    assert verdict["runs"][0]["error"] == "RUN_STALE" and verdict["runs"][0]["file"] is None


def test_api_check_and_version(home) -> None:
    test_home, _fake = home
    assert _api(test_home, "version")["result"]["commands"][0] == "prepare"
    check = _api(test_home, "check", "--language", "ja")
    assert {i["name"] for i in check["result"]["items"]} >= {"anki", "deck", "dictionary", "ffmpeg"}
