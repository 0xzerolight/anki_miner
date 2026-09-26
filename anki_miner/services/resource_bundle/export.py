"""Export side of resource bundles: what the active language could send, and the zip.

Only what the active chains reference is offered -- that is the setup. A slot
travels as its kept ``source.*`` file; one without it (a legacy JMdict-XML
import) or stamped for another language is still returned, marked
``unavailable``, so the picker can say why it cannot go.
"""

from __future__ import annotations

import logging
import zipfile
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Literal

from anki_miner.config import AnkiMinerConfig
from anki_miner.exceptions import OperationCancelled
from anki_miner.languages.registry import config_language
from anki_miner.services.dictionary.registry import DictionaryRegistry
from anki_miner.services.frequency.registry import FrequencySourceRegistry
from anki_miner.services.frequency.source_importer import FREQUENCY_SOURCE_SUFFIXES, slot_import_options
from anki_miner.services.known_word_db import KnownWordDB
from anki_miner.services.pitch_accent.registry import PitchSourceRegistry
from anki_miner.services.pitch_accent.source_importer import PITCH_SOURCE_SUFFIXES
from anki_miner.services.resource_bundle.manifest import (
    MANIFEST_MEMBER,
    BundleItem,
    BundleManifest,
    ItemKind,
    manifest_to_json,
    member_name,
)
from anki_miner.utils.atomic_io import atomic_write_path

logger = logging.getLogger(__name__)

ProgressFn = Callable[[int, int, str], None]
CancelFn = Callable[[], bool]
Unavailable = Literal["", "no_source", "other_language"]

# Names of the single-instance kinds as they appear in the manifest and the
# (English, like every importer's) progress line; the picker labels them itself.
_SINGLE_ITEM_NAMES: dict[str, str] = {
    "known_words": "Known-words ignore list",
    "blacklist": "Blacklist",
    "whitelist": "Whitelist",
}


@dataclass(frozen=True)
class ExportCandidate:
    item: BundleItem
    #: The file streamed into the zip; ``None`` for the ignore list (read at write time).
    source: Path | None = None
    size_bytes: int = 0
    word_count: int = 0
    unavailable: Unavailable = ""


@dataclass(frozen=True)
class BundleWriteResult:
    path: Path
    item_count: int
    size_bytes: int


def collect_export_candidates(config: AnkiMinerConfig, *, known_words_db: Path) -> list[ExportCandidate]:
    """Everything the active language's setup could put in a bundle, in chain order.

    Scans the three registries (disk I/O): call off the GUI thread.
    ``known_words_db`` is ``resolve_known_words_db_path(config)``, derived by the
    caller -- services never derive it.
    """
    language = config_language(config)
    candidates: list[ExportCandidate] = []

    dicts = DictionaryRegistry(config.dicts_root)
    dicts.load()
    for entry in config.dictionary_chain:
        if entry.kind != "indexed" or entry.dict_id is None:
            continue
        dict_meta = dicts.get(entry.dict_id)
        if dict_meta is not None:
            candidates.append(
                _slot_candidate(
                    "dictionary",
                    entry.dict_id,
                    dict_meta.source_name,
                    entry.enabled,
                    dict_meta.db_path.parent,
                    (".zip",),
                    dict_meta.language,
                    language,
                )
            )

    freqs = FrequencySourceRegistry(config.freqs_root)
    freqs.load()
    for freq_entry in config.frequency_chain:
        freq_meta = freqs.get(freq_entry.source_id)
        if freq_meta is not None:
            declared_mode, lemmatised = slot_import_options(freq_meta.db_path.parent)
            candidates.append(
                _slot_candidate(
                    "frequency",
                    freq_entry.source_id,
                    freq_meta.source_name,
                    freq_entry.enabled,
                    freq_meta.db_path.parent,
                    FREQUENCY_SOURCE_SUFFIXES,
                    freq_meta.language,
                    language,
                    options=(("declared_mode", declared_mode), ("lemmatised", "1" if lemmatised else "0")),
                )
            )

    pitches = PitchSourceRegistry(config.pitch_root)
    pitches.load()
    for pitch_entry in config.pitch_chain:
        pitch_meta = pitches.get(pitch_entry.source_id)
        if pitch_meta is not None:
            candidates.append(
                _slot_candidate(
                    "pitch",
                    pitch_entry.source_id,
                    pitch_meta.source_name,
                    pitch_entry.enabled,
                    pitch_meta.db_path.parent,
                    PITCH_SOURCE_SUFFIXES,
                    pitch_meta.language,
                    language,
                )
            )

    if known_words_db.is_file():
        word_count = len(KnownWordDB(known_words_db, language=language).get_words_by_source("user"))
        if word_count:
            candidates.append(ExportCandidate(_single_item("known_words", enabled=True), word_count=word_count))

    wordlists: tuple[tuple[ItemKind, Path | None, bool], ...] = (
        ("blacklist", config.blacklist_path, config.use_blacklist),
        ("whitelist", config.whitelist_path, config.use_whitelist),
    )
    for kind, path, enabled in wordlists:
        if path is not None and path.is_file():
            candidates.append(
                ExportCandidate(_single_item(kind, enabled=enabled), source=path, size_bytes=path.stat().st_size)
            )
    # A hand-edited or legacy config can chain one slot twice; the manifest
    # refuses duplicate items, so the first occurrence is the one offered.
    unique: dict[tuple[str, str], ExportCandidate] = {}
    for candidate in candidates:
        unique.setdefault((candidate.item.kind, candidate.item.item_id), candidate)
    return list(unique.values())


