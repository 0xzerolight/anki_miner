"""``media``: a clip and a picture for each named line, cut into the run folder (API.md).

No parse, no dictionary, no Anki: lines are read by the mining language's own
cleaner (``create_line_parser``, its tokenizer never loaded) and chosen and
merged as ``mine`` chooses and merges them. An audio file's lines get their
clip and the file's own cover as their picture. A reading source's lines are
its units: each gets its text, its location and the picture its card gets (a
manga text block's crop, a book's cover), and no clip, so it needs no ffmpeg.
"""

from __future__ import annotations

import logging
import math
import shutil
import zipfile
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from functools import partial
from pathlib import Path

from anki_miner.cli.api import settings
from anki_miner.cli.api.contract import SOURCE_UNREADABLE, SUBTITLE_UNREADABLE, ApiError
from anki_miner.cli.api.files import MediaEpisode, MediaFile
from anki_miner.cli.api.jobs import verdict
from anki_miner.cli.api.lines import fit_expansion, line_merge, nearest_line, unit_line
from anki_miner.cli.api.runfolder import next_numbered, write_json
from anki_miner.cli.api.runs import check_video, guarded, require_ffmpeg
from anki_miner.cli.api.sources import check_audio, open_reading
from anki_miner.config import AnkiMinerConfig
from anki_miner.exceptions import SetupError, SubtitleParseError
from anki_miner.gui.utils.service_factory import create_line_parser
from anki_miner.models.reading import ReadingUnit
from anki_miner.models.word import TokenizedWord
from anki_miner.orchestration.episode_processor import reading_cleanup
from anki_miner.services.cue_merge import merge_budget_seconds
from anki_miner.services.media_extractor import MediaExtractorService
from anki_miner.services.reading.images import prepare_card_image
from anki_miner.services.word_filter import merge_cue_window

logger = logging.getLogger(__name__)

#: What making one unit's picture can raise; the line keeps a null picture (phase 3' goes imageless the same way).
_PICTURE_ERRORS = (SetupError, OSError, ValueError, zipfile.BadZipFile, RuntimeError, NotImplementedError, EOFError)


def media_runs(media_file: MediaFile) -> list[dict[str, object]]:
    """Every episode's lines cut in turn; a media-<n>.json for each episode that got as far as cutting."""
    config = settings.resolve_run_config(media_file.profile, media_file.language, {})
    if media_file.audio_bitrate is not None:
        config = replace(config, audio_bitrate=media_file.audio_bitrate)
    if any(episode.reading_file is None for episode in media_file.episodes):  # a reading source cuts nothing
        require_ffmpeg(config)
    return [guarded(episode.run_id, partial(_cut_one, media_file, episode, config)) for episode in media_file.episodes]


def _row(**set_keys: object) -> dict[str, object]:
    """One line of media-<n>.json, every key present."""
    row: dict[str, object] = dict.fromkeys(
        ("line_start", "start", "end", "unit_index", "location", "text", "picture", "audio")
    )
    row.update(set_keys)
    return row


def _cut_one(media_file: MediaFile, episode: MediaEpisode, config: AnkiMinerConfig) -> dict[str, object]:
    folder = media_file.run_dir / episode.run_id
    folder.mkdir(exist_ok=True)
    if episode.reading_file is not None:
        return _read_one(folder, episode, config)
    source: Path
    if episode.audio_file is not None:
        source = episode.audio_file
        check_audio(config, source)
    else:
        assert episode.video_file is not None  # one of the three is set
        source = episode.video_file
        check_video(config, source)
    assert episode.subtitle_file is not None  # beside every video or audio file
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
        assert request.line_start is not None  # a video or audio episode's lines name one
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
    audio_only = episode.audio_file is not None
    cut = partial(
        extractor.extract_media,
        source,
        temp_folder=out,
        audio_track_override=episode.audio_track_override,
        audio_only=audio_only,
    )
    # Side by side, as a mine's media stage cuts. Not extract_media_batch: it keeps only
    # the lines whose picture was cut, and a line here keeps whichever cut worked.
    with ThreadPoolExecutor(max_workers=config.max_parallel_workers) as pool:
        cuts = list(pool.map(cut, words))
    # An audio file's picture is its embedded cover, as on an Audiobooks card: one per line.
    cover = extractor.extract_cover_art(episode.audio_file, out) if episode.audio_file is not None else None
    rows = [
        _row(
            line_start=raw[index][0],
            start=window.start,
            end=window.end,
            text=window.text,
            picture=_keep(cover, out, k, copy=True) if audio_only else _keep(made.screenshot_path, out, k),
            audio=_keep(made.audio_path, out, k),
        )
        for k, ((index, window), made) in enumerate(zip(chosen, cuts, strict=True), 1)
    ]
    if cover is not None:
        cover.unlink(missing_ok=True)
    write_json(path, {"schema": 1, "run_id": episode.run_id, "lines": rows})
    return verdict(episode.run_id, file=path.name)


