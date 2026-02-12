"""Service for validating system setup and dependencies."""

import subprocess

import requests

from anki_miner.config import AnkiMinerConfig
from anki_miner.models import ValidationIssue, ValidationResult
from anki_miner.utils import ensure_directory


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

        return ValidationResult(
            ankiconnect_ok=ankiconnect_ok,
            ffmpeg_ok=ffmpeg_ok,
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
            response = requests.post(
                self.config.ankiconnect_url,
                json={"action": "version", "version": 6},
                timeout=5,
            )

            if response.status_code != 200:
                return False, "AnkiConnect returned non-200 status"

            result = response.json()
            if result.get("error"):
                return False, f"AnkiConnect error: {result['error']}"

            version = result.get("result", "unknown")
            return True, f"AnkiConnect v{version} is running"

        except requests.exceptions.ConnectionError:
            return False, "Cannot connect to Anki. Is Anki running with AnkiConnect installed?"
        except requests.exceptions.Timeout:
            return False, "Connection to AnkiConnect timed out"
        except Exception as e:
            return False, f"Unexpected error: {e}"

    def _check_ffmpeg(self) -> tuple[bool, str]:
        """Check if ffmpeg is installed and accessible.

        Returns:
            Tuple of (success, message)
        """
        try:
            result = subprocess.run(
                ["ffmpeg", "-version"],
                capture_output=True,
                text=True,
                timeout=10,
            )

            if result.returncode != 0:
                return False, "ffmpeg returned non-zero exit code"

            # Extract version from first line
            version_line = result.stdout.split("\n")[0] if result.stdout else "unknown"
            return True, version_line

        except FileNotFoundError:
            return False, "ffmpeg not found. Install it and ensure it's in PATH"
        except subprocess.TimeoutExpired:
            return False, "ffmpeg check timed out"
        except Exception as e:
            return False, f"Unexpected error: {e}"

    def _check_deck_exists(self) -> tuple[bool, str]:
        """Check if the target deck exists in Anki.

        Returns:
            Tuple of (success, message)
        """
        try:
            response = requests.post(
                self.config.ankiconnect_url,
                json={"action": "deckNames", "version": 6},
                timeout=10,
            )

            result = response.json()
            if result.get("error"):
                return False, f"Error fetching decks: {result['error']}"

            decks = result.get("result", [])
            deck_name = self.config.anki_deck_name

            if deck_name in decks:
                return True, f"Deck '{deck_name}' found"
            else:
                available = ", ".join(decks[:5])
                more = "..." if len(decks) > 5 else ""
                return False, f"Deck '{deck_name}' not found. Available: {available}{more}"

        except Exception as e:
            return False, f"Error checking deck: {e}"

    def _check_note_type_exists(self) -> tuple[bool, str]:
        """Check if the note type (model) exists in Anki.

        Returns:
            Tuple of (success, message)
        """
        try:
            response = requests.post(
                self.config.ankiconnect_url,
                json={"action": "modelNames", "version": 6},
                timeout=10,
            )

            result = response.json()
            if result.get("error"):
                return False, f"Error fetching models: {result['error']}"

            models = result.get("result", [])
            note_type = self.config.anki_note_type

            if note_type in models:
                return True, f"Note type '{note_type}' found"
            else:
                available = ", ".join(models[:5])
                more = "..." if len(models) > 5 else ""
                return False, f"Note type '{note_type}' not found. Available: {available}{more}"

        except Exception as e:
            return False, f"Error checking note type: {e}"
