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

from anki_miner.config import create_default_config
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

#: 本 and 買う sit on lines 0 and 2 (line 2 reads differently); 学校 on line 1 only.
_ROUND_TRIP = [
    (0.5, 1.5, "新しい本を買いました"),
    (1.7, 2.7, "今日は学校で勉強する"),
    (2.9, 3.9, "ねえ、新しい本を買いました"),
]


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


def _mine(
    home: Path, tmp_path: Path, runs_dir: Path, video: Path, subtitle: Path, words: list[dict], command: str = "mine"
) -> dict:
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
    return _api(home, command, str(run_file))


def test_api_mine_round_trip(home, tmp_path) -> None:
    test_home, fake = home
    fake.seed_model(E2EConfig().note_type, ["Front", "Back", *_EXTRA_FIELDS.values()])
    fields = {"word": "Front", **_EXTRA_FIELDS}
    video = tmp_path / "ep.mkv"
    shutil.copy(get_test_video(), video)
    assert (get_media_duration_seconds(video, "ffprobe") or 0) >= 5
    runs_dir = tmp_path / "runs"
    runs_dir.mkdir()
    subtitle = tmp_path / "ep.ja.srt"
    subtitle.write_text(_srt(_ROUND_TRIP), encoding="utf-8")
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


def _run(tmp_path: Path, runs_dir: Path, video: Path, subtitle: Path, words: list[dict], **top) -> Path:
    run_file = tmp_path / "run.json"
    run_file.write_text(
        json.dumps(
            {
                "schema": 1,
                "run_dir": str(runs_dir),
                "language": "ja",
                **top,
                "config": {
                    "anki_deck_name": DECK,
                    "min_frequency_rank": 0,
                    "max_frequency_rank": 0,
                    # 学校's line is ten characters: the profile's sentence cap would remove it (Z-1)
                    "max_sentence_chars": 3,
                    "bold_target_in_sentence": True,
                    "merge_incomplete_cues": False,  # each sentence is its own line
                    "deduplicate_sentences": False,
                    "use_i_plus_one_filter": False,
                    "anki_fields": _EXTRA_FIELDS,
                },
                "episodes": [
                    {"run_id": "ep-01", "video_file": str(video), "subtitle_file": str(subtitle), "words": words}
                ],
            }
        ),
        encoding="utf-8",
    )
    return run_file


def test_api_named_words_whitelisted_made_from_their_line_and_dry_run(home, tmp_path) -> None:
    test_home, fake = home
    fake.seed_model(E2EConfig().note_type, ["Front", "Back", *_EXTRA_FIELDS.values()])
    video = tmp_path / "ep.mkv"
    shutil.copy(get_test_video(), video)
    runs_dir = tmp_path / "runs"
    runs_dir.mkdir()
    subtitle = tmp_path / "ep.ja.srt"
    subtitle.write_text(
        _srt([(0.5, 1.5, "新しい本を買いました"), (1.7, 2.7, "今日は学校で勉強する"), (2.9, 3.9, "本屋に行く")]),
        encoding="utf-8",
    )
    words = [
        {"word": "学校", "line_start": 1.7},  # whitelisted past the sentence cap
        {"word": "本", "line_start": 2.9},  # produced on line 0 only: made from line 2
        {"word": "勉強", "line_start": 0.5},  # not in line 0: not_found
    ]
    dry = _api(test_home, "mine", str(_run(tmp_path, runs_dir, video, subtitle, words, dry_run=True)))
    assert dry["ok"] is True, dry
    rows = json.loads((runs_dir / "ep-01" / "result-1.json").read_text(encoding="utf-8"))["words"]
    assert [(r["status"], r["from_line"]) for r in rows] == [("ready", False), ("ready", True), ("not_found", False)]
    assert fake.notes(DECK) == []

    verdict = _api(test_home, "mine", str(_run(tmp_path, runs_dir, video, subtitle, words)))
    assert verdict["ok"] is True, verdict
    rows = json.loads((runs_dir / "ep-01" / "result-2.json").read_text(encoding="utf-8"))["words"]
    assert [(r["word"], r["status"], r["from_line"]) for r in rows] == [
        ("学校", "created", False),
        ("本", "created", True),
        ("勉強", "not_found", False),
    ], rows
    notes = {_plain(n["Front"]): n for n in fake.notes(DECK)}
    assert _plain(notes["本"]["Sentence"]) == "本屋に行く" and "<b>本</b>" in notes["本"]["Sentence"]