def _read_one(folder: Path, episode: MediaEpisode, config: AnkiMinerConfig) -> dict[str, object]:
    """A reading source's lines: each unit's text (merged as ``mine`` merges it), location and picture.

    A ``unit_index`` past the end of the source, or naming a unit with no
    text, gives a line of nulls but its ``unit_index``.
    """
    source = episode.reading_file
    assert source is not None
    if isinstance(source, Path) and not source.exists():
        raise ApiError(SOURCE_UNREADABLE, f"File not found: {source}")
    parser = create_line_parser(config)
    document = open_reading(config, parser, source, cancel_check=lambda: False)  # media takes no cancel file
    entries = parser.reading_entries(document.units, subtitle_cleanup=reading_cleanup(document))
    if not entries:
        raise ApiError(SOURCE_UNREADABLE, f"{document.title} has no text.")
    units = {unit.index: unit for unit in document.units}
    path = next_numbered(folder, "media")
    out = folder / path.stem
    out.mkdir()
    archives: dict[Path, zipfile.ZipFile] = {}  # one open archive per volume for every line, as phase 3' keeps
    rows: list[dict[str, object]] = []
    try:
        for k, request in enumerate(episode.lines, 1):
            assert request.unit_index is not None  # a reading episode's lines name one
            index = unit_line(entries, request.unit_index)
            if index is None:
                rows.append(_row(unit_index=request.unit_index))
                continue
            # No time budget: units have no times, so a merge stops only at the source's ends.
            before, after = (
                (0, 0)
                if request.line_expansion is None
                else fit_expansion(entries, index, request.line_expansion, math.inf)
            )
            unit = units[request.unit_index]
            rows.append(
                _row(
                    unit_index=unit.index,
                    location=unit.location_label,
                    text=merge_cue_window(entries, index, before, after).text,
                    picture=_picture(unit, out, k, archives),
                )
            )
    finally:
        for archive in archives.values():
            archive.close()
    write_json(path, {"schema": 1, "run_id": episode.run_id, "lines": rows})
    return verdict(episode.run_id, file=path.name)


def _picture(unit: ReadingUnit, out: Path, k: int, archives: dict[Path, zipfile.ZipFile]) -> str | None:
    """*unit*'s card picture as ``<k>.jpg``: its page cropped to its text block (manga, always), or the cover."""
    if unit.image_ref is None:
        return None
    try:
        made = prepare_card_image(unit.image_ref, out, archives, block_box=unit.block_box)
    except _PICTURE_ERRORS as exc:
        logger.warning("media: no picture for unit %d (%s): %s", unit.index, unit.image_ref.source.name, exc)
        return None
    return _keep(made, out, k)


def _keep(cut: Path | None, out: Path, k: int, *, copy: bool = False) -> str | None:
    """A cut file as ``<k>.<ext>`` in *out*, as a path inside the run folder; None when it failed.

    Moved, or *copy*-ed for a file every line shares (an audio file's cover).
    """
    if cut is None:
        return None
    kept = out / f"{k}{cut.suffix}"
    if copy:
        shutil.copyfile(cut, kept)
    else:
        shutil.move(cut, kept)
    return f"{out.name}/{kept.name}"
