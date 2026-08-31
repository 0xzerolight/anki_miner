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
    exclude: tuple[str, ...] = ()  # package-relative prefixes never extracted


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
