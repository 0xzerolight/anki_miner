"""Import side of resource bundles: what may land, landing it, and chaining it.

Every slot is rebuilt from its shipped source through the family importer,
pinned to the sender's slot id and stamped with the bundle's language. The
import never replaces what the receiver has: an existing slot id or a
configured word list is blocked in :func:`plan_import`, and ignore-list rows
are added, never removed.
"""

from __future__ import annotations

import logging
import tempfile
import unicodedata
import zipfile
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Literal

from anki_miner.config import AnkiMinerConfig, ChainEntry, FreqEntry, PitchSourceEntry
from anki_miner.exceptions import OperationCancelled
from anki_miner.services._sqlite_index import language_kwarg
from anki_miner.services.dictionary.importers.yomitan_importer import import_yomitan_zip
from anki_miner.services.dictionary.zip_safety import MAX_UNCOMPRESSED_BYTES, read_member
from anki_miner.services.frequency.lemmatize import build_frequency_lemmatizer, lemmatize_kwarg
from anki_miner.services.frequency.source_importer import import_frequency_source
from anki_miner.services.known_word_db import add_user_known_words
from anki_miner.services.pitch_accent.source_importer import import_pitch_source
from anki_miner.services.resource_bundle.export import CancelFn, ProgressFn
from anki_miner.services.resource_bundle.manifest import SLOT_KINDS, WORDLIST_KINDS, BundleItem, BundleManifest
from anki_miner.utils.atomic_io import atomic_write_path

logger = logging.getLogger(__name__)

#: Same cap the word-list reader and known-words importer apply to a text file.
_TEXT_MEMBER_LIMIT = 50 * 1024 * 1024
#: A slot source is extracted before its importer's own zip checks run, and a
#: bundle can come from someone else: cap what it may write to temp. Python's
#: zip reader stops at the declared size, so checking the declaration is enough.
_MEMBER_LIMIT = MAX_UNCOMPRESSED_BYTES

Blocked = Literal["", "installed", "configured"]


@dataclass(frozen=True)
class ImportCandidate:
    item: BundleItem
    blocked: Blocked = ""


@dataclass(frozen=True)
class BundleInstallResult:
    #: Every item that landed, in bundle order (the ignore list included).
    installed: tuple[BundleItem, ...]
    #: Word-list kind -> the managed file it was written to.
    wordlist_paths: Mapping[str, Path]
    known_words_added: int
    #: (item name, error text) for each item that could not be installed.
    failures: tuple[tuple[str, str], ...]
    cancelled: bool
    #: Slot kind -> the root it was installed under (checked before chaining).
    roots: Mapping[str, Path]


def _slot_roots(config: AnkiMinerConfig) -> dict[str, Path]:
    return {"dictionary": config.dicts_root, "frequency": config.freqs_root, "pitch": config.pitch_root}


def plan_import(manifest: BundleManifest, config: AnkiMinerConfig) -> list[ImportCandidate]:
    """Mark each item the receiver already has; those are never replaced."""
    roots = _slot_roots(config)
    planned: list[ImportCandidate] = []
    for item in manifest.items:
        blocked: Blocked = ""
        if item.kind in SLOT_KINDS and (roots[item.kind] / item.item_id).exists():
            blocked = "installed"
        elif (
            item.kind == "blacklist"
            and config.blacklist_path is not None
            or item.kind == "whitelist"
            and config.whitelist_path is not None
        ):
            blocked = "configured"
        planned.append(ImportCandidate(item, blocked))
    return planned


