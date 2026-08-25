"""Service for validating system setup and dependencies."""

import logging
import subprocess
import sys
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

import requests

from anki_miner.config import AnkiMinerConfig, paths
from anki_miner.exceptions import AnkiConnectionError
from anki_miner.models import ValidationIssue, ValidationResult
from anki_miner.services._ankiconnect import post_action
from anki_miner.services.anki_note_builder import (
    configured_target_field_names,
    field_mapping_error,
    field_target_collision_message,
)
from anki_miner.utils import ensure_directory
from anki_miner.utils.alass_resolver import resolve_alass
from anki_miner.utils.ffmpeg_resolver import resolve_ffmpeg, resolve_ffprobe
from anki_miner.utils.subprocess_utils import no_window_kwargs
from anki_miner.utils.ytdlp_resolver import managed_ytdlp_lock, resolve_ytdlp

logger = logging.getLogger(__name__)

#: Age at which a resolved yt-dlp earns a staleness nudge. Generous on purpose:
#: yt-dlp ships roughly monthly and a bundled binary is already weeks old on release
#: day, so this must not fire on a healthy install that simply has auto-update off
#: and a recent manual download.
_YTDLP_STALE_AFTER_DAYS = 120

#: How long :meth:`ValidationService._check_ytdlp` waits for the yt-dlp lock before
#: reporting it busy. Bounded, not zero: at startup the validation worker and the
#: scheduled auto-update overlap, and a cold-cache SHA-256 of the managed binary or a
#: ``--version`` probe holds the lock about a second — an instant give-up mislabelled
#: a healthy install as busy. 10s rides over those and is still nothing against the
#: 3h transfer this refuses to park behind.
_YTDLP_LOCK_WAIT_SECONDS = 10.0


@dataclass(frozen=True)
class ResourceReadiness:
    """What each resource family can actually do right now.

    One object rather than three separate probes because the setup wizard's
    page base keeps exactly one live-check worker as its generation counter --
    a second concurrent probe would have no way to be recognised as stale.

    The dictionary's ``bool`` and the other two families' ``bool | None`` are
    deliberately different types. ``None`` means "nothing configured", which
    for frequency and pitch is a legitimate resting state and must never be
    rendered as a problem; a dictionary has no such state, because without one
    every mined card comes out with no definition (D26).
    """

    dictionary: tuple[bool, str]
    frequency: tuple[bool | None, str]
    pitch: tuple[bool | None, str]


def _classify_resolved(base: str, resolved: str) -> str:
    """Classify a resolved external-binary path for the success message.

    Returns a short bracketed suffix describing where the binary came from:

    - ``[system PATH]`` — the resolver returned the bare literal (PATH lookup).
    - ``[bundled]`` — the resolved path lives under the frozen ``sys._MEIPASS``.
    - ``[app-managed]`` — an in-app download under ``ANKI_MINER_HOME``.
    - ``[venv]`` — a console script beside ``sys.executable`` (a pip/pipx install).
    - ``[custom path]`` — an explicit config override / any other absolute path.

    The app-managed and venv cases exist because yt-dlp resolves through tiers the
    ffmpeg-era version of this function had no concept of; labelling both
    "[custom path]" told the user their binary came from a setting they never set.
    """
    if resolved == base:
        return "[system PATH]"
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass is not None and resolved.startswith(str(meipass)):
        return "[bundled]"
    resolved_path = Path(resolved)
    if _is_within(resolved_path, paths.ANKI_MINER_HOME):
        return "[app-managed]"
    if _is_within(resolved_path, Path(sys.executable).parent):
        return "[venv]"
    return "[custom path]"


def _is_within(candidate: Path, directory: Path) -> bool:
    """True when *candidate* sits inside *directory* (best-effort, never raises)."""
    try:
        candidate.resolve().relative_to(directory.resolve())
    except (ValueError, OSError):
        return False
    return True


