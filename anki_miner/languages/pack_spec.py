"""Data shapes for downloadable dependency packs.

A language that needs third-party engines in frozen bundles ships a
``languages/<code>/pack.py`` exporting ``PACK: LanguagePack``. Japanese has
none: its engine is bundled. These types are shared by the per-language packs
(``languages/<code>/pack.py``) and the ASR engine pack (``services/asr/asr_pack.py``).
They are pure data so that importing a manifest can never pull an engine, a
downloader, or Qt.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Literal

#: ``…-macosx_11_0_arm64.whl`` -> (11, 0). macOS is the only platform whose
#: wheel tag encodes an OS floor, and dyld enforces it absolutely: a wheel built
#: against a newer SDK fails to load on an older release, taking the import with
#: it. A tag can name several platforms (``macosx_10_9_x86_64.macosx_11_0_arm64``),
#: so the LOWEST floor in the name wins — that is the release the artifact as a
#: whole still loads on.
_MACOS_TAG_RE = re.compile(r"macosx_(\d+)_(\d+)_")


def macos_floor_from_url(url: str) -> tuple[int, int] | None:
    """Return the macOS floor a pinned artifact's platform tag demands, or None.

    None for every non-macOS artifact (and for an sdist, which has no tag).
    """
    found = [(int(major), int(minor)) for major, minor in _MACOS_TAG_RE.findall(url.rsplit("/", 1)[-1])]
    return min(found) if found else None


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
    #: A prefix ending in "/" names a whole DIRECTORY promoted to the pack
    #: root — the auditwheel/delvewheel ``<pkg>.libs/`` tree an extension
    #: resolves by an ``$ORIGIN``-relative rpath (ctranslate2, av).
    root_members: tuple[str, ...] = ()

    @property
    def min_macos(self) -> tuple[int, int] | None:
        """The macOS release this artifact's platform tag demands, or None.

        Read off the pinned filename rather than declared beside it, so it can
        never drift from the wheel actually being downloaded — a hand-written
        floor is exactly what nobody updated when av's arm64 wheel moved from
        ``macosx_11_0`` to ``macosx_14_0`` and Apple Silicon Macs below macOS 14
        started failing to load the engine at all.
        """
        return macos_floor_from_url(self.url)


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


@dataclass(frozen=True)
class DependencyPack:
    """A named set of components installed together into one pack root.

    The language packs keep :class:`LanguagePack` (keyed by language code);
    every other pack — today the ASR engine pack — is one of these. The
    installer core (``services/pack_installer.py``) reads only ``components``
    and a label, so the two shapes share one implementation.
    """

    name: str
    approx_download_mb: int
    components: tuple[PackComponent, ...] = field(default=())
