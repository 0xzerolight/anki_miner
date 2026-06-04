"""Service for validating system setup and dependencies."""

import subprocess
import sys

import requests

from anki_miner.config import AnkiMinerConfig
from anki_miner.exceptions import AnkiConnectionError
from anki_miner.models import ValidationIssue, ValidationResult
from anki_miner.services._ankiconnect import post_action
from anki_miner.utils import ensure_directory
from anki_miner.utils.ffmpeg_resolver import resolve_ffmpeg, resolve_ffprobe


def _classify_resolved(base: str, resolved: str) -> str:
    """Classify a resolved ffmpeg/ffprobe path for the success message.

    Returns a short bracketed suffix describing where the binary came from:

    - ``[system PATH]`` — the resolver returned the bare literal (PATH lookup).
    - ``[bundled]`` — the resolved path lives under the frozen ``sys._MEIPASS``.
    - ``[custom path]`` — an explicit config override / any other absolute path.
    """
    if resolved == base:
        return "[system PATH]"
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass is not None and resolved.startswith(str(meipass)):
        return "[bundled]"
    return "[custom path]"


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
            issues.append(
                ValidationIssue(
                    component="Temp Folder",
                    severity="WARNING",
                    message=f"Could not create temp folder: {e}",
                )
            )

        # Optional resource files: warn (not fail) when the user enabled a
        # feature but the underlying file is missing, so the GUI can surface
        # an "enabled but unavailable" state up front instead of silently
        # falling back at lookup time.

        # Dictionary chain validation — warn if every enabled indexed entry is
        # missing on disk. The chain falls back to other providers (Jisho), so
        # this is only a warning, not an error.
        dicts_root = self.config.dicts_root
        indexed_entries = [e for e in self.config.dictionary_chain if e.kind == "indexed" and e.enabled]
        if indexed_entries:
            missing = [
                e.dict_id
                for e in indexed_entries
                if e.dict_id is None or not (dicts_root / e.dict_id / "index.sqlite").exists()
            ]
            if missing:
                issues.append(
                    ValidationIssue(
                        component="Offline Dictionary",
                        severity="WARNING",
                        message=(
                            f"Dictionary index(es) not found on disk: {', '.join(m for m in missing if m)}. "
                            "Lookups will fall back to other providers in the chain."
                        ),
                    )
                )

        if self.config.use_pitch_accent and not self.config.pitch_accent_path.is_file():
            issues.append(
                ValidationIssue(
                    component="Pitch Accent",
                    severity="WARNING",
                    message=(
                        f"Pitch accent data not found at {self.config.pitch_accent_path}. "
                        "Pitch accent annotations will be skipped."
                    ),
                )
            )

        if self.config.use_frequency_data and not self.config.frequency_list_path.is_file():
            issues.append(
                ValidationIssue(
                    component="Frequency Data",
                    severity="WARNING",
                    message=(
                        f"Frequency list not found at {self.config.frequency_list_path}. "
                        "Frequency-based filtering and annotations will be skipped."
                    ),
                )
            )

        return ValidationResult(
            ankiconnect_ok=ankiconnect_ok,
            ffmpeg_ok=ffmpeg_ok,
            ffprobe_ok=ffprobe_ok,
            deck_exists=deck_ok,
            note_type_exists=note_type_ok,
            issues=issues,
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
            return False, f"Unexpected error: {e}"
        return True, f"AnkiConnect v{version if version is not None else 'unknown'} is running"

    def _check_ffmpeg(self) -> tuple[bool, str]:
        """Check if ffmpeg is installed and accessible.

        Routes through ``resolve_ffmpeg`` so a frozen bundle validates the
        bundled binary (not whatever happens to be on PATH) and the success
        message reports whether the resolved binary is bundled / system / custom.

        Returns:
            Tuple of (success, message)
        """
        ffmpeg = resolve_ffmpeg(self.config)
        try:
            result = subprocess.run(
                [ffmpeg, "-version"],
                capture_output=True,
                text=True,
                timeout=10,
            )

            if result.returncode != 0:
                return False, "ffmpeg returned non-zero exit code"

            # Extract version from first line
            version_line = result.stdout.split("\n")[0] if result.stdout else "unknown"
            return True, f"{version_line} {_classify_resolved('ffmpeg', ffmpeg)}"

        except FileNotFoundError:
            return False, "ffmpeg not found. Install it and ensure it's in PATH"
        except subprocess.TimeoutExpired:
            return False, "ffmpeg check timed out"
        except Exception as e:
            return False, f"Unexpected error: {e}"

    def _check_ffprobe(self) -> tuple[bool, str]:
        """Check if ffprobe is installed and accessible.

        Mirrors ``_check_ffmpeg`` but resolves and probes ffprobe, which the
        audio-track detection depends on. Routes through ``resolve_ffprobe`` so
        a frozen bundle validates the bundled binary.

        Returns:
            Tuple of (success, message)
        """
        ffprobe = resolve_ffprobe(self.config)
        try:
            result = subprocess.run(
                [ffprobe, "-version"],
                capture_output=True,
                text=True,
                timeout=10,
            )

            if result.returncode != 0:
                return False, "ffprobe returned non-zero exit code"

            version_line = result.stdout.split("\n")[0] if result.stdout else "unknown"
            return True, f"{version_line} {_classify_resolved('ffprobe', ffprobe)}"

        except FileNotFoundError:
            return False, "ffprobe not found. Install it and ensure it's in PATH"
        except subprocess.TimeoutExpired:
            return False, "ffprobe check timed out"
        except Exception as e:
            return False, f"Unexpected error: {e}"

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
                return False, f"Error fetching decks: {msg[len(prefix):]}"
            return False, f"Error checking deck: {e}"
        except Exception as e:
            return False, f"Error checking deck: {e}"

        deck_name = self.config.anki_deck_name
        if deck_name in decks:
            return True, f"Deck '{deck_name}' found"
        available = ", ".join(decks[:5])
        more = "..." if len(decks) > 5 else ""
        return False, f"Deck '{deck_name}' not found. Available: {available}{more}"

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
                return False, f"Error fetching models: {msg[len(prefix):]}"
            return False, f"Error checking note type: {e}"
        except Exception as e:
            return False, f"Error checking note type: {e}"

        note_type = self.config.anki_note_type
        if note_type in models:
            return True, f"Note type '{note_type}' found"
        available = ", ".join(models[:5])
        more = "..." if len(models) > 5 else ""
        return False, f"Note type '{note_type}' not found. Available: {available}{more}"

    def _check_field_names_exist(self) -> tuple[bool, str]:
        """Check that configured field names exist on the note type.

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
                return False, f"Error fetching fields: {msg[len(prefix):]}"
            return False, f"Error checking fields: {e}"
        except Exception as e:
            return False, f"Error checking fields: {e}"

        actual_fields = set(actual_fields_list)
        configured_fields = {v for v in self.config.anki_fields.values() if v}
        missing = configured_fields - actual_fields
        if missing:
            return False, (
                f"Field(s) {', '.join(sorted(missing))} not found on note type "
                f"'{self.config.anki_note_type}'. "
                f"Available: {', '.join(sorted(actual_fields))}"
            )
        return True, "All configured fields exist"
