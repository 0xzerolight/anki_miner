"""Find installed resources that publish updates, and ask their publishers.

A Yomitan dictionary can name its own latest version: ``index.json`` carries
``isUpdatable: true``, an ``indexUrl`` (the latest ``index.json``) and a
``downloadUrl`` (the latest zip). Every slot keeps the zip it was built from
as ``source.zip``, so dictionaries installed before this feature need no
migration.

Only the active mining language's chains are read. The resource download
worker stamps every index it writes with the session's language, so a slot
of another language would come back stamped wrong and drop out of its own
chain. The check is slow (one request per publisher), so its result is
re-validated against the live config before anything is installed
(:func:`updates_still_valid`).

Qt-free: the GUI runs :func:`check_for_updates` off the GUI thread.
"""

from __future__ import annotations

import json
import logging
import re
import time
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TypeGuard
from urllib.parse import urlsplit

import requests

from anki_miner.config import AnkiMinerConfig
from anki_miner.languages.registry import config_language
from anki_miner.services._sqlite_index import StoreFamily, prove_owned_slot, read_slot_language
from anki_miner.services.dictionary.importers.yomitan_importer import read_yomitan_index
from anki_miner.services.frequency.source_importer import slot_import_options
from anki_miner.services.resource_catalog import ResourceSpec

logger = logging.getLogger(__name__)

#: How often the automatic run checks: Hoshi Reader's default.
UPDATE_INTERVAL_SECONDS = 7 * 24 * 60 * 60

_INDEX_TIMEOUT = (5, 10)
_MAX_REMOTE_INDEX_BYTES = 1024 * 1024
_USER_AGENT = "anki-miner (+https://github.com/0xzerolight/anki_miner)"
# Yomitan's simpleVersionTest. [0-9], not \d: JavaScript's \d is ASCII-only.
_DOTTED_INTEGERS = re.compile(r"(?:[0-9]+\.)*[0-9]+")


@dataclass(frozen=True)
class UpdatableResource:
    """An installed slot whose saved ``index.json`` names where its latest version lives."""

    kind: str  # a ResourceSpec kind: "dict", "freq" or "pitch"
    slot_id: str
    title: str
    revision: str
    index_url: str
    download_url: str
    #: Frequency only: the list was imported aggregated per lemma (S17), and
    #: the rebuild must be too.
    lemmatised: bool = False


@dataclass(frozen=True)
class ResourceUpdate:
    """A newer published revision of one installed slot."""

    resource: UpdatableResource
    latest_title: str
    latest_revision: str
    download_url: str

    def to_spec(self) -> ResourceSpec:
        """The download that rebuilds the slot in place, under its own id.

        ``pin_slot`` keeps a frequency zip in this slot rather than the one
        its title derives; ``sweep_superseded`` is off because an update must
        never delete a sibling copy; ``lemmatise`` replays how the list was
        built.
        """
        return ResourceSpec(
            id=self.resource.slot_id,
            kind=self.resource.kind,
            display_name=self.latest_title,
            url=self.download_url,
            license_note="",
            lemmatise=self.resource.lemmatised,
            pin_slot=True,
            sweep_superseded=False,
        )


@dataclass(frozen=True)
class UpdateCheck:
    """What one check found. ``failures`` pairs a resource with its error text."""

    checked: int
    updates: tuple[ResourceUpdate, ...] = ()
    failures: tuple[tuple[UpdatableResource, str], ...] = ()

    @property
    def reached(self) -> bool:
        """True when nothing was checkable or at least one publisher answered."""
        return self.checked == 0 or len(self.failures) < self.checked


