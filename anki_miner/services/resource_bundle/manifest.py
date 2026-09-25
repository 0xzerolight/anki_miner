"""What a resource bundle carries, and how its manifest is written and read back.

A resource bundle is a zip of one mining language's resources: the kept original
``source.*`` file of each dictionary, frequency and pitch slot (rebuilt on import
through the family importer, so a bundle outlives index schema bumps), the
known-words ignore list, and the blacklist/whitelist. ``manifest.json`` names
each member. Member paths are fixed by kind and id, so validating the manifest
is also what keeps extraction inside its temp directory.
"""

from __future__ import annotations

import json
import logging
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, cast, get_args

from anki_miner.languages import AVAILABLE_LANGUAGES
from anki_miner.services._sqlite_index import validate_store_id
from anki_miner.services.dictionary.zip_safety import read_member
from anki_miner.services.frequency.source_importer import FREQUENCY_SOURCE_SUFFIXES
from anki_miner.services.pitch_accent.source_importer import PITCH_SOURCE_SUFFIXES

logger = logging.getLogger(__name__)

BUNDLE_MARKER = "anki_miner_resources"
BUNDLE_FORMAT = 1
MANIFEST_MEMBER = "manifest.json"
_MANIFEST_MAX_BYTES = 1024 * 1024

ItemKind = Literal["dictionary", "frequency", "pitch", "known_words", "blacklist", "whitelist"]
ITEM_KINDS: tuple[str, ...] = get_args(ItemKind)
SLOT_KINDS: frozenset[str] = frozenset({"dictionary", "frequency", "pitch"})
WORDLIST_KINDS: frozenset[str] = frozenset({"blacklist", "whitelist"})

_SUFFIXES: dict[str, tuple[str, ...]] = {
    "dictionary": (".zip",),
    "frequency": FREQUENCY_SOURCE_SUFFIXES,
    "pitch": PITCH_SOURCE_SUFFIXES,
    "known_words": (".txt",),
    "blacklist": (".txt",),
    "whitelist": (".txt",),
}


class BundleError(ValueError):
    """The file is not a resource bundle this version can read."""


@dataclass(frozen=True)
class BundleItem:
    """One resource inside a bundle.

    ``item_id`` is the on-disk slot id for the slot kinds -- kept on import,
    because a dictionary's rendered media filenames embed it -- and the kind
    itself for the single-instance kinds. ``enabled`` is the sender's chain
    flag or ``use_blacklist``/``use_whitelist``. ``options`` holds the
    frequency import options a rebuild must replay (``declared_mode``,
    ``lemmatised``), sorted, as pairs so the item stays hashable.
    """

    kind: ItemKind
    item_id: str
    name: str
    member: str
    enabled: bool = True
    options: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True)
class BundleManifest:
    language: str
    app_version: str
    items: tuple[BundleItem, ...]


def member_name(kind: str, item_id: str, suffix: str) -> str:
    """The zip member path of one item, e.g. ``frequency/jpdb/source.csv``."""
    if kind in SLOT_KINDS:
        return f"{kind}/{item_id}/source{suffix}"
    return f"{kind}{suffix}"


def manifest_to_json(manifest: BundleManifest) -> str:
    payload = {
        BUNDLE_MARKER: BUNDLE_FORMAT,
        "app_version": manifest.app_version,
        "language": manifest.language,
        "items": [
            {
                "kind": item.kind,
                "id": item.item_id,
                "name": item.name,
                "member": item.member,
                "enabled": item.enabled,
                "options": dict(item.options),
            }
            for item in manifest.items
        ],
    }
    return json.dumps(payload, indent=2, ensure_ascii=False)


