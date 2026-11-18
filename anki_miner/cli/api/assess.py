"""assess: what the speech model hears in short clips, such as a card's audio (API.md, "assess").

Per episode, each clip in turn, decoded as ``transcribe`` decodes a file, into
one ``assess-<n>.json`` row per clip: the transcript, Whisper's own language
detection, every segment with its numbers, and the speech regions and levels
(:mod:`~anki_miner.services.asr.clip_assessment`). One model session serves
the whole call, so the model loads once. A clip whose audio cannot be
extracted gets its own error row and the run goes on. Nothing goes to Anki and
no lock is held, as for ``transcribe``.
"""

from __future__ import annotations

import dataclasses
import threading
from dataclasses import dataclass, replace
from functools import partial
from pathlib import Path

from anki_miner.cli.api import files
from anki_miner.cli.api.contract import AUDIO_UNREADABLE, CANCELLED, ApiError
from anki_miner.cli.api.files import JobsFile
from anki_miner.cli.api.jobs import Run, job_runs
from anki_miner.cli.api.transcribe import CANCELLED_MESSAGE, asr_language, prepare_speech
from anki_miner.config import AnkiMinerConfig
from anki_miner.services.asr import model_manager
from anki_miner.services.asr.clip_assessment import ClipAssessment, assess_clip
from anki_miner.services.asr.transcriber import Ct2ModelSession
from anki_miner.services.media_extractor import MediaExtractorService

_KEYS = frozenset({"run_id", "audio"})
#: A clip row's measured keys, all null on a clip that failed.
_MEASURED = (
    "engine",
    "device",
    "duration",
    "text",
    "detected_language",
    "detected_language_probability",
    "language_probability",
    "speech_ratio",
    "speech_level_db",
    "background_level_db",
    "speech",
    "segments",
)


@dataclass(frozen=True)
class AssessEpisode:
    run_id: str
    #: The clips, in the order given.
    audio: tuple[Path, ...]


def _episode(raw: object, where: str) -> AssessEpisode:
    obj = files.json_object(raw, where)
    files.check_keys(obj, _KEYS, _KEYS, where)
    return AssessEpisode(
        files.valid_run_id(obj["run_id"], f"{where}.run_id"), files.path_list(obj["audio"], f"{where}.audio")
    )


def call(assess_file: Path, cancel_all: threading.Event) -> list[dict[str, object]]:
    """``assess ASSESS_FILE``: the file checked, then its runs."""
    return assess_runs(files.parse_jobs_file(files.read_json_file(assess_file), "assess", _episode), cancel_all)


def assess_runs(job: JobsFile[AssessEpisode], cancel_all: threading.Event) -> list[dict[str, object]]:
    """Every episode of *job* in turn, on one model session; an assess-<n>.json for each that ends."""
    session = Ct2ModelSession()
    try:
        return job_runs(job, "assess", cancel_all, partial(prepare_speech, job), partial(_assess_episode, session))
    finally:
        session.release()


def _assess_episode(
    session: Ct2ModelSession, config: AnkiMinerConfig, episode: AssessEpisode, run: Run
) -> dict[str, object]:
    run_config = replace(config, media_temp_folder=run.media)
    extractor = MediaExtractorService(run_config)
    # The Whisper code the model listens with (Cantonese on large-v3 is yue); prepare_speech refused a model without one.
    language = model_manager.decode_language(config.asr_model, asr_language(config))
    run.progress.on_stage(1, 1, "assess")
    run.progress.on_start(len(episode.audio), "")
    clips: list[dict[str, object]] = []
    for done, clip in enumerate(episode.audio, 1):
        found = assess_clip(run_config, extractor, clip, language=language, session=session, cancel_event=run.cancel)
        # Before using it: a cancelled extraction reads as no audio, a cancelled decode as no speech.
        if run.cancel.is_set():
            raise ApiError(CANCELLED, CANCELLED_MESSAGE)
        clips.append(_row(clip, found))
        run.progress.on_progress(done, "")
    return {"model": config.asr_model, "language": language, "clips": clips}


def _row(clip: Path, found: ClipAssessment | None) -> dict[str, object]:
    """One clip of assess-<n>.json, every key present."""
    if found is None:
        return {
            "audio_file": str(clip),
            "error": AUDIO_UNREADABLE,
            "message": f"The audio could not be extracted from {clip}",
            **dict.fromkeys(_MEASURED),
        }
    return {
        "audio_file": str(clip),
        "error": None,
        "message": None,
        "engine": found.engine,
        "device": found.device,
        "duration": found.duration,
        "text": found.text,
        "detected_language": found.detected_language,
        "detected_language_probability": found.detected_language_probability,
        "language_probability": found.language_probability,
        "speech_ratio": found.speech_ratio,
        "speech_level_db": found.speech_level_db,
        "background_level_db": found.background_level_db,
        "speech": None if found.speech is None else [[start, end] for start, end in found.speech],
        "segments": [dataclasses.asdict(segment) for segment in found.segments],
    }