def install_resource_bundle(
    bundle: Path,
    manifest: BundleManifest,
    selected: Sequence[BundleItem],
    config: AnkiMinerConfig,
    *,
    known_words_db: Path,
    wordlists_root: Path,
    progress: ProgressFn,
    cancel_check: CancelFn,
) -> BundleInstallResult:
    """Install ``selected`` from ``bundle``, one item at a time.

    One item failing is recorded and the rest continue. A cancel stops after
    the item in flight (whose importer discards its own staging) and returns
    what already landed, so the caller still chains it. Progress counts items;
    the importer's own step text rides along in the message. Each source is
    extracted just before its import and deleted right after.
    ``known_words_db`` is the caller's ``resolve_known_words_db_path``;
    ``wordlists_root`` is ``ANKI_MINER_HOME / "wordlists"``.
    """
    language = manifest.language
    installed: list[BundleItem] = []
    wordlist_paths: dict[str, Path] = {}
    failures: list[tuple[str, str]] = []
    known_added = 0
    cancelled = False
    total = len(selected)
    with zipfile.ZipFile(bundle) as zf:
        for index, item in enumerate(selected):
            if cancel_check():
                cancelled = True
                break
            step = f"({index + 1}/{total}) {item.name}"
            progress(index, total, step)
            try:
                if item.kind in SLOT_KINDS:
                    _install_slot(
                        zf,
                        item,
                        config,
                        language,
                        index=index,
                        total=total,
                        step=step,
                        progress=progress,
                        cancel_check=cancel_check,
                    )
                elif item.kind in WORDLIST_KINDS:
                    wordlist_paths[item.kind] = _install_wordlist(zf, item, wordlists_root / language)
                else:
                    known_added += _install_known_words(zf, item, known_words_db, language)
                installed.append(item)
            except OperationCancelled:
                cancelled = True
                break
            except Exception as exc:  # noqa: BLE001 - bucket A: one bad item must not sink the rest
                logger.warning(
                    "Resource bundle item failed: kind=%s id=%s exc=%s: %s",
                    item.kind,
                    item.item_id,
                    type(exc).__name__,
                    exc,
                )
                failures.append((item.name, str(exc)))
    logger.info(
        "Resource bundle installed: path=%s language=%s installed=%d failed=%d cancelled=%s",
        bundle,
        language,
        len(installed),
        len(failures),
        cancelled,
    )
    return BundleInstallResult(
        installed=tuple(installed),
        wordlist_paths=wordlist_paths,
        known_words_added=known_added,
        failures=tuple(failures),
        cancelled=cancelled,
        roots=_slot_roots(config),
    )


def _install_slot(
    zf: zipfile.ZipFile,
    item: BundleItem,
    config: AnkiMinerConfig,
    language: str,
    *,
    index: int,
    total: int,
    step: str,
    progress: ProgressFn,
    cancel_check: CancelFn,
) -> None:
    def forward(_current: int, _total: int, message: str) -> None:
        progress(index, total, f"{step}: {message}" if message else step)

    declared = zf.getinfo(item.member).file_size
    if declared > _MEMBER_LIMIT:
        raise ValueError(f"{item.member} declares {declared} bytes, over the {_MEMBER_LIMIT}-byte limit")
    with tempfile.TemporaryDirectory(prefix="anki-miner-bundle-", ignore_cleanup_errors=True) as tmp:
        # ``item.member`` is a fixed-shape path the manifest already validated.
        source = Path(zf.extract(item.member, tmp))
        if item.kind == "dictionary":
            import_yomitan_zip(
                source,
                config.dicts_root,
                dict_id=item.item_id,
                progress=forward,
                cancel_check=cancel_check,
                **language_kwarg(language),
            )
        elif item.kind == "frequency":
            options = dict(item.options)
            lemmatize = build_frequency_lemmatizer(language) if options.get("lemmatised") == "1" else None
            import_frequency_source(
                source,
                config.freqs_root,
                source_id=item.item_id,
                source_name=item.name,
                progress=forward,
                cancel_check=cancel_check,
                declared_mode=options.get("declared_mode", ""),
                **language_kwarg(language),
                **lemmatize_kwarg(lemmatize),
            )
        else:
            import_pitch_source(
                source,
                config.pitch_root,
                source_id=item.item_id,
                source_name=item.name,
                progress=forward,
                cancel_check=cancel_check,
                **language_kwarg(language),
            )


