"""Generic in-app installer for downloadable dependency packs.

Stateless, GUI-free service. A *pack* is a set of :class:`PackComponent`s —
each one top-level Python package pinned to a sha256-verified PyPI artifact —
installed into one managed root directory that is then appended to
``sys.path`` so a plain ``import <name>`` resolves. Two adapters build on it:

* ``services/language_pack_installer.py`` — the per-language engine packs
  (``language_packs/<code>/``, manifests in ``languages/<code>/pack.py``),
  plus the read-only legacy ``ko_model/`` tier.
* ``services/asr/asr_pack_installer.py`` — the ASR engine pack
  (``asr_pack/``, manifest ``services/asr/asr_pack.py``): faster-whisper,
  CTranslate2, PyAV and their exclusive dependencies, which the frozen bundle
  excludes at Analysis time.

The rules the adapters share, and why:

* A component is satisfied by an extracted directory whose sentinels are all
  present AND whose declared root members sit beside it, or by a package
  importable from OUTSIDE the pack roots (a pip install with the matching extra
  needs no pack). The outside qualifier is load-bearing: a pack root is on
  ``sys.path`` as soon as any one of its components is complete, so from then on
  ``find_spec`` resolves the pack's OWN copy of every package it holds, and an
  importable tier that accepted that would report a damaged component — an
  antivirus quarantine of an extension module, a half-deleted package dir — as
  installed, disabling the one button that would repair it.
* Because the root IS the ``sys.path`` entry, a wheel's top-level siblings —
  kiwipiepy's ``_kiwipiepy.abi3.so``, or the auditwheel/delvewheel
  ``<pkg>.libs/`` directory whose shared libraries the extension resolves by an
  ``$ORIGIN``-relative rpath — are promoted there beside the package dir
  (``ArtifactSpec.root_members``; a prefix ending in ``/`` names a directory).
* Placement is atomic staging: members are extracted into a private staging dir
  *inside* the root (same filesystem), root members are promoted first and the
  package dir last with ``os.replace``, so no partial package is ever visible.
  The downloaded ``.part`` artifact is always removed (success, failure, or
  cancel).
"""

from __future__ import annotations

import logging
import os
import platform
import shutil
import sys
import tarfile
import tempfile
import zipfile
from collections.abc import Callable, Iterable, Iterator, Sequence
from importlib.util import find_spec
from pathlib import Path, PurePosixPath
from urllib.parse import urlsplit

from anki_miner.exceptions import OperationCancelled, SetupError
from anki_miner.interfaces.progress import DownloadProgressFn
from anki_miner.languages.pack_spec import ArtifactSpec, PackComponent
from anki_miner.services._install_common import cleanup_part, sweep_stale, verify_sha256
from anki_miner.services.resource_downloader import download_to_temp
from anki_miner.utils.atomic_io import atomic_replace_dir, reconcile_dir
from anki_miner.utils.logging_ext import log_summary

logger = logging.getLogger(__name__)

__all__ = [
    "MAX_ARTIFACT_BYTES",
    "append_to_syspath",
    "artifact_for",
    "component_complete",
    "components_supported",
    "importable_outside",
    "install_components",
    "installed_dir",
    "root_has_complete_component",
    "root_member_present",
]

#: The largest pinned artifact is the ~88 MB Korean model, then the ~40 MB
#: Linux ctranslate2 wheel; cap well below resource_downloader's 600 MB default
#: so a wrong or oversized download fails fast instead of filling the disk.
MAX_ARTIFACT_BYTES = 200 * 1024 * 1024


def artifact_for(comp: PackComponent) -> ArtifactSpec | None:
    """Return the artifact to download for *comp* here, or None.

    ``None`` when the component pins a CPython ABI this interpreter is not (the
    wheel would be ABI-incompatible) or no artifact is pinned for this
    platform/arch.
    """
    if comp.abi is not None and sys.version_info[:2] != comp.abi:
        return None
    if comp.universal is not None:
        return comp.universal
    if comp.per_platform is None:
        return None
    return comp.per_platform.get((sys.platform, platform.machine()))


