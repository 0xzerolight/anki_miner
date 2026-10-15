"""F-12: the saved-transcript cache (services/asr/transcript_cache.py). No ASR, no numpy."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from anki_miner.config import paths
from anki_miner.services.asr import transcript_cache
from anki_miner.services.asr.transcript_cache import (
    CACHE_FORMAT,
    cache_key,
    load_transcript,
    prune_transcript_cache,
    store_transcript,
)

_SEGMENTS = [(0.0, 1.5, "吾輩は猫である。"), (1.5, 3.0, "名前はまだ無い。")]


@pytest.fixture
def root(tmp_path, monkeypatch) -> Path:
    folder = tmp_path / "transcripts"
    monkeypatch.setattr(transcript_cache, "cache_root", lambda: folder)
    return folder


def _media(tmp_path: Path, name: str = "book.m4b", data: bytes = b"audio" * 100) -> Path:
    path = tmp_path / name
    path.write_bytes(data)
    return path


def test_a_stored_transcript_comes_back_for_the_same_file_model_and_language(root, tmp_path):
    media = _media(tmp_path)
    key = cache_key(media, model="large-v3", language="ja")
    assert key is not None and key.endswith("-large-v3-ja")

    store_transcript(key, _SEGMENTS)

    assert load_transcript(cache_key(media, model="large-v3", language="ja") or "") == _SEGMENTS
    assert [p.name for p in root.iterdir()] == [f"{key}.json"]  # the atomic temp file is gone


def test_a_language_switch_reads_and_writes_its_own_entry(root, tmp_path):
    """Review Focus 5: the cache key carries the language, so ja and zh never share a transcript."""
    media = _media(tmp_path)
    ja = cache_key(media, model="large-v3", language="ja")
    zh = cache_key(media, model="large-v3", language="zh")
    assert ja is not None and zh is not None and ja != zh

    store_transcript(ja, _SEGMENTS)

    assert load_transcript(zh) is None
    store_transcript(zh, [(0.0, 1.0, "你好。")])
    assert load_transcript(ja) == _SEGMENTS
    assert load_transcript(zh) == [(0.0, 1.0, "你好。")]


def test_another_model_misses(root, tmp_path):
    media = _media(tmp_path)
    store_transcript(cache_key(media, model="small", language="ja") or "", _SEGMENTS)

    assert load_transcript(cache_key(media, model="large-v3", language="ja") or "") is None


def test_an_edited_or_moved_file_misses(root, tmp_path):
    media = _media(tmp_path)
    before = cache_key(media, model="large-v3", language="ja")
    store_transcript(before or "", _SEGMENTS)

    st = media.stat()
    os.utime(media, ns=(st.st_atime_ns, st.st_mtime_ns + 5_000_000_000))
    assert cache_key(media, model="large-v3", language="ja") != before

    moved = _media(tmp_path, "other.m4b")
    assert cache_key(moved, model="large-v3", language="ja") != before


def test_a_file_that_cannot_be_read_has_no_key(tmp_path):
    assert cache_key(tmp_path / "missing.m4b", model="large-v3", language="ja") is None


@pytest.mark.parametrize(
    "content",
    [
        "not json",
        '{"format": 0, "segments": [[0.0, 1.0, "x"]]}',
        json.dumps({"format": CACHE_FORMAT, "segments": [[0.0, "late", "x"]]}),
        json.dumps({"format": CACHE_FORMAT, "segments": [[0.0, 1.0]]}),
        json.dumps({"format": CACHE_FORMAT, "segments": {}}),
        "[]",
    ],
)
def test_a_corrupt_or_stale_entry_is_a_miss(root, tmp_path, content):
    key = cache_key(_media(tmp_path), model="large-v3", language="ja") or ""
    root.mkdir(parents=True)
    (root / f"{key}.json").write_text(content, encoding="utf-8")

    assert load_transcript(key) is None


def test_a_failed_save_is_logged_not_raised(tmp_path, monkeypatch, caplog):
    blocker = tmp_path / "blocker"
    blocker.write_text("a file where the folder should be", encoding="utf-8")
    monkeypatch.setattr(transcript_cache, "cache_root", lambda: blocker / "transcripts")

    store_transcript("k", _SEGMENTS)

    assert "could not save" in caplog.text


def test_prune_deletes_the_oldest_files_down_to_the_limit(root):
    root.mkdir(parents=True)
    for age, name in enumerate(("newest", "middle", "oldest")):
        path = root / f"{name}.json"
        path.write_bytes(b"x" * 100)
        os.utime(path, (1_000_000 - age * 1000, 1_000_000 - age * 1000))

    removed = prune_transcript_cache(limit_bytes=150)

    assert removed == 2
    assert [p.name for p in root.iterdir()] == ["newest.json"]


def test_prune_keeps_everything_under_the_limit_and_tolerates_no_folder(root):
    assert prune_transcript_cache() == 0  # the folder does not exist yet
    root.mkdir(parents=True)
    (root / "a.json").write_bytes(b"x" * 10)
    assert prune_transcript_cache(limit_bytes=10) == 0
    assert (root / "a.json").exists()


def test_a_hit_counts_as_recent_use_for_pruning(root, tmp_path):
    old = cache_key(_media(tmp_path, "old.m4b"), model="large-v3", language="ja") or ""
    new = cache_key(_media(tmp_path, "new.m4b"), model="large-v3", language="ja") or ""
    store_transcript(old, _SEGMENTS)
    store_transcript(new, _SEGMENTS)
    os.utime(root / f"{old}.json", (1_000, 1_000))
    os.utime(root / f"{new}.json", (2_000, 2_000))

    assert load_transcript(old) == _SEGMENTS  # refreshes its mtime to now
    size = (root / f"{new}.json").stat().st_size
    prune_transcript_cache(limit_bytes=size)

    assert (root / f"{old}.json").exists()
    assert not (root / f"{new}.json").exists()


def test_the_cache_lives_under_the_app_home_and_stays_out_of_the_diagnostics_bundle(tmp_path):
    from anki_miner.diagnostics.bundle import collect_state_members

    assert transcript_cache.cache_root() == paths.ANKI_MINER_HOME / "cache" / "transcripts"
    key = cache_key(_media(tmp_path), model="large-v3", language="ja") or ""
    store_transcript(key, _SEGMENTS)
    try:
        members, missing = collect_state_members()
        assert not any("transcript" in name for name, _content in members)
        assert not any("transcript" in name for name in missing)
    finally:
        (transcript_cache.cache_root() / f"{key}.json").unlink()
