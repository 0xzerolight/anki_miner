"""``media``: a clip and a picture for each named line, cut into the run folder (API.md).

No parse, no dictionary, no Anki: lines are read by the mining language's own
cleaner (``create_line_parser``, its tokenizer never loaded) and chosen and
merged as ``mine`` chooses and merges them.
"""

from __future__ import annotations

import shutil
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from functools import partial
from pathlib import Path

from anki_miner.cli.api import settings
from anki_miner.cli.api.contract import SUBTITLE_UNREADABLE, ApiError, run_verdict
from anki_miner.cli.api.files import MediaEpisode, MediaFile
from anki_miner.cli.api.lines import fit_expansion, line_merge, nearest_line
from anki_miner.cli.api.runfolder import next_numbered, write_json
from anki_miner.cli.api.runs import check_video, guarded, require_ffmpeg
from anki_miner.config import AnkiMinerConfig
from anki_miner.exceptions import SubtitleParseError
from anki_miner.gui.utils.service_factory import create_line_parser
from anki_miner.models.word import TokenizedWord
from anki_miner.services.cue_merge import merge_budget_seconds
from anki_miner.services.media_extractor import MediaExtractorService
from anki_miner.services.word_filter import merge_cue_window


def media_runs(media_file: MediaFile) -> list[dict[str, object]]:
    """Every episode's lines cut in turn; a media-<n>.json for each episode that got as far as cutting."""
    config = settings.resolve_run_config(media_file.profile, media_file.language, {})
    if media_file.audio_bitrate is not None:
        config = replace(config, audio_bitrate=media_file.audio_bitrate)
    require_ffmpeg(config)
    return [guarded(episode.run_id, partial(_cut_one, media_file, episode, config)) for episode in media_file.episodes]


def _cut_one(media_file: MediaFile, episode: MediaEpisode, config: AnkiMinerConfig) -> dict[str, object]:
    folder = media_file.run_dir / episode.run_id
    folder.mkdir(exist_ok=True)
    check_video(config, episode.video_file)
    parser = create_line_parser(config)
    try:
        entries = parser.parse_raw_entries(episode.subtitle_file, episode.subtitle_offset)
        # The same lines at the file's own times: line_start is what the caller read there.
        raw = parser.parse_raw_entries(episode.subtitle_file, 0.0)
    except SubtitleParseError as exc:
        raise ApiError(SUBTITLE_UNREADABLE, str(exc)) from exc
    if not entries:
        raise ApiError(SUBTITLE_UNREADABLE, f"The subtitle file has no lines: {episode.subtitle_file}")
    path = next_numbered(folder, "media")
    out = folder / path.stem
    out.mkdir()
    height = media_file.still_height
    # The profile's animated picture has its own height setting: still_height sets both.
    cut_config = replace(
        config, media_temp_folder=out, screenshot_animated_height=height or config.screenshot_animated_height
    )
    extractor = MediaExtractorService(cut_config, still_height=height)
    budget = merge_budget_seconds(config.audio_padding)
    chosen = []  # (line index, merged window) per requested line
    for request in episode.lines:
        index = nearest_line(raw, request.line_start)
        before, after = (
            fit_expansion(entries, index, request.line_expansion, budget)
            if request.line_expansion is not None
            else line_merge(config, entries, index)
        )
        chosen.append((index, merge_cue_window(entries, index, before, after)))
    words = [
        TokenizedWord(
            surface=str(k),
            lemma="",
            reading="",
            sentence=window.text,
            start_time=window.start,
            end_time=window.end,
            duration=window.end - window.start,
        )
        for k, (_index, window) in enumerate(chosen, 1)
    ]
    cut = partial(
        extractor.extract_media, episode.video_file, temp_folder=out, audio_track_override=episode.audio_track_override
    )
    # Side by side, as a mine's media stage cuts. Not extract_media_batch: it keeps only
    # the lines whose picture was cut, and a line here keeps whichever cut worked.
    with ThreadPoolExecutor(max_workers=config.max_parallel_workers) as pool:
        cuts = list(pool.map(cut, words))
    rows = [
        {
            "line_start": raw[index][0],
            "start": window.start,
            "end": window.end,
            "text": window.text,
            "picture": _keep(made.screenshot_path, out, k),
            "audio": _keep(made.audio_path, out, k),
        }
        for k, ((index, window), made) in enumerate(zip(chosen, cuts, strict=True), 1)
    ]
    write_json(path, {"schema": 1, "run_id": episode.run_id, "lines": rows})
    return run_verdict(episode.run_id, file=path.name)


def _keep(cut: Path | None, out: Path, k: int) -> str | None:
    """A cut file renamed to ``<k>.<ext>`` in *out*, as a path inside the run folder; None when it failed."""
    if cut is None:
        return None
    kept = out / f"{k}{cut.suffix}"
    shutil.move(cut, kept)
    return f"{out.name}/{kept.name}"