def _read_text_member(zf: zipfile.ZipFile, member: str) -> bytes:
    data = read_member(zf, member, limit=_TEXT_MEMBER_LIMIT)
    if len(data) > _TEXT_MEMBER_LIMIT:
        raise ValueError(f"{member} is over the {_TEXT_MEMBER_LIMIT}-byte limit")
    return data


def _install_known_words(zf: zipfile.ZipFile, item: BundleItem, known_words_db: Path, language: str) -> int:
    text = _read_text_member(zf, item.member).decode("utf-8")
    words = {unicodedata.normalize("NFC", line.strip()) for line in text.splitlines() if line.strip()}
    return add_user_known_words(known_words_db, words, language=language)


def _install_wordlist(zf: zipfile.ZipFile, item: BundleItem, language_dir: Path) -> Path:
    """Land a word list as the managed ``wordlists/<lang>/<kind>.txt``, bytes as sent.

    Kept byte-for-byte: ``WordListService`` decodes with the language's
    encoding ladder when it reads the file, exactly as for a hand-picked list.
    """
    data = _read_text_member(zf, item.member)
    language_dir.mkdir(parents=True, exist_ok=True)
    target = language_dir / f"{item.kind}.txt"
    with atomic_write_path(target) as staged:
        staged.write_bytes(data)
    return target


def apply_install_to_config(config: AnkiMinerConfig, result: BundleInstallResult) -> AnkiMinerConfig:
    """Chain what landed, the way the Add buttons do.

    New dictionaries go on top in bundle order (``DictionaryImportFlow``'s
    placement); new frequency and pitch sources are appended in bundle order
    (``SourceChainImportFlow``'s). Each keeps the sender's enabled flag. A
    dangling entry for the same id is replaced, never duplicated; every other
    entry keeps its place. A landed word list becomes the active one, with the
    sender's on/off state.

    Raises:
        ValueError: A slot was installed under a root the config no longer
            points at (the same guard ``apply_download_summary`` applies).
    """
    live_roots = _slot_roots(config)
    for item in result.installed:
        if item.kind in SLOT_KINDS and result.roots[item.kind] != live_roots[item.kind]:
            raise ValueError(
                f"{item.name} ({item.item_id}) was installed under {result.roots[item.kind]}, "
                f"but the active folder is now {live_roots[item.kind]}"
            )
    dicts = [i for i in result.installed if i.kind == "dictionary"]
    freqs = [i for i in result.installed if i.kind == "frequency"]
    pitches = [i for i in result.installed if i.kind == "pitch"]
    new_dicts = {i.item_id for i in dicts}
    new_freqs = {i.item_id for i in freqs}
    new_pitches = {i.item_id for i in pitches}
    config = replace(
        config,
        dictionary_chain=(
            *(ChainEntry(kind="indexed", dict_id=i.item_id, enabled=i.enabled) for i in dicts),
            *(e for e in config.dictionary_chain if not (e.kind == "indexed" and e.dict_id in new_dicts)),
        ),
        frequency_chain=(
            *(e for e in config.frequency_chain if e.source_id not in new_freqs),
            *(FreqEntry(i.item_id, enabled=i.enabled) for i in freqs),
        ),
        pitch_chain=(
            *(e for e in config.pitch_chain if e.source_id not in new_pitches),
            *(PitchSourceEntry(i.item_id, enabled=i.enabled) for i in pitches),
        ),
    )
    enabled = {i.kind: i.enabled for i in result.installed}
    if "blacklist" in result.wordlist_paths:
        config = replace(config, blacklist_path=result.wordlist_paths["blacklist"], use_blacklist=enabled["blacklist"])
    if "whitelist" in result.wordlist_paths:
        config = replace(config, whitelist_path=result.wordlist_paths["whitelist"], use_whitelist=enabled["whitelist"])
    return config