def is_newer_revision(current: str, latest: str) -> bool:
    """Yomitan's ``compareRevisions``: True when ``latest`` is newer than ``current``.

    Dotted integers ("2026.10.03.0", "4.10") with the same number of parts
    compare as numbers; everything else ("JMdict.2026-10-07",
    "Jiten 26-10-02") compares as text, which the publishers' zero-padded
    dates keep in order. Newer only, so a publisher that briefly serves an
    older index never downgrades a dictionary.
    """
    if not (_DOTTED_INTEGERS.fullmatch(current) and _DOTTED_INTEGERS.fullmatch(latest)):
        return current < latest
    current_parts = [int(part) for part in current.split(".")]
    latest_parts = [int(part) for part in latest.split(".")]
    if len(current_parts) != len(latest_parts):
        return current < latest
    return current_parts < latest_parts


def _is_https(url: object) -> TypeGuard[str]:
    if not isinstance(url, str):
        return False
    try:
        parts = urlsplit(url)
    except ValueError:  # "https://[x": an unbalanced IPv6 bracket
        return False
    return parts.scheme == "https" and bool(parts.netloc)


def _chain_slots(config: AnkiMinerConfig) -> Iterator[tuple[str, StoreFamily, Path, str]]:
    """``(kind, family, root, slot_id)`` for every indexed chain entry, in chain order, once each."""
    families: tuple[tuple[str, StoreFamily, Path, list[str]], ...] = (
        (
            "dict",
            "dictionary",
            config.dicts_root,
            [e.dict_id for e in config.dictionary_chain if e.kind == "indexed" and e.dict_id],
        ),
        ("freq", "frequency", config.freqs_root, [e.source_id for e in config.frequency_chain]),
        ("pitch", "pitch", config.pitch_root, [e.source_id for e in config.pitch_chain]),
    )
    for kind, family, root, slot_ids in families:
        for slot_id in dict.fromkeys(slot_ids):
            yield kind, family, root, slot_id


def updatable_resources(config: AnkiMinerConfig) -> list[UpdatableResource]:
    """Every slot in the active language's chains that names its own latest version.

    Dictionaries, then frequency, then pitch, each in chain order. Disabled
    entries count: they are still installed, and Yomitan and Hoshi Reader
    update those too. A slot is skipped, never raised on, when it is missing
    or not ours, belongs to another language, has no saved zip, or its
    ``index.json`` does not name two https URLs.
    """
    language = config_language(config)
    found: list[UpdatableResource] = []
    for kind, family, root, slot_id in _chain_slots(config):
        try:
            resource = _updatable_slot(kind, family, root, slot_id, language)
        except Exception as exc:  # noqa: BLE001 — one unreadable slot must not stop the others' updates
            logger.warning("Slot skipped by the update check: slot=%s error=%s", slot_id, type(exc).__name__)
            continue
        if resource is not None:
            found.append(resource)
    return found


def _updatable_slot(
    kind: str, family: StoreFamily, root: Path, slot_id: str, language: str
) -> UpdatableResource | None:
    if not prove_owned_slot(root, slot_id, family):
        return None
    slot_dir = root / slot_id
    source_zip = slot_dir / "source.zip"
    if read_slot_language(slot_dir) != language or not source_zip.is_file():
        return None
    try:
        index = read_yomitan_index(source_zip)
    except Exception as exc:  # noqa: BLE001 — an unreadable saved zip just isn't updatable
        logger.warning("Saved source unreadable for update check: slot=%s error=%s", slot_id, type(exc).__name__)
        return None
    index_url = index.get("indexUrl")
    download_url = index.get("downloadUrl")
    if index.get("isUpdatable") is not True or not _is_https(index_url) or not _is_https(download_url):
        return None
    return UpdatableResource(
        kind=kind,
        slot_id=slot_id,
        title=str(index.get("title", "")).strip(),
        revision=str(index.get("revision", "")).strip(),
        index_url=index_url,
        download_url=download_url,
        lemmatised=kind == "freq" and slot_import_options(slot_dir)[1],
    )