def parse_manifest(raw: object) -> BundleManifest:
    """Validate a decoded ``manifest.json``; raise :class:`BundleError` on anything off."""
    if not isinstance(raw, dict) or BUNDLE_MARKER not in raw:
        raise BundleError("not a resource bundle manifest")
    version = raw[BUNDLE_MARKER]
    if not isinstance(version, int) or isinstance(version, bool) or version < 1:
        raise BundleError(f"unreadable bundle format {version!r}")
    if version > BUNDLE_FORMAT:
        raise BundleError(f"bundle format {version} is newer than this app reads ({BUNDLE_FORMAT})")
    language = raw.get("language")
    if not isinstance(language, str) or language not in AVAILABLE_LANGUAGES:
        raise BundleError(f"unknown mining language {language!r}")
    raw_items = raw.get("items")
    if not isinstance(raw_items, list):
        raise BundleError("manifest has no item list")
    items: list[BundleItem] = []
    seen: set[tuple[str, str]] = set()
    for entry in raw_items:
        item = _parse_item(entry)
        if item is None:
            continue
        key = (item.kind, item.item_id)
        if key in seen:
            raise BundleError(f"duplicate item {item.kind}/{item.item_id}")
        seen.add(key)
        items.append(item)
    app_version = raw.get("app_version")
    return BundleManifest(
        language=language,
        app_version=app_version if isinstance(app_version, str) else "",
        items=tuple(items),
    )


def _parse_item(entry: object) -> BundleItem | None:
    if not isinstance(entry, dict):
        raise BundleError("manifest item is not an object")
    kind = entry.get("kind")
    if kind not in ITEM_KINDS:
        # A newer app may add kinds within the same format; skip, don't refuse.
        logger.warning("Resource bundle item of unknown kind skipped: kind=%r", kind)
        return None
    item_id, name, member = entry.get("id"), entry.get("name"), entry.get("member")
    if not (isinstance(item_id, str) and isinstance(name, str) and isinstance(member, str) and item_id and name):
        raise BundleError(f"manifest item {kind} is missing its id, name or member")
    if kind in SLOT_KINDS:
        try:
            validate_store_id(item_id)
        except ValueError as exc:
            raise BundleError(str(exc)) from exc
    elif item_id != kind:
        raise BundleError(f"manifest item {kind} has id {item_id!r}")
    if member not in {member_name(kind, item_id, suffix) for suffix in _SUFFIXES[kind]}:
        raise BundleError(f"unexpected member path {member!r}")
    enabled = entry.get("enabled", True)
    options = entry.get("options", {})
    if not isinstance(enabled, bool) or not isinstance(options, dict):
        raise BundleError(f"manifest item {kind}/{item_id} has a malformed flag or options")
    if not all(isinstance(key, str) and isinstance(value, str) for key, value in options.items()):
        raise BundleError(f"manifest item {kind}/{item_id} has non-text options")
    return BundleItem(
        kind=cast(ItemKind, kind),
        item_id=item_id,
        name=name,
        member=member,
        enabled=enabled,
        options=tuple(sorted(options.items())),
    )


def read_bundle_manifest(path: Path) -> BundleManifest:
    """Open ``path`` and parse its manifest; every member it names must be present.

    Raises:
        BundleError: Not a zip, no or malformed manifest, or a named member missing.
        OSError: The file cannot be opened.
    """
    try:
        with zipfile.ZipFile(path) as zf:
            names = set(zf.namelist())
            if MANIFEST_MEMBER not in names:
                raise BundleError("the file has no manifest.json")
            data = read_member(zf, MANIFEST_MEMBER, limit=_MANIFEST_MAX_BYTES)
    except zipfile.BadZipFile as exc:
        raise BundleError(f"not a zip file: {exc}") from exc
    if len(data) > _MANIFEST_MAX_BYTES:
        raise BundleError("manifest.json is too large")
    try:
        raw = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise BundleError(f"manifest.json is not valid JSON: {exc}") from exc
    manifest = parse_manifest(raw)
    missing = [item.member for item in manifest.items if item.member not in names]
    if missing:
        raise BundleError(f"the bundle is missing {', '.join(missing)}")
    return manifest