def components_supported(components: Iterable[PackComponent]) -> bool:
    """Return True when every REQUIRED component resolves an artifact here.

    Optional components are ignored: opencc has no wheel for some platforms and
    Chinese still mines without it, so their absence must not make the whole
    pack undownloadable.
    """
    return all(artifact_for(comp) is not None for comp in components if comp.required)


def _resolved(path: Path) -> Path:
    """Resolve *path* for comparison, tolerating an unresolvable one."""
    try:
        return path.resolve()
    except OSError:  # pragma: no cover - resolve() is non-strict; only exotic FS errors land here
        return path


def _spec_locations(spec: object) -> Iterator[Path]:
    """Yield the filesystem locations *spec* would import from.

    ``origin`` is None for a namespace package — exactly what a pack dir whose
    ``__init__.py`` went missing becomes — so its search locations are read too,
    and they are what identifies it. Built-in and frozen modules yield nothing,
    which is right: they are never pack-provided.
    """
    origin = getattr(spec, "origin", None)
    if isinstance(origin, str) and origin not in ("built-in", "frozen"):
        yield Path(origin)
    for entry in getattr(spec, "submodule_search_locations", None) or ():
        if isinstance(entry, str):
            yield Path(entry)


def importable_outside(name: str, roots: Sequence[Path]) -> bool:
    """Return True when *name* imports from somewhere outside every *roots* entry.

    The pip tier: a source install with the matching extra has the package in
    site-packages and needs no pack at all. A resolution that lands INSIDE a
    pack root is not an answer (see the module docstring). Nothing is imported;
    ``find_spec`` only locates.
    """
    try:
        spec = find_spec(name)
    except (ImportError, ValueError):
        return False
    if spec is None:
        return False
    resolved_roots = [_resolved(root) for root in roots]
    for location in _spec_locations(spec):
        resolved = _resolved(location)
        if any(resolved == root or root in resolved.parents for root in resolved_roots):
            return False
    return True


def _sentinels_present(directory: Path, sentinels: tuple[str, ...]) -> bool:
    """Return True when every sentinel of an extracted component is on disk.

    Every sentinel must be present: an engine that loads all of them treats a
    directory missing one as a crash at parse time, not a degraded start.
    ``reconcile_dir`` first, so a crash inside ``atomic_replace_dir`` that left
    only a ``.bak-`` sibling is recovered rather than reported as missing.
    """
    reconcile_dir(directory)
    return all((directory / name).is_file() for name in sentinels)


def _root_members_for(comp: PackComponent) -> tuple[str, ...]:
    """Return the root-member prefixes *comp*'s artifact declares here.

    Empty when no artifact resolves for this platform/Python: an already-extracted
    pack from another interpreter must not be judged against prefixes we cannot
    read off a spec.
    """
    spec = artifact_for(comp)
    return () if spec is None else spec.root_members


def root_member_present(base: Path, prefix: str) -> bool:
    """Return True when the root member *prefix* names has landed under *base*.

    Two forms. A prefix ending in ``/`` is a directory: present when it holds
    at least one file (an empty ``av.libs/`` is no payload). Any other prefix is
    matched against file names in its parent, so one manifest entry
    (``_kiwipiepy.``) covers ``.abi3.so`` and ``.pyd``, and any auditwheel
    sibling that shares the stem.
    """
    if prefix.endswith("/"):
        directory = base / PurePosixPath(prefix)
        try:
            return any(entry.is_file() for entry in directory.rglob("*"))
        except OSError:
            return False
    pure = PurePosixPath(prefix)
    parent = base / pure.parent
    try:
        return any(entry.is_file() and entry.name.startswith(pure.name) for entry in parent.iterdir())
    except OSError:
        return False


def component_complete(base: Path, comp: PackComponent) -> bool:
    """Return True when *base* holds a usable extraction of *comp*.

    Both halves must be there: the package dir with every sentinel, AND one
    payload per declared root-member prefix beside it. A kiwipiepy dir without
    ``_kiwipiepy.abi3.so`` — or a ctranslate2 dir without ``ctranslate2.libs/`` —
    passes its sentinels and raises on import, so counting it as installed
    would be an unrepairable state — nothing would ever download the missing
    half.
    """
    if not _sentinels_present(base / comp.import_name, comp.sentinels):
        return False
    return all(root_member_present(base, prefix) for prefix in _root_members_for(comp))