def updates_still_valid(
    updates: Sequence[ResourceUpdate], *, checked: AnkiMinerConfig, live: AnkiMinerConfig
) -> tuple[ResourceUpdate, ...]:
    """The updates the live config can still take.

    The check ran on ``checked``. A different mining language or a moved
    root drops everything: the install would stamp the wrong language or
    write to the wrong folder. A slot no longer listed (removed during the
    check) is dropped alone.
    """
    roots = ("dicts_root", "freqs_root", "pitch_root")
    if config_language(checked) != config_language(live) or any(
        getattr(checked, root) != getattr(live, root) for root in roots
    ):
        return ()
    listed = {(kind, slot_id) for kind, _family, _root, slot_id in _chain_slots(live)}
    return tuple(update for update in updates if (update.resource.kind, update.resource.slot_id) in listed)


def fetch_remote_index(url: str) -> dict[str, Any]:
    """GET a publisher's latest ``index.json``; raise on any failure.

    The body is read in chunks and refused past 1 MiB. ``requests`` follows
    the redirect a GitHub ``releases/latest/download`` link always answers
    with.
    """
    response = requests.get(
        url,
        headers={"User-Agent": _USER_AGENT, "Accept": "application/json"},
        timeout=_INDEX_TIMEOUT,
        stream=True,
    )
    try:
        response.raise_for_status()
        body = bytearray()
        for chunk in response.iter_content(chunk_size=65536):
            body.extend(chunk)
            if len(body) > _MAX_REMOTE_INDEX_BYTES:
                raise ValueError(f"index.json exceeds the {_MAX_REMOTE_INDEX_BYTES:,}-byte cap")
    finally:
        response.close()
    index = json.loads(body.decode("utf-8"))
    if not isinstance(index, dict):
        raise ValueError("index.json is not a JSON object")
    return index


def check_for_updates(
    resources: Sequence[UpdatableResource],
    *,
    cancelled: Callable[[], bool] = lambda: False,
    fetch: Callable[[str], dict[str, Any]] = fetch_remote_index,
) -> UpdateCheck:
    """Ask each publisher for its latest revision; one failure never stops the rest.

    The published ``downloadUrl`` wins over the saved one when it is https,
    as in Yomitan.
    """
    updates: list[ResourceUpdate] = []
    failures: list[tuple[UpdatableResource, str]] = []
    for resource in resources:
        if cancelled():
            break
        # Everything that reads the publisher's answer stays inside the try:
        # a revision past int()'s digit limit must fail this publisher alone.
        try:
            remote = fetch(resource.index_url)
            latest_revision = str(remote.get("revision", "")).strip()
            if not latest_revision:
                raise ValueError("the published index.json has no revision")
            if not is_newer_revision(resource.revision, latest_revision):
                continue
            published = remote.get("downloadUrl")
            update = ResourceUpdate(
                resource=resource,
                latest_title=str(remote.get("title", "")).strip() or resource.title,
                latest_revision=latest_revision,
                download_url=published if _is_https(published) else resource.download_url,
            )
        except Exception as exc:  # noqa: BLE001 — isolate one publisher's failure
            logger.warning(
                "Update check failed: slot=%s url=%s error=%s: %s",
                resource.slot_id,
                resource.index_url,
                type(exc).__name__,
                exc,
            )
            failures.append((resource, str(exc) or type(exc).__name__))
            continue
        updates.append(update)
    return UpdateCheck(checked=len(resources), updates=tuple(updates), failures=tuple(failures))


def update_check_due(stamp: Path, *, now: float | None = None) -> bool:
    """True unless a check completed within the last week (a future stamp counts as due)."""
    try:
        last = stamp.stat().st_mtime
    except OSError:
        return True
    current = time.time() if now is None else now
    return not 0 <= current - last < UPDATE_INTERVAL_SECONDS


def mark_update_checked(stamp: Path) -> None:
    """Record a completed check. Never raises: a missed stamp only means an early re-check."""
    try:
        stamp.parent.mkdir(parents=True, exist_ok=True)
        stamp.touch()
    except OSError as exc:
        logger.warning("Could not record the resource update check: %s", exc)
