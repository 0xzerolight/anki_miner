"""Named settings profiles stored as sidecar snapshots beside gui_config.json.

Layout::

    ~/.anki_miner/
      gui_config.json      # the live config — NOT owned by this module
      profiles/
        anime.json         # full config snapshot + "profile_name": "Anime"
        novels.json

There is deliberately **no index file**: the directory listing enumerates the
profiles (id = filename stem), each file carries its own display name, and the
active id is the ``active_profile_id`` marker inside gui_config.json
(:attr:`GUIConfigManager.ACTIVE_PROFILE_ID`). An index would duplicate both and
buy a whole class of marker-vs-index divergence bugs.

Storage layer only — Qt-free, no active-profile policy (that is caller policy).
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any

from anki_miner.config import AnkiMinerConfig
from anki_miner.gui.utils.config_manager import _CONFIG_MAX_BYTES, GUIConfigManager
from anki_miner.utils.atomic_io import atomic_write_path
from anki_miner.utils.bounded_reader import read_json_bounded
from anki_miner.utils.slug import slugify

logger = logging.getLogger(__name__)

MAX_PROFILES = 50

# JSON-only marker key holding a profile's display name. Popped by
# GUIConfigManager._migrate_dict, so it never reaches AnkiMinerConfig.
_NAME_KEY = "profile_name"

_PROFILES_DIRNAME = "profiles"

# Every separator, on every platform — not just the running one. A restored or
# hand-edited gui_config.json can carry a Windows-style active_profile_id and be
# read back on Linux. See _validate_id.
_ID_SEPARATORS = frozenset(sep for sep in ("/", "\\", os.sep, os.altsep) if sep)

# Sentinel for the raw reads. Distinct object so a legitimately-decoded ``None``
# is not mistaken for a read failure.
_UNREADABLE = object()


@dataclass(frozen=True)
class Profile:
    """A named profile: its on-disk id (filename stem) and display name."""

    id: str
    name: str


class ProfileStore:
    """Read/write access to the profile sidecar files.

    All-classmethod, mirroring :class:`GUIConfigManager`'s shape. Every path is
    derived from ``GUIConfigManager.CONFIG_FILE`` at call time, never snapshotted.
    """

    @classmethod
    def profiles_dir(cls) -> Path:
        """Return the profiles directory, computed fresh on every call.

        Derived from ``GUIConfigManager.CONFIG_FILE`` rather than from
        ``ANKI_MINER_HOME`` at import: tests/_home_isolation.py retargets that
        class attribute per test, so profile files follow the tmp home
        automatically. A module- or class-level snapshot would keep pointing at
        the user's real ~/.anki_miner and trip the ``_guard_real_home`` tripwire.
        """
        return GUIConfigManager.CONFIG_FILE.parent / _PROFILES_DIRNAME

    @classmethod
    def list_profiles(cls) -> tuple[Profile, ...]:
        """Enumerate stored profiles, sorted by display name (case-insensitive).

        Never raises. A missing directory yields ``()``, a directory that cannot
        be scanned at all is *also* reported as ``()``, and a member file that is
        unreadable, undecodable, non-dict, or missing a usable ``profile_name``
        falls back to its filename stem as the display name.

        Callers that WRITE on the strength of an empty result must use
        :meth:`scan_profiles` instead and handle its ``None`` — see the warning
        there.
        """
        profiles = cls.scan_profiles()
        return () if profiles is None else profiles

    @classmethod
    def scan_profiles(cls) -> tuple[Profile, ...] | None:
        """Enumerate stored profiles, or ``None`` when the scan itself failed.

        ``None`` means UNKNOWN, not empty, and that distinction is the whole
        point of this method existing beside :meth:`list_profiles`: a transient
        permission or I/O error on the directory must never read as "no profile
        has ever been created". A caller that acts on emptiness by writing (the
        boot reconcile adopts the live config as ``default.json``) would
        overwrite a real profile it simply could not see, and profile files have
        no ``.bak``.

        A missing directory is a legitimate empty state and still yields ``()``.
        Member files keep the lenient behaviour described on
        :meth:`list_profiles` — the strictness here is about the enumeration
        only. Only ``profile_name`` is read; the full config is not decoded.
        """
        directory = cls.profiles_dir()
        paths: list[Path] = []
        try:
            with os.scandir(directory) as entries:
                for entry in entries:
                    if len(paths) >= MAX_PROFILES:
                        logger.warning(
                            "Profile directory entry cap (%d) reached for %s; ignoring the rest",
                            MAX_PROFILES,
                            directory,
                        )
                        break
                    if entry.name.startswith("."):
                        # atomic_write_path stages ".<stem>-<rand>.json" siblings,
                        # which pass the .json test below. Its finally-unlink does
                        # not run on SIGKILL or power loss, so a crash mid-write
                        # would otherwise leave a permanent phantom profile: a
                        # second entry showing the same display name, blocking
                        # create/rename with an unexplainable "already exists" the
                        # UI offers no way to clear, and eating a MAX_PROFILES slot.
                        continue
                    if entry.name.endswith(".json") and entry.is_file():
                        paths.append(Path(entry.path))
        except FileNotFoundError:
            # No profile has ever been created — the normal empty state.
            return ()
        except OSError as exc:
            # The directory is there but unreadable. Reported as UNKNOWN, never
            # as empty: see this method's docstring.
            logger.warning("Could not enumerate profile directory %s: %s", directory, exc)
            return None

        profiles = [Profile(id=path.stem, name=cls._read_display_name(path)) for path in paths]
        profiles.sort(key=lambda profile: (profile.name.casefold(), profile.id))
        return tuple(profiles)

    @classmethod
    def read_profile(cls, profile_id: str) -> AnkiMinerConfig:
        """Load a profile snapshot as a full :class:`AnkiMinerConfig`.

        Goes through ``GUIConfigManager._parse_and_migrate`` so an old profile
        file migrates exactly like an old gui_config.json (bounded read,
        string→Path, field renames, all four chain rebuilds, schema shims, type
        decoding). ``archive_future=False`` because
        ``_archive_future_schema_config`` derives its archive filename from
        ``CONFIG_FILE`` — a future-schema sidecar would be archived under a
        misleading gui_config name.

        **Errors propagate. This must never fall back to create_default_config().**
        The adjacent ``GUIConfigManager.load_config_with_provenance`` swallows
        read/decode failures into factory defaults; copying that here is a
        data-loss trap. A truncated anime.json would silently become factory
        defaults, the caller's switch would look like it succeeded, and the next
        switch-away would overwrite the file with those defaults. Profile files
        have no .bak, so that loss is permanent.

        Raises:
            FileNotFoundError: If no file exists for ``profile_id``.
            ValueError: If ``profile_id`` is not a plain filename stem, or the
                file is unreadable, oversized, undecodable, not a JSON object,
                or holds a value the config rejects. Note that a file which
                exists but cannot be read (permission denied, I/O error) also
                lands HERE and not on OSError: read_json_bounded swallows read
                OSErrors into its sentinel, which _parse_and_migrate turns into
                _ConfigReadError — itself a ValueError. No read-side OSError
                escapes this method.
            TypeError: If the decoded data cannot build an AnkiMinerConfig.
        """
        path = cls._path_for(profile_id)
        # Explicit existence check: the bounded reader underneath
        # _parse_and_migrate swallows OSError, so a missing file would otherwise
        # surface as an opaque "could not decode" ValueError and callers could
        # not tell a deleted profile from a corrupt one.
        if not path.exists():
            raise FileNotFoundError(f"No profile file for '{profile_id}': {path}")
        return GUIConfigManager._parse_and_migrate(path, archive_future=False)

    @classmethod
    def write_profile(cls, profile_id: str, config: AnkiMinerConfig, *, name: str) -> None:
        """Atomically write a full config snapshot plus its display name.

        Reuses ``GUIConfigManager``'s serializers, NOT its write path:
        ``save_config``'s .bak rotation is interleaved inside the same try
        between the tmp write and os.replace, and pulling that apart re-opens
        the one-overwrite-recovery data-loss bug its comments document. Profile
        files get no .bak of their own.

        Raises:
            ValueError: If ``profile_id`` is not a plain filename stem.
            OSError: If the directory or file cannot be written.
        """
        directory = cls.profiles_dir()
        directory.mkdir(parents=True, exist_ok=True)

        data = GUIConfigManager._paths_to_strings(GUIConfigManager._config_to_serializable_dict(config))
        # Stamp the schema version so _parse_and_migrate reads the file back
        # with the right shims. ``active_profile_id`` is deliberately NOT
        # stamped: that key means "which profile the LIVE config is", and a
        # profile file claiming an identity would poison the boot reconcile if a
        # user ever restored one over gui_config.json.
        data["config_schema_version"] = GUIConfigManager.CONFIG_SCHEMA_VERSION
        data[_NAME_KEY] = name

        cls._write_json(cls._path_for(profile_id), data)

    @classmethod
    def create(cls, name: str, config: AnkiMinerConfig) -> Profile:
        """Create a new profile holding ``config``, returning its identity.

        Raises:
            ValueError: If ``name`` is blank, collides case-insensitively with
                an existing display name, or the store is already at
                :data:`MAX_PROFILES`.
            OSError: If the file cannot be written.
        """
        existing = cls.list_profiles()
        clean = cls._validate_name(name, existing)
        if len(existing) >= MAX_PROFILES:
            raise ValueError(f"Profile limit reached ({MAX_PROFILES})")

        profile_id = cls._allocate_id(clean)
        cls.write_profile(profile_id, config, name=clean)
        return Profile(id=profile_id, name=clean)

    @classmethod
    def rename(cls, profile_id: str, name: str) -> None:
        """Change only a profile's display name; the id never changes.

        Rewrites the ``profile_name`` key in place via a raw JSON read/write. It
        deliberately does NOT decode to AnkiMinerConfig and re-serialize: that
        round trip would drop unknown ``anki_fields`` sub-keys, which the
        codebase preserves forever (the field is typed ``Mapping[str, str]``, and
        real user files carry stale sub-keys).

        Raises:
            ValueError: If ``profile_id`` is not a plain filename stem, ``name``
                is blank or collides case-insensitively with another profile's
                display name, or the file is unreadable, oversized, undecodable,
                or not a JSON object. As in ``read_profile``, an existing but
                unreadable file lands here rather than on OSError — the bounded
                reader swallows read OSErrors into its sentinel.
            FileNotFoundError: If no file exists for ``profile_id``.
            OSError: If the rewritten file cannot be written.
        """
        clean = cls._validate_name(name, cls.list_profiles(), exclude_id=profile_id)

        path = cls._path_for(profile_id)
        if not path.exists():
            raise FileNotFoundError(f"No profile file for '{profile_id}': {path}")
        raw = read_json_bounded(path, _CONFIG_MAX_BYTES, _UNREADABLE, "profile")
        if raw is _UNREADABLE:
            raise ValueError(f"Could not decode profile file {path.name}")
        if not isinstance(raw, dict):
            raise ValueError(f"Invalid profile file {path.name}: expected a JSON object")

        raw[_NAME_KEY] = clean
        cls._write_json(path, raw)

    @classmethod
    def delete(cls, profile_id: str) -> None:
        """Remove a profile file.

        Deliberately enforces no policy — "not the active one", "not the last
        one" and any confirmation live with the caller.

        Raises:
            ValueError: If ``profile_id`` is not a plain filename stem.
            FileNotFoundError: If no file exists for ``profile_id``.
            OSError: If the file cannot be removed.
        """
        cls._path_for(profile_id).unlink()

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    @classmethod
    def _path_for(cls, profile_id: str) -> Path:
        """Map an id to its file, refusing any id that could escape the dir."""
        cls._validate_id(profile_id)
        return cls.profiles_dir() / f"{profile_id}.json"

    @staticmethod
    def _validate_id(profile_id: str) -> None:
        """Reject ids that are not a plain filename stem.

        This is the layer that owns the id→path mapping, so the guard belongs
        here rather than in each caller. Every id this module mints is
        ``slugify`` output ([a-z0-9-] only), so its own flows never trip it —
        but ids also arrive from disk. ``GUIConfigManager.read_active_profile_id``
        returns ANY non-empty string found in gui_config.json with no character
        validation, and that value is the designed input to the boot reconcile.
        Without this check a hand-edited or restored config carrying
        ``"active_profile_id": "../gui_config"`` would make ``read_profile``
        load the live config as a profile, ``write_profile`` stamp a
        ``profile_name`` into it, and ``delete`` unlink the user's settings.

        Raises:
            ValueError: If ``profile_id`` is empty, contains a path separator
                or ``..``, or does not resolve to a bare filename.
        """
        if not profile_id:
            raise ValueError("Profile id must not be empty")
        if ".." in profile_id or any(sep in profile_id for sep in _ID_SEPARATORS):
            raise ValueError(f"Invalid profile id: {profile_id!r}")
        filename = f"{profile_id}.json"
        if PurePosixPath(filename).name != filename or PureWindowsPath(filename).name != filename:
            raise ValueError(f"Invalid profile id: {profile_id!r}")

    @staticmethod
    def _read_display_name(path: Path) -> str:
        """Return the file's ``profile_name``, or its stem when unusable."""
        raw = read_json_bounded(path, _CONFIG_MAX_BYTES, _UNREADABLE, "profile")
        if isinstance(raw, dict):
            name = raw.get(_NAME_KEY)
            if isinstance(name, str) and name.strip():
                return name.strip()
        return path.stem

    @staticmethod
    def _validate_name(name: str, existing: tuple[Profile, ...], *, exclude_id: str | None = None) -> str:
        """Return the stripped name, rejecting blanks and case-insensitive dupes.

        Uniqueness is case-insensitive so the header combo can never show two
        entries a user reads as the same profile.
        """
        clean = name.strip()
        if not clean:
            raise ValueError("Profile name must not be empty")
        folded = clean.casefold()
        for profile in existing:
            if profile.id != exclude_id and profile.name.casefold() == folded:
                raise ValueError(f"A profile named '{clean}' already exists")
        return clean

    @classmethod
    def _allocate_id(cls, name: str) -> str:
        """Slug the name, suffixing -2, -3, … until the filename is free.

        ``slugify`` maps non-ASCII code points to ``u<hex>``, so a pure-CJK name
        yields a usable id (e.g. ``u30a2u30cb``) rather than an empty string.
        """
        base = slugify(name, fallback="profile")
        candidate = base
        suffix = 2
        while cls._path_for(candidate).exists():
            candidate = f"{base}-{suffix}"
            suffix += 1
        return candidate

    @staticmethod
    def _write_json(path: Path, data: dict[str, Any]) -> None:
        """Atomically replace ``path`` with ``data``, 0600 on POSIX."""
        with atomic_write_path(path) as tmp:
            if os.name == "posix":
                # atomic_write_path does not chmod. Do it on the temp file so the
                # profile is never group/world-readable, not even momentarily.
                os.chmod(tmp, 0o600)
            with tmp.open("w", encoding="utf-8") as handle:
                json.dump(data, handle, indent=2, ensure_ascii=False)