def test_api_render_and_media(home, tmp_path) -> None:
    test_home, fake = home
    fake.seed_model(E2EConfig().note_type, ["Front", "Back", *_EXTRA_FIELDS.values()])
    video = tmp_path / "ep.mkv"
    shutil.copy(get_test_video(), video)
    runs_dir = tmp_path / "runs"
    runs_dir.mkdir()
    subtitle = tmp_path / "ep.ja.srt"
    subtitle.write_text(_srt(_ROUND_TRIP), encoding="utf-8")
    folder = runs_dir / "ep-01"

    words = [{"word": "本", "line_start": 2.9, "line_expansion": [0, 0]}, {"word": "学校", "line_start": 1.7}]
    verdict = _mine(test_home, tmp_path, runs_dir, video, subtitle, words, command="render")
    assert verdict["ok"] is True and verdict["runs"][0]["file"] == "render-1.json", verdict
    rows = json.loads((folder / "render-1.json").read_text(encoding="utf-8"))["words"]
    assert [(r["word"], r["status"]) for r in rows] == [("本", "rendered"), ("学校", "rendered")], rows
    for row in rows:
        assert _plain(row["fields"]["Front"]) == row["word"], row
        assert row["files"] and all((folder / name).is_file() for name in row["files"]), row
    assert fake.notes(DECK) == []  # nothing reached Anki

    media_file = tmp_path / "media.json"
    media_file.write_text(
        json.dumps(
            {
                "schema": 1,
                "run_dir": str(runs_dir),
                "language": "ja",
                "still_height": 120,
                "episodes": [
                    {
                        "run_id": "ep-01",
                        "video_file": str(video),
                        "subtitle_file": str(subtitle),
                        "lines": [{"line_start": 1.7, "line_expansion": [0, 0]}],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    verdict = _api(test_home, "media", str(media_file))
    assert verdict["ok"] is True and verdict["runs"][0]["file"] == "media-1.json", verdict
    [line] = json.loads((folder / "media-1.json").read_text(encoding="utf-8"))["lines"]
    assert (line["line_start"], line["text"]) == (1.7, "今日は学校で勉強する"), line
    picture, audio = folder / line["picture"], folder / line["audio"]
    assert picture.is_file() and audio.is_file(), line
    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=height", "-of", "csv=p=0"]
        + [str(picture)],
        capture_output=True,
        text=True,
        check=True,
    )
    assert probe.stdout.strip() == "120"


def test_api_settings_import_round_trip(isolated_home, tmp_path) -> None:
    GUIConfigManager.save_config(dataclasses.replace(create_default_config(), anki_deck_name="Live"))
    exported = tmp_path / "ja.json"
    assert _api(isolated_home, "settings-export", "--language", "ja", "--out", str(exported))["ok"]
    data = json.loads(exported.read_text(encoding="utf-8"))
    data["settings"]["anki_deck_name"] = "Caller"
    exported.write_text(json.dumps(data), encoding="utf-8")
    v = _api(isolated_home, "settings-import", str(exported), "--language", "ja", "--name", "Caller")
    assert v["result"] == {"profile": "caller", "created": True, "invalid_fields": []}, v
    assert _api(isolated_home, "profiles")["result"]["profiles"] == [
        {"id": "caller", "name": "Caller", "active": False},
        {"id": "default", "name": "Default", "active": True},
    ]
    back = tmp_path / "caller.json"
    assert _api(isolated_home, "settings-export", "--profile", "caller", "--language", "ja", "--out", str(back))["ok"]
    assert json.loads(back.read_text(encoding="utf-8"))["settings"]["anki_deck_name"] == "Caller"
    assert GUIConfigManager.load_config().anki_deck_name == "Live"  # the active settings are untouched