def _single_item(kind: ItemKind, *, enabled: bool) -> BundleItem:
    return BundleItem(
        kind=kind,
        item_id=kind,
        name=_SINGLE_ITEM_NAMES[kind],
        member=member_name(kind, kind, ".txt"),
        enabled=enabled,
    )


def _slot_candidate(
    kind: ItemKind,
    slot_id: str,
    name: str,
    enabled: bool,
    slot_dir: Path,
    suffixes: tuple[str, ...],
    slot_language: str,
    language: str,
    *,
    options: tuple[tuple[str, str], ...] = (),
) -> ExportCandidate:
    source = next((slot_dir / f"source{s}" for s in suffixes if (slot_dir / f"source{s}").is_file()), None)
    item = BundleItem(
        kind=kind,
        item_id=slot_id,
        name=name,
        member=member_name(kind, slot_id, source.suffix if source is not None else suffixes[0]),
        enabled=enabled,
        options=options,
    )
    if slot_language != language:
        return ExportCandidate(item, unavailable="other_language")
    if source is None:
        return ExportCandidate(item, unavailable="no_source")
    return ExportCandidate(item, source=source, size_bytes=source.stat().st_size)


def write_resource_bundle(
    target: Path,
    candidates: Sequence[ExportCandidate],
    *,
    language: str,
    app_version: str,
    known_words_db: Path,
    progress: ProgressFn,
    cancel_check: CancelFn,
) -> BundleWriteResult:
    """Write the available ``candidates`` into a bundle at ``target``, atomically.

    Source files are streamed; a ``.zip`` source is stored, not recompressed.
    A cancel or failure leaves ``target`` as it was.

    Raises:
        OperationCancelled: ``cancel_check`` turned true between items.
        OSError / sqlite3.Error: A source or the known-words DB could not be read.
    """
    selected = [c for c in candidates if not c.unavailable]
    total = len(selected)
    with atomic_write_path(target) as staged, zipfile.ZipFile(staged, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        written: list[BundleItem] = []
        for index, candidate in enumerate(selected):
            if cancel_check():
                raise OperationCancelled("Export cancelled")
            item = candidate.item
            progress(index, total, item.name)
            if item.kind == "known_words":
                words = sorted(KnownWordDB(known_words_db, language=language).get_words_by_source("user"))
                zf.writestr(item.member, "".join(f"{word}\n" for word in words))
                # The receiver's checklist shows how many words it would take on.
                item = replace(item, options=(("word_count", str(len(words))),))
            else:
                assert candidate.source is not None  # every other available kind has a file
                compress = zipfile.ZIP_STORED if candidate.source.suffix == ".zip" else zipfile.ZIP_DEFLATED
                zf.write(candidate.source, item.member, compress_type=compress)
            written.append(item)
        manifest = BundleManifest(language=language, app_version=app_version, items=tuple(written))
        zf.writestr(MANIFEST_MEMBER, manifest_to_json(manifest))
    size = target.stat().st_size
    logger.info("Resource bundle written: path=%s language=%s items=%d bytes=%d", target, language, total, size)
    return BundleWriteResult(path=target, item_count=total, size_bytes=size)