def installed_dir(comp: PackComponent, bases: Sequence[Path]) -> Path | None:
    """Return the extracted directory providing *comp* from *bases*, best first."""
    for base in bases:
        if component_complete(base, comp):
            return base / comp.import_name
    return None


def _check_cancelled(cancelled_check: Callable[[], bool] | None, display_noun: str) -> None:
    if cancelled_check is not None and cancelled_check():
        raise OperationCancelled(f"{display_noun} installation cancelled")


def install_components(
    label: str,
    components: Sequence[PackComponent],
    root: Path,
    *,
    display_noun: str,
    satisfied: Callable[[PackComponent], bool],
    progress: DownloadProgressFn | None = None,
    cancelled_check: Callable[[], bool] | None = None,
) -> Path:
    """Download, verify, and install every unsatisfied component into *root*.

    Components *satisfied* reports True for are skipped (the adapter decides
    what satisfied means for the root it is filling — see
    ``language_pack_installer.component_satisfied``). Each remaining component
    is downloaded to a ``.part`` file inside *root*, sha256-verified, extracted
    into a fresh staging dir and atomically ``os.replace``d onto
    ``root/<import_name>``, with any declared root members promoted beside it.
    The ``.part`` artifact is always removed. A cancellation or any failure
    leaves nothing partial promoted; components installed earlier in the same
    call stay.

    Args:
        label: Short pack identifier (``"ko"``, ``"asr"``): names the resume
            key and prefixes the progress line (``"ASR pack (1/11): downloading"``),
            which the GUI task relabels.
        components: The pack's components, in install order.
        root: Managed directory for the pack; created if missing.
        display_noun: What the pack is called in the two user-facing errors
            (``"ko language pack"``, ``"ASR engine pack"``) — kept separate from
            *label* so the language adapter's messages read exactly as before.
        satisfied: Predicate deciding which components need no download.
        progress: Optional ``(downloaded, total, message)`` callback.
        cancelled_check: Optional zero-arg predicate. Checked before each heavy
            step (download, verify, extract); on cancellation no partial package
            is promoted and ``OperationCancelled`` is raised.

    Returns:
        The *root* path.

    Raises:
        SetupError: When a required component has no artifact for this
            platform/Python, or on download failure, sha256 mismatch, or a
            bad/empty archive.
        OperationCancelled: When *cancelled_check* returns True.
    """
    _check_cancelled(cancelled_check, display_noun)

    # Resolved up front so an unsupported platform refuses before any bytes are
    # fetched, rather than half-installing and failing on the last component.
    plan: list[tuple[PackComponent, ArtifactSpec]] = []
    for comp in components:
        if satisfied(comp):
            continue
        spec = artifact_for(comp)
        if spec is None:
            if comp.required:
                raise SetupError(
                    f"The {display_noun} is not supported on this platform/Python "
                    f"({sys.platform}/{platform.machine()}/"
                    f"{sys.version_info[0]}.{sys.version_info[1]})."
                )
            logger.info("Pack %s: no %s artifact for this platform; skipping", label, comp.import_name)
            continue
        plan.append((comp, spec))

    root.mkdir(parents=True, exist_ok=True)
    # Reclaim orphans from a previous crashed/killed install (a hard kill between
    # download and os.replace leaves a .part artifact and/or a .staging-* dir).
    # Promoted package dirs are never touched, so satisfaction is unaffected.
    sweep_stale(root)

    total = len(plan)
    for index, (comp, spec) in enumerate(plan, start=1):
        logger.info(
            "Pack install: pack=%s component=%s host=%s",
            label,
            comp.import_name,
            urlsplit(spec.url).hostname or "-",
        )
        _check_cancelled(cancelled_check, display_noun)

        # The label, not a translated name: this service is GUI-free and the
        # caller relabels. i/n counts what this run actually downloads.
        line = f"{label.upper()} pack ({index}/{total}): downloading"

        def _on_progress(downloaded: int, artifact_total: int, _msg: str, line: str = line) -> None:
            if progress is not None:
                progress(downloaded, artifact_total, line)

        part_path = download_to_temp(
            spec.url,
            dest_dir=root,
            progress=_on_progress if progress is not None else None,
            cancelled_check=cancelled_check,
            max_bytes=MAX_ARTIFACT_BYTES,
            # Keyed on the pinned checksum, so the key names exactly the bytes it
            # stands for: a pin bump changes the sha and therefore the key, and a
            # stale partial from the old artifact is never resumed into the new
            # one (D16-C).
            resume_key=f"pack-{label}-{comp.import_name}-{spec.sha256[:16]}",
        )
        try:
            _check_cancelled(cancelled_check, display_noun)
            verify_sha256(part_path, spec.sha256, f"{comp.import_name} download")
            _check_cancelled(cancelled_check, display_noun)
            _extract_component(part_path, root, comp, spec)
        finally:
            cleanup_part(part_path)

    byte_count = sum(path.stat().st_size for path in root.rglob("*") if path.is_file())
    log_summary(
        logger,
        "Pack install done",
        pack=label,
        installed=root,
        components=total,
        bytes=byte_count,
    )
    return root


