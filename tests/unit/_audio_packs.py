"""Minimal on-disk audio packs for the audio-pack import tests.

Plain builders shared by ``test_audio_pack_importer.py``,
``test_audio_pack_import_worker.py`` and ``test_audio_pack_import_flow.py``.
``test_audio_pack_registry.py`` and ``test_service_factory_audio_chain.py``
keep their own AJT variant (different words, non-empty audio bytes).
"""

from __future__ import annotations

import json
from pathlib import Path


def make_ajt_pack(directory: Path, n_entries: int = 2) -> Path:
    """Create a minimal AJT-format audio pack under *directory*."""
    media_dir = directory / "media"
    media_dir.mkdir(parents=True, exist_ok=True)
    headwords: dict = {}
    files_meta: dict = {}
    words = ["食べる", "飲む", "走る", "見る", "来る"]
    for i in range(n_entries):
        word = words[i % len(words)]
        fname = f"word_{i}.mp3"
        (media_dir / fname).touch()
        headwords.setdefault(word, []).append(fname)
        files_meta[fname] = {"kana_reading": f"reading_{i}", "pitch_number": str(i)}
    (directory / "index.json").write_text(
        json.dumps({"headwords": headwords, "files": files_meta}),
        encoding="utf-8",
    )
    return directory


def make_forvo_pack(directory: Path, n_entries: int = 2) -> Path:
    """Create a minimal Forvo-format audio pack under *directory*."""
    speakers = ["alice", "bob"]
    words = ["食べる", "飲む", "走る", "見る"]
    for i in range(n_entries):
        speaker = speakers[i % len(speakers)]
        word = words[i % len(words)]
        speaker_dir = directory / speaker
        speaker_dir.mkdir(parents=True, exist_ok=True)
        (speaker_dir / f"{word}.mp3").touch()
    return directory


def make_jpod_pack(directory: Path, n_entries: int = 2) -> Path:
    """Create a minimal JPod-legacy-format audio pack under *directory*."""
    directory.mkdir(parents=True, exist_ok=True)
    words = [("たべる", "食べる"), ("のむ", "飲む"), ("はしる", "走る")]
    for i in range(n_entries):
        reading, expr = words[i % len(words)]
        (directory / f"{reading} - {expr}.mp3").touch()
    return directory
