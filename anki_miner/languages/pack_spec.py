"""Data shapes for per-language downloadable dependency packs.

A language that needs third-party engines in frozen bundles ships a
``languages/<code>/pack.py`` exporting ``PACK: LanguagePack``. Japanese has
none: its engine is bundled. These types are pure data so that importing a
manifest can never pull an engine, a downloader, or Qt.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Literal


@dataclass(frozen=True)
class ArtifactSpec:
    """One pinned PyPI artifact and how to unpack it."""

    url: str
    sha256: str
    kind: Literal["wheel", "sdist"]
    member_prefix: str  # archive prefix stripped on extraction, e.g. "jieba-0.42.1/jieba/"
    #: Package-relative paths never extracted. An entry ending in ``/`` is a
    #: directory prefix and drops the whole subtree; every other entry matches
    #: that EXACT path, so excluding ``finalseg/prob_start.p`` leaves the
    #: ``.py`` beside it alone.
    exclude: tuple[str, ...] = ()
    #: Archive member-name PREFIXES extracted alongside the package and placed
    #: at the PACK ROOT, keeping their archive-relative path. For the top-level
    #: sibling modules a wheel puts beside its package dir — kiwipiepy's
    #: ``_kiwipiepy.abi3.so``, which ``kiwipiepy/_wrap.py`` imports by name.
    #: The pack root is the ``sys.path`` entry, so that is where such a module
    #: has to land. Prefix form so one pin covers ``.abi3.so`` and ``.pyd``.
    root_members: tuple[str, ...] = ()


@dataclass(frozen=True)
class PackComponent:
    """One top-level package the pack installs."""

    import_name: str
    required: bool
    sentinels: tuple[str, ...]  # files under the package dir; ALL must exist
    universal: ArtifactSpec | None = None
    per_platform: Mapping[tuple[str, str], ArtifactSpec] | None = None
    abi: tuple[int, int] | None = None  # cpXX pin; None = pure-Python or abi3


@dataclass(frozen=True)
class LanguagePack:
    code: str
    approx_download_mb: int
    components: tuple[PackComponent, ...] = field(default=())