def _safe_member_path(base: Path, member: str) -> Path:
    """Resolve *member* under *base*, rejecting path traversal (zip/tar slip)."""
    base_resolved = base.resolve()
    dest = (base / member).resolve()
    if base_resolved != dest and base_resolved not in dest.parents:
        raise SetupError(f"unsafe path in the {base.name} archive: {member}")
    return dest


def _wanted(name: str, spec: ArtifactSpec) -> str | None:
    """Return the package-relative path for archive member *name*, or None.

    None for anything outside ``member_prefix`` (a wheel's ``.dist-info``, an
    sdist's ``PKG-INFO``) and for anything an ``exclude`` entry matches. An
    entry ending in ``/`` is a directory prefix and takes the whole subtree;
    every other entry matches that EXACT relative path and nothing else.

    Plain prefix matching also swallowed each excluded file's neighbours a
    suffix away: jieba's ``finalseg/prob_start.p`` exclude took
    ``finalseg/prob_start.py`` with it — the table CPython imports — so the
    installed pack died on ``import jieba``.
    """
    if not name.startswith(spec.member_prefix):
        return None
    relative = name[len(spec.member_prefix) :]
    excluded = any(relative.startswith(entry) if entry.endswith("/") else relative == entry for entry in spec.exclude)
    if not relative or excluded:
        return None
    return relative


def _wanted_root(name: str, spec: ArtifactSpec) -> str | None:
    """Return the pack-root-relative path for a declared root member, or None.

    Matched against the RAW archive member name, and the member keeps that path
    under the pack root. For a wheel — the only kind that has these in practice —
    the archive root is the layout site-packages would get, so
    ``_kiwipiepy.abi3.so`` lands directly beside ``kiwipiepy/`` and
    ``av.libs/libavcodec-*.so`` under ``av.libs/`` beside ``av/``.
    """
    if not any(name.startswith(prefix) for prefix in spec.root_members):
        return None
    return name


def _member_destination(name: str, spec: ArtifactSpec, pkg_dir: Path, root_stage: Path) -> tuple[Path, bool] | None:
    """Return where archive member *name* is staged and whether it is a root member."""
    relative = _wanted(name, spec)
    if relative is not None:
        return _safe_member_path(pkg_dir, relative), False
    root_relative = _wanted_root(name, spec)
    if root_relative is not None:
        return _safe_member_path(root_stage, root_relative), True
    return None


def _extract_wheel(part_path: Path, pkg_dir: Path, root_stage: Path, spec: ArtifactSpec) -> int:
    """Stream the wheel's wanted members out; return the PACKAGE member count.

    Root members are staged too but not counted: the gate on them is per declared
    prefix, checked on the staged tree.
    """
    package_count = 0
    with zipfile.ZipFile(part_path) as zf:
        for name in zf.namelist():
            if name.endswith("/"):
                continue
            target = _member_destination(name, spec, pkg_dir, root_stage)
            if target is None:
                continue
            dest, is_root = target
            dest.parent.mkdir(parents=True, exist_ok=True)
            # Streamed: a wheel member can be tens of MB and reading it whole
            # would spike the resident set of a GUI process.
            with zf.open(name) as source, dest.open("wb") as out:
                shutil.copyfileobj(source, out)
            if not is_root:
                package_count += 1
    return package_count