class ValidationService:
    """Validate system setup and dependencies (stateless service)."""

    def __init__(self, config: AnkiMinerConfig):
        """Initialize the validation service.

        Args:
            config: Configuration to validate against
        """
        self.config = config

    def validate_setup(self) -> ValidationResult:
        """Run all validation checks.

        Returns:
            ValidationResult with status of each check

        Note:
            This method never raises exceptions - all errors are captured
            in the ValidationResult.
        """
        issues = []
        # Success messages are otherwise discarded (issues carry only failures), but
        # the Settings → YouTube status line needs the resolved version + tier. This
        # check already runs off the GUI thread, so surfacing it here is what keeps a
        # `--version` subprocess off a panel-load path.
        tool_versions: dict[str, str] = {}

        # Check AnkiConnect
        ankiconnect_ok, anki_msg = self._check_ankiconnect()
        if not ankiconnect_ok:
            issues.append(
                ValidationIssue(
                    component="AnkiConnect",
                    severity="ERROR",
                    message=anki_msg,
                )
            )

        # Check ffmpeg
        ffmpeg_ok, ffmpeg_msg = self._check_ffmpeg()
        if not ffmpeg_ok:
            issues.append(
                ValidationIssue(
                    component="ffmpeg",
                    severity="ERROR",
                    message=ffmpeg_msg,
                )
            )

        # Check ffprobe (audio-track detection depends on it)
        ffprobe_ok, ffprobe_msg = self._check_ffprobe()
        if not ffprobe_ok:
            issues.append(
                ValidationIssue(
                    component="ffprobe",
                    severity="ERROR",
                    message=ffprobe_msg,
                )
            )

        # Check alass (optional — subtitle retiming only; absent is non-fatal)
        alass_ok, alass_msg = self._check_alass()
        if not alass_ok:
            issues.append(
                ValidationIssue(
                    component="alass",
                    severity="WARNING",
                    message=alass_msg,
                )
            )

        # Check yt-dlp (optional — YouTube mining only; absent is non-fatal).
        # Previously unchecked entirely, which is why a missing yt-dlp stayed
        # invisible until the user hit a per-row "Probe failed" in the YouTube tab.
        ytdlp_ok, ytdlp_msg = self._check_ytdlp()
        tool_versions["yt-dlp"] = ytdlp_msg if ytdlp_ok else ""
        if not ytdlp_ok:
            issues.append(
                ValidationIssue(
                    component="yt-dlp",
                    severity="WARNING",
                    message=ytdlp_msg,
                )
            )
        else:
            stale_msg = self._ytdlp_staleness_warning(ytdlp_msg)
            if stale_msg is not None:
                issues.append(
                    ValidationIssue(
                        component="yt-dlp",
                        severity="WARNING",
                        message=stale_msg,
                    )
                )

        # Check deck exists (only if AnkiConnect is working)
        deck_ok = False
        if ankiconnect_ok:
            deck_ok, deck_msg = self._check_deck_exists()
            if not deck_ok:
                issues.append(
                    ValidationIssue(
                        component="Anki Deck",
                        severity="ERROR",
                        message=deck_msg,
                    )
                )

        # Check note type exists (only if AnkiConnect is working)
        note_type_ok = False
        if ankiconnect_ok:
            note_type_ok, note_type_msg = self._check_note_type_exists()
            if not note_type_ok:
                issues.append(
                    ValidationIssue(
                        component="Note Type",
                        severity="ERROR",
                        message=note_type_msg,
                    )
                )

        # Check field names exist on note type (only if note type is valid)
        fields_ok = False
        if ankiconnect_ok and note_type_ok:
            fields_ok, fields_msg = self._check_field_names_exist()
            if not fields_ok:
                issues.append(
                    ValidationIssue(
                        component="Field Mapping",
                        severity="WARNING",
                        message=fields_msg,
                    )
                )

        # Ensure temp folder exists
        try:
            ensure_directory(self.config.media_temp_folder)
        except Exception as e:
            logger.exception("Unexpected error creating temp folder")
            issues.append(
                ValidationIssue(
                    component="Temp Folder",
                    severity="WARNING",
                    message=f"Could not create temp folder: {e}",
                )
            )

        # Offline dictionary readiness. Reported for its own sake, not as a
        # by-product of a file existing: mining without a usable offline
        # dictionary produces cards with no definition, which is the failure the
        # first-run setup used to let people walk into by calling the dictionary
        # optional. Still a WARNING — the chain can fall back to Jisho — but it
        # is now emitted for "none configured" too, which the old on-disk check
        # could not represent.
        dictionary_ok, dictionary_msg = self._check_offline_dictionary()
        if dictionary_ok:
            tool_versions["offline-dictionary"] = dictionary_msg
        else:
            issues.append(
                ValidationIssue(
                    component="Offline Dictionary",
                    severity="WARNING",
                    message=dictionary_msg,
                )
            )

        # Pitch/frequency "resource missing" warnings were removed with the
        # use_pitch_accent / use_frequency_data flags: activation is now derived
        # from the resource being present (pitch_active / frequency_active = an
        # enabled source in the chain), so a "wanted but missing" state is no
        # longer representable. That still holds.
        #
        # "Wanted but BROKEN" is a different state and is representable: the
        # source is enabled, on disk, and unusable because an app upgrade bumped
        # the index schema. Reported here so the silent failure it causes
        # (frequency: no rank and no rank filtering; pitch: blank field; audio
        # packs: cards fall through to the online sources) is not the user's
        # only clue. Nothing configured stays silent — all three families are
        # optional, and the checks return None for it.
        for component, key, check in (
            ("Frequency Sources", "frequency-sources", self._check_frequency_sources),
            ("Pitch Sources", "pitch-sources", self._check_pitch_sources),
            ("Audio Packs", "audio-packs", self._check_audio_packs),
        ):
            ok, message = check()
            if ok is None:
                # Not configured: the row renders "not configured (optional)".
                tool_versions[key] = ""
            elif ok:
                tool_versions[key] = message
            else:
                issues.append(ValidationIssue(component=component, severity="WARNING", message=message))

        return ValidationResult(
            ankiconnect_ok=ankiconnect_ok,
            ffmpeg_ok=ffmpeg_ok,
            ffprobe_ok=ffprobe_ok,
            deck_exists=deck_ok,
            note_type_exists=note_type_ok,
            field_mapping_ok=fields_ok,
            issues=issues,
            tool_versions=tool_versions,
        )

    def check_ankiconnect(self) -> tuple[bool, str]:
        """Public wrapper over :meth:`_check_ankiconnect` (setup wizard).

        Returns:
            Tuple of (success, message) — identical to the private method.
        """
        return self._check_ankiconnect()

    def check_field_names(self) -> tuple[bool, str]:
        """Public wrapper over the field-mapping check (setup wizard).

        Returns:
            Tuple of (success, message) — identical to the private method.
        """
        return self._check_field_names_exist()

    def check_deck_exists(self) -> tuple[bool, str]:
        """Public wrapper over :meth:`_check_deck_exists` (setup wizard).

        Returns:
            Tuple of (success, message) — identical to the private method.
        """
        return self._check_deck_exists()

    def check_note_type_exists(self) -> tuple[bool, str]:
        """Public wrapper over :meth:`_check_note_type_exists` (setup wizard).

        Returns:
            Tuple of (success, message) — identical to the private method.
        """
        return self._check_note_type_exists()

    def check_offline_dictionary(self) -> tuple[bool, str]:
        """Public wrapper over :meth:`_check_offline_dictionary` (setup wizard).

        Returns:
            Tuple of (success, message) — identical to the private method.
        """
        return self._check_offline_dictionary()

    def check_resource_readiness(self) -> ResourceReadiness:
        """Probe all three resource families in one pass (setup wizard).

        Goes through the public :meth:`check_offline_dictionary` for the
        dictionary leg so that wrapper stays the single dictionary-readiness
        entry point rather than becoming a second, drifting copy of the same
        question.

        Scans registry snapshots only -- see :meth:`_check_offline_dictionary`
        for why a readiness probe must never open a provider.
        """
        return ResourceReadiness(
            dictionary=self.check_offline_dictionary(),
            frequency=self._check_frequency_sources(),
            pitch=self._check_pitch_sources(),
        )

    def _check_ankiconnect(self) -> tuple[bool, str]:
        """Check if AnkiConnect is running and accessible.

        Returns:
            Tuple of (success, message)
        """
        try:
            version = post_action(
                self.config.ankiconnect_url,
                "version",
                timeout=5,
            )
        except AnkiConnectionError as e:
            cause = e.__cause__
            if isinstance(cause, requests.exceptions.ConnectionError):
                return False, "Cannot connect to Anki. Is Anki running with AnkiConnect installed?"
            if isinstance(cause, requests.exceptions.Timeout):
                return False, "Connection to AnkiConnect timed out"
            return False, f"AnkiConnect error: {e}"
        except Exception as e:
            logger.exception("Unexpected error checking AnkiConnect")
            return False, f"Unexpected error: {e}"
        return True, f"AnkiConnect v{version if version is not None else 'unknown'} is running"

    @staticmethod
    def _check_tool(
        name: str,
        resolved_path: str,
        *,
        version_flag: str = "-version",
        prefix_args: tuple[str, ...] = (),
        missing_message: str | None = None,
    ) -> tuple[bool, str]:
        """Run ``<resolved_path> <version_flag>`` and classify the result.

        Shared body for every external-binary check. ``name`` is the bare tool name
        used in messages and bundled/system/custom classification; ``resolved_path``
        is the already-resolved binary to invoke (so a frozen bundle validates the
        bundled binary, not whatever is on PATH).

        Args:
            version_flag: ffmpeg/ffprobe use the single-dash ``-version``; alass and
                yt-dlp use ``--version``.
            prefix_args: Arguments that must precede the version flag.
            missing_message: Overrides the generic not-found text for optional tools
                that want to name the feature the user loses.

        Returns:
            Tuple of (success, message)
        """
        try:
            result = subprocess.run(
                [resolved_path, *prefix_args, version_flag],
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                timeout=10,
                **no_window_kwargs(),  # hide the Windows cmd.exe flash (Issue #79)
            )

            if result.returncode != 0:
                return False, f"{name} returned non-zero exit code"

            # Extract version from first line
            version_line = result.stdout.split("\n")[0] if result.stdout else "unknown"
            return True, f"{version_line} {_classify_resolved(name, resolved_path)}"

        except FileNotFoundError:
            return False, missing_message or f"{name} not found. Install it and ensure it's in PATH"
        except subprocess.TimeoutExpired:
            return False, f"{name} check timed out"
        except Exception as e:
            logger.exception("Unexpected error checking %s", name)
            return False, f"Unexpected error: {e}"

    def _check_ffmpeg(self) -> tuple[bool, str]:
        """Check if ffmpeg is installed and accessible.

        Routes through ``resolve_ffmpeg`` so a frozen bundle validates the
        bundled binary (not whatever happens to be on PATH) and the success
        message reports whether the resolved binary is bundled / system / custom.

        Returns:
            Tuple of (success, message)
        """
        return self._check_tool("ffmpeg", resolve_ffmpeg(self.config))

    def _check_ffprobe(self) -> tuple[bool, str]:
        """Check if ffprobe is installed and accessible.

        Mirrors ``_check_ffmpeg`` but resolves and probes ffprobe, which the
        audio-track detection depends on. Routes through ``resolve_ffprobe`` so
        a frozen bundle validates the bundled binary.

        Returns:
            Tuple of (success, message)
        """
        return self._check_tool("ffprobe", resolve_ffprobe(self.config))

    def _check_alass(self) -> tuple[bool, str]:
        """Check if alass is installed and accessible (optional/non-fatal).

        alass is used for subtitle retiming, an opt-in feature.  A missing
        binary must not block startup; callers treat ``ok=False`` as a
        non-fatal warning, not an error.

        Returns:
            Tuple of (success, message).  ``ok=False`` means alass is absent
            or misbehaving; callers should surface this as a WARNING.
        """
        return self._check_tool(
            "alass",
            resolve_alass(self.config),
            version_flag="--version",
            missing_message=(
                "alass not found — retiming will use ffsubsync only; install alass or set its path in Settings "
                "for a fallback alignment engine"
            ),
        )

    def _check_ytdlp(self) -> tuple[bool, str]:
        """Check if yt-dlp is installed and accessible (optional/non-fatal).

        yt-dlp gates one tab (Video → YouTube), so a missing binary is a WARNING,
        not a startup blocker.

        Unlike every other check here, ``resolve_ytdlp`` is called INSIDE the try:
        it can raise ``FileNotFoundError`` when PATH resolves an unverified
        app-managed binary, and :meth:`validate_setup` has no blanket handler and
        documents itself as never raising. Resolving outside would take the whole
        startup validation down over an optional tool.

        The generation lock is taken with a bounded wait
        (``_YTDLP_LOCK_WAIT_SECONDS``): a run using the app-managed binary holds
        it for the whole transfer (up to the supervisor's 3h timeout), and
        waiting on that parked the validation worker — and every surface built
        on it — behind a download. Past the bound, report the busy state instead
        of waiting.

        Returns:
            Tuple of (success, message).
        """
        with managed_ytdlp_lock(timeout=_YTDLP_LOCK_WAIT_SECONDS) as acquired:
            if not acquired:
                return (
                    False,
                    "yt-dlp is busy — a yt-dlp task is running, so its version could not be checked. "
                    "Re-run this check once that task finishes.",
                )
            try:
                resolved = resolve_ytdlp(self.config)
            except FileNotFoundError:
                return (
                    False,
                    "yt-dlp is present but unverified, so it will not be used. "
                    "Re-run Settings → YouTube → Update yt-dlp now.",
                )
            return self._check_tool(
                "yt-dlp",
                resolved,
                version_flag="--version",
                prefix_args=("--ignore-config",),
                missing_message=(
                    "yt-dlp not found — YouTube mining will be unavailable; "
                    "use Settings → YouTube → Update yt-dlp now to install it"
                ),
            )

    def _ytdlp_staleness_warning(self, version_message: str) -> str | None:
        """Nudge opted-out users whose yt-dlp has aged out, else None.

        yt-dlp versions are ``YYYY.MM.DD``, so the first token of ``--version`` output
        dates the binary directly.

        Fires only while ``auto_update_ytdlp`` is False. That gate is load-bearing: a
        bundled binary is pinned at build time and is already weeks old on release
        day, so an ungated nudge would nag every fresh install about a setting that is
        already on. Gated, it reaches exactly the audience that needs it — existing
        users carrying the old opt-out default.
        """
        if self.config.auto_update_ytdlp:
            return None

        token = version_message.split()[0] if version_message else ""
        try:
            released = datetime.strptime(token, "%Y.%m.%d").date()
        except ValueError:
            # Nightly/dev builds and unexpected shapes: silence beats a false alarm.
            return None

        age_days = (date.today() - released).days
        if age_days < _YTDLP_STALE_AFTER_DAYS:
            return None
        return (
            f"yt-dlp is {age_days} days old ({token}). YouTube changes often break older "
            "versions — enable 'Keep yt-dlp up to date automatically' in "
            "Settings → YouTube, or click Update yt-dlp now."
        )

    def _check_offline_dictionary(self) -> tuple[bool, str]:
        """Check that an enabled offline dictionary can answer a lookup.

        Reads the registry snapshot only — no provider is constructed, loaded or
        closed, so nothing here takes a SQLite handle on the very indexes the
        repair route (Reimport All) may be about to replace. On Windows an open
        handle is a file lock, so a readiness probe that opened the chain would
        be able to block its own fix.

        The four failure shapes are reported apart because they have four
        different repairs: nothing configured, configured but absent, present
        but schema-stale (needs reimport), and present but empty.

        Returns:
            Tuple of (success, message). On success the message names the
            dictionaries and their entry counts.
        """
        from anki_miner.services.dictionary.registry import DictionaryRegistry

        enabled = [e for e in self.config.dictionary_chain if e.kind == "indexed" and e.enabled]
        if not enabled:
            return False, (
                "No offline dictionary is enabled, so mined cards will have no definitions. "
                "Import one in Settings → Dictionaries, or use Tools → Download Recommended Resources."
            )

        registry = DictionaryRegistry(self.config.dicts_root)
        registry.load()
        usable = registry.usable_enabled(self.config)
        if usable:
            return True, ", ".join(f"{meta.source_name} ({meta.entry_count:,} entries)" for meta in usable)

        stale = [meta.source_name for meta in registry.stale_enabled(self.config)]
        if stale:
            return False, (
                f"Dictionary index(es) need reimporting after an upgrade: {', '.join(stale)}. "
                "Use Settings → Dictionaries → Reimport All."
            )
        empty = [
            meta.source_name
            for meta in (registry.get(e.dict_id) for e in enabled if e.dict_id is not None)
            if meta is not None and meta.schema_ok and meta.entry_count == 0
        ]
        if empty:
            return False, (
                f"Dictionary index(es) contain no entries: {', '.join(empty)}. "
                "Reimport them in Settings → Dictionaries."
            )
        missing = [e.dict_id for e in enabled if e.dict_id]
        return False, (
            f"Dictionary index(es) not found on disk: {', '.join(missing)}. "
            "Import them again in Settings → Dictionaries."
        )

    def _check_frequency_sources(self) -> tuple[bool | None, str]:
        """Check that the configured frequency chain can answer a lookup.

        Reads the registry snapshot only, for the same reason
        :meth:`_check_offline_dictionary` does: a readiness probe that opened
        the chain would take a Windows file lock on the very index Reimport All
        is about to replace, and so could block its own fix.

        Frequency is optional and activation is derived from an enabled source
        existing, so "configured nothing" is not a failure — it returns ``None``
        and the row reports itself as unconfigured rather than nagging. Neither
        is "enabled but gone from disk": that state was deliberately dropped
        from validation when the ``use_frequency_data`` flag went away, and
        nothing here brings it back.

        What *is* reported is a source the user asked for that an app upgrade
        broke. A stale index is silently dropped from the chain, which costs the
        card its rank AND stops ``max_frequency_rank`` filtering — so the run
        floods the deck with rare words and says nothing.

        Returns:
            ``(None, "")`` when nothing is configured, ``(True, names)`` when
            usable, ``(False, message)`` when stale or empty.
        """
        from anki_miner.services.frequency.registry import FrequencySourceRegistry

        enabled = [e for e in self.config.frequency_chain if e.enabled and e.source_id]
        if not enabled:
            return None, ""

        registry = FrequencySourceRegistry(self.config.freqs_root)
        registry.load()
        usable = registry.usable_enabled(self.config)

        stale = [meta.source_name for meta in registry.stale_enabled(self.config)]
        if stale:
            return False, (
                f"Frequency source(s) need reimporting after an upgrade: {', '.join(stale)}. "
                "Use Settings → Frequency → Reimport All."
            )
        empty = [
            meta.source_name
            for meta in (registry.get(e.source_id) for e in enabled)
            if meta is not None and meta.schema_ok and meta.entry_count == 0
        ]
        if empty:
            return False, (
                f"Frequency source(s) contain no entries: {', '.join(empty)}. " "Reimport them in Settings → Frequency."
            )
        if usable:
            return True, ", ".join(f"{meta.source_name} ({meta.entry_count:,} entries)" for meta in usable)
        # Enabled but absent from disk. Not reported — see the docstring.
        return None, ""

    def _check_pitch_sources(self) -> tuple[bool | None, str]:
        """Check that the configured pitch chain can answer a lookup.

        Same contract and same registry-snapshot-only rule as
        :meth:`_check_frequency_sources`. A stale pitch index costs the card its
        pitch field silently; an unconfigured chain is reported as unconfigured,
        never as a problem.

        Returns:
            ``(None, "")`` when nothing is configured, ``(True, names)`` when
            usable, ``(False, message)`` when stale or empty.
        """
        from anki_miner.services.pitch_accent.registry import PitchSourceRegistry

        enabled = [e for e in self.config.pitch_chain if e.enabled and e.source_id]
        if not enabled:
            return None, ""

        registry = PitchSourceRegistry(self.config.pitch_root)
        registry.load()
        usable = registry.usable_enabled(self.config)

        stale = [meta.source_name for meta in registry.stale_enabled(self.config)]
        if stale:
            return False, (
                f"Pitch accent source(s) need reimporting after an upgrade: {', '.join(stale)}. "
                "Use Settings → Pitch Accent → Reimport All."
            )
        empty = [
            meta.source_name
            for meta in (registry.get(e.source_id) for e in enabled)
            if meta is not None and meta.schema_ok and meta.entry_count == 0
        ]
        if empty:
            return False, (
                f"Pitch accent source(s) contain no entries: {', '.join(empty)}. "
                "Reimport them in Settings → Pitch Accent."
            )
        if usable:
            return True, ", ".join(f"{meta.source_name} ({meta.entry_count:,} entries)" for meta in usable)
        # Enabled but absent from disk. Not reported — see the docstring.
        return None, ""

    def _check_audio_packs(self) -> tuple[bool | None, str]:
        """Check that the configured audio pack chain can answer a lookup.

        Same contract and same registry-snapshot-only rule as
        :meth:`_check_frequency_sources`. Audio packs are optional — a chain of
        only online sources (JPod101, Google TTS) has no index to go stale and
        is reported as unconfigured, never as a problem.

        What *is* reported is a pack the user asked for that an app upgrade
        broke: a stale index is dropped from the fetcher chain, so the card
        falls through to the online sources or gets no audio at all, silently.
        A pack whose audio folder has merely moved is not reported — that
        degrades the same way but is not upgrade damage, matching how a source
        missing from disk is treated for the other three families.

        A pack is only ever consulted when ``expression_audio`` is also mapped
        (the fetcher's two-part gate — see ``audio_stage.py``). An unmapped
        field must report the same as no enabled pack at all, never an
        OK-green for a feature that will not run.

        Returns:
            ``(None, "")`` when nothing is configured, ``(True, names)`` when
            usable, ``(False, message)`` when stale.
        """
        from anki_miner.services.audio_packs.registry import AudioPackRegistry

        if not self.config.anki_fields.get("expression_audio"):
            return None, ""

        enabled_ids = [
            e.pack_id for e in self.config.expression_audio_chain if e.enabled and e.kind == "pack" and e.pack_id
        ]
        if not enabled_ids:
            return None, ""

        registry = AudioPackRegistry(self.config.audio_packs_root)
        registry.load()
        packs = registry.packs

        stale = [meta.source for meta in registry.stale_enabled(self.config)]
        if stale:
            return False, (
                f"Audio pack(s) need reimporting after an upgrade: {', '.join(stale)}. "
                "Use Settings → Audio → Reimport All."
            )
        usable = [
            meta
            for meta in (packs.get(pack_id) for pack_id in enabled_ids if pack_id)
            if meta is not None and meta.schema_ok and meta.source_available
        ]
        if usable:
            return True, ", ".join(f"{meta.source} ({meta.entry_count:,} entries)" for meta in usable)
        # Enabled but absent from disk, or its audio is unreachable. Not
        # reported — see the docstring.
        return None, ""

    def _check_deck_exists(self) -> tuple[bool, str]:
        """Check if the target deck exists in Anki.

        Returns:
            Tuple of (success, message)
        """
        try:
            decks = (
                post_action(
                    self.config.ankiconnect_url,
                    "deckNames",
                    timeout=10,
                )
                or []
            )
        except AnkiConnectionError as e:
            # Surface AnkiConnect-side error payloads with the historical
            # "Error fetching decks: ..." prefix; everything else falls
            # through to "Error checking deck: ...".
            msg = str(e)
            prefix = "AnkiConnect error in 'deckNames': "
            if msg.startswith(prefix):
                return False, f"Error fetching decks: {msg[len(prefix) :]}"
            return False, f"Error checking deck: {e}"
        except Exception as e:
            logger.exception("Unexpected error checking deck existence")
            return False, f"Error checking deck: {e}"

        deck_name = self.config.anki_deck_name
        if deck_name in decks:
            return True, f"Deck '{deck_name}' found"
        available = ", ".join(decks[:5])
        more = "..." if len(decks) > 5 else ""
        return False, (
            f"Deck '{deck_name}' not found in Anki. "
            f"Pick an existing deck in Settings → Anki. "
            f"Available: {available}{more}"
        )

    def _check_note_type_exists(self) -> tuple[bool, str]:
        """Check if the note type (model) exists in Anki.

        Returns:
            Tuple of (success, message)
        """
        try:
            models = (
                post_action(
                    self.config.ankiconnect_url,
                    "modelNames",
                    timeout=10,
                )
                or []
            )
        except AnkiConnectionError as e:
            msg = str(e)
            prefix = "AnkiConnect error in 'modelNames': "
            if msg.startswith(prefix):
                return False, f"Error fetching models: {msg[len(prefix) :]}"
            return False, f"Error checking note type: {e}"
        except Exception as e:
            logger.exception("Unexpected error checking note type existence")
            return False, f"Error checking note type: {e}"

        note_type = self.config.anki_note_type
        if note_type in models:
            return True, f"Note type '{note_type}' found"
        available = ", ".join(models[:5])
        more = "..." if len(models) > 5 else ""
        return False, f"Note type '{note_type}' not found. Available: {available}{more}"

    def _check_field_names_exist(self) -> tuple[bool, str]:
        """Check configured field presence and the first-field invariant.

        Returns:
            Tuple of (success, message)
        """
        try:
            actual_fields_list = (
                post_action(
                    self.config.ankiconnect_url,
                    "modelFieldNames",
                    params={"modelName": self.config.anki_note_type},
                    timeout=10,
                )
                or []
            )
        except AnkiConnectionError as e:
            msg = str(e)
            prefix = "AnkiConnect error in 'modelFieldNames': "
            if msg.startswith(prefix):
                return False, f"Error fetching fields: {msg[len(prefix) :]}"
            return False, f"Error checking fields: {e}"
        except Exception as e:
            logger.exception("Unexpected error checking field names")
            return False, f"Error checking fields: {e}"

        targets = [target for target in self.config.anki_fields.values() if target]
        if self.config.card_type:
            marker_target = self.config.card_type_marker_fields.get(self.config.card_type, "")
            if marker_target:
                targets.append(marker_target)
        collision_error = field_target_collision_message(self.config.anki_note_type, targets)
        if collision_error:
            return False, collision_error

        configured_fields = configured_target_field_names(self.config)
        word_target = self.config.anki_fields["word"]
        mapping_error = field_mapping_error(
            self.config.anki_note_type,
            actual_fields_list,
            configured_fields,
            word_target,
        )
        if mapping_error:
            return False, mapping_error
        return True, "All configured fields exist"