def _extract_sdist(part_path: Path, pkg_dir: Path, root_stage: Path, spec: ArtifactSpec) -> int:
    """Stream the sdist's wanted members out; return the PACKAGE member count."""
    package_count = 0
    with tarfile.open(part_path, mode="r:gz") as tf:
        for member in tf:
            if not member.isfile():
                continue
            target = _member_destination(member.name, spec, pkg_dir, root_stage)
            if target is None:
                continue
            dest, is_root = target
            dest.parent.mkdir(parents=True, exist_ok=True)
            source = tf.extractfile(member)
            if source is None:  # pragma: no cover - isfile() already excludes these
                continue
            with source, dest.open("wb") as out:
                shutil.copyfileobj(source, out)
            if not is_root:
                package_count += 1
    return package_count


def _promote_root_members(root_stage: Path, root: Path) -> None:
    """Move every staged root member into the pack root, keeping its path."""
    for staged in sorted(root_stage.rglob("*")):
        if not staged.is_file():
            continue
        dest = root / staged.relative_to(root_stage)
        dest.parent.mkdir(parents=True, exist_ok=True)
        os.replace(staged, dest)


def _extract_component(part_path: Path, root: Path, comp: PackComponent, spec: ArtifactSpec) -> None:
    """Extract one component's package tree and atomically promote it.

    Members under ``spec.member_prefix`` are written into a fresh staging dir
    with their relative structure preserved and the prefix stripped; packaging
    metadata and excluded subtrees are skipped. Members matching
    ``spec.root_members`` are staged separately, because they belong at the pack
    root rather than inside the package dir.

    Promotion order is root members first, package dir last: the package dir is
    what satisfaction keys on, so it becomes visible only once its siblings are
    already in place.
    """
    staging = Path(tempfile.mkdtemp(prefix=f".staging-pack-{comp.import_name}-", dir=root))
    pkg_dir = staging / comp.import_name
    # A sibling of pkg_dir inside the same staging dir, so a root member named
    # after the package cannot collide with the package tree mid-extraction.
    root_stage = staging / "__root__"
    try:
        try:
            if spec.kind == "wheel":
                extracted = _extract_wheel(part_path, pkg_dir, root_stage, spec)
            else:
                extracted = _extract_sdist(part_path, pkg_dir, root_stage, spec)
        except (zipfile.BadZipFile, tarfile.TarError) as exc:
            logger.warning(
                "Pack install failed: stage=extract component=%s exc=%s",
                comp.import_name,
                type(exc).__name__,
            )
            raise SetupError(f"the {comp.import_name} download is not a valid archive: {exc}") from exc

        if not extracted:
            raise SetupError(f"the {comp.import_name} archive contained no {spec.member_prefix} payload")

        missing = [name for name in comp.sentinels if not (pkg_dir / name).is_file()]
        # A repackaged wheel that moved or dropped the extension module must
        # refuse here, not promote a package dir whose import raises.
        missing += [prefix + "*" for prefix in spec.root_members if not root_member_present(root_stage, prefix)]
        if missing:
            raise SetupError(f"the {comp.import_name} archive is missing {', '.join(missing)}")

        _promote_root_members(root_stage, root)
        atomic_replace_dir(pkg_dir, root / comp.import_name)
    finally:
        # Best-effort cleanup on success or an already-failing extraction path.
        shutil.rmtree(staging, ignore_errors=True)


def append_to_syspath(directory: Path) -> bool:
    """Append *directory* to ``sys.path`` if absent; return True if appended.

    Append, never insert: a pack root holds only the packages the pack
    installed, so it never needs to win priority, and appending means it cannot
    shadow a same-named module already on the path. The caller invalidates the
    import caches once after its batch of appends.
    """
    entry = str(directory)
    if entry in sys.path:
        return False
    sys.path.append(entry)
    return True


def root_has_complete_component(root: Path, components: Iterable[PackComponent]) -> bool:
    """Return True when *root* holds at least one sentinel-complete component.

    The boot-injection gate: a root with one complete component is worth
    putting on ``sys.path`` (the rest may still be downloading, and the
    adapters' ``is_installed`` keeps answering from the disk).
    """
    return any(component_complete(root, comp) for comp in components)
