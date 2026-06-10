"""Tests for validation_service module."""

import subprocess
import sys
from unittest.mock import MagicMock, patch

import pytest

from anki_miner.services.validation_service import ValidationService


class TestValidationService:
    """Tests for ValidationService class."""

    @pytest.fixture
    def service(self, test_config):
        """Create a ValidationService instance."""
        return ValidationService(test_config)

    class TestCheckAnkiconnect:
        """Tests for _check_ankiconnect method."""

        def test_success(self, test_config):
            service = ValidationService(test_config)

            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.json.return_value = {"result": 6, "error": None}

            with patch("anki_miner.services._ankiconnect.requests.post", return_value=mock_response):
                success, message = service._check_ankiconnect()

            assert success is True
            assert "v6" in message

        def test_connection_error(self, test_config):
            service = ValidationService(test_config)

            import requests

            with patch(
                "anki_miner.services._ankiconnect.requests.post",
                side_effect=requests.exceptions.ConnectionError(),
            ):
                success, message = service._check_ankiconnect()

            assert success is False
            assert "Cannot connect" in message

        def test_timeout(self, test_config):
            service = ValidationService(test_config)

            import requests

            with patch(
                "anki_miner.services._ankiconnect.requests.post",
                side_effect=requests.exceptions.Timeout(),
            ):
                success, message = service._check_ankiconnect()

            assert success is False
            assert "timed out" in message

        def test_ankiconnect_error(self, test_config):
            service = ValidationService(test_config)

            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.json.return_value = {"result": None, "error": "Some error"}

            with patch("anki_miner.services._ankiconnect.requests.post", return_value=mock_response):
                success, message = service._check_ankiconnect()

            assert success is False
            assert "error" in message.lower()

    class TestCheckFfmpeg:
        """Tests for _check_ffmpeg method."""

        def test_success(self, test_config):
            service = ValidationService(test_config)

            mock_result = MagicMock()
            mock_result.returncode = 0
            mock_result.stdout = "ffmpeg version 5.0"

            with patch("anki_miner.services.validation_service.subprocess.run", return_value=mock_result):
                success, message = service._check_ffmpeg()

            assert success is True
            assert "ffmpeg version" in message

        def test_not_found(self, test_config):
            service = ValidationService(test_config)

            with patch(
                "anki_miner.services.validation_service.subprocess.run",
                side_effect=FileNotFoundError(),
            ):
                success, message = service._check_ffmpeg()

            assert success is False
            assert "not found" in message

        def test_timeout(self, test_config):
            service = ValidationService(test_config)

            with patch(
                "anki_miner.services.validation_service.subprocess.run",
                side_effect=subprocess.TimeoutExpired("ffmpeg", 10),
            ):
                success, message = service._check_ffmpeg()

            assert success is False
            assert "timed out" in message

        def test_non_zero_exit(self, test_config):
            service = ValidationService(test_config)

            mock_result = MagicMock()
            mock_result.returncode = 1

            with patch("anki_miner.services.validation_service.subprocess.run", return_value=mock_result):
                success, message = service._check_ffmpeg()

            assert success is False
            assert "non-zero" in message

    class TestCheckDeckExists:
        """Tests for _check_deck_exists method."""

        def test_deck_found(self, test_config):
            service = ValidationService(test_config)

            mock_response = MagicMock()
            mock_response.json.return_value = {
                "result": ["Default", test_config.anki_deck_name, "Other"],
                "error": None,
            }

            with patch("anki_miner.services._ankiconnect.requests.post", return_value=mock_response):
                success, message = service._check_deck_exists()

            assert success is True
            assert "found" in message.lower()

        def test_deck_not_found(self, test_config):
            service = ValidationService(test_config)

            mock_response = MagicMock()
            mock_response.json.return_value = {
                "result": ["Default", "Other"],
                "error": None,
            }

            with patch("anki_miner.services._ankiconnect.requests.post", return_value=mock_response):
                success, message = service._check_deck_exists()

            assert success is False
            assert "not found" in message.lower()
            assert "created automatically" in message

        def test_deck_not_found_lists_available(self, test_config):
            """Missing deck message should still list available decks."""
            service = ValidationService(test_config)

            mock_response = MagicMock()
            mock_response.json.return_value = {
                "result": ["Default", "Other"],
                "error": None,
            }

            with patch("anki_miner.services._ankiconnect.requests.post", return_value=mock_response):
                success, message = service._check_deck_exists()

            assert success is False
            assert "Available" in message
            assert "Default" in message

    class TestCheckNoteTypeExists:
        """Tests for _check_note_type_exists method."""

        def test_note_type_found(self, test_config):
            service = ValidationService(test_config)

            mock_response = MagicMock()
            mock_response.json.return_value = {
                "result": ["Basic", test_config.anki_note_type, "Cloze"],
                "error": None,
            }

            with patch("anki_miner.services._ankiconnect.requests.post", return_value=mock_response):
                success, message = service._check_note_type_exists()

            assert success is True
            assert "found" in message.lower()

        def test_note_type_not_found(self, test_config):
            service = ValidationService(test_config)

            mock_response = MagicMock()
            mock_response.json.return_value = {
                "result": ["Basic", "Cloze"],
                "error": None,
            }

            with patch("anki_miner.services._ankiconnect.requests.post", return_value=mock_response):
                success, message = service._check_note_type_exists()

            assert success is False
            assert "not found" in message.lower()

        def test_generic_exception(self, test_config):
            """Generic exception should be caught and reported."""
            service = ValidationService(test_config)

            with patch(
                "anki_miner.services._ankiconnect.requests.post",
                side_effect=RuntimeError("something broke"),
            ):
                success, message = service._check_ankiconnect()

            assert success is False
            assert "Unexpected error" in message

    class TestCheckFfmpegExceptions:
        """Additional exception tests for _check_ffmpeg."""

        def test_generic_exception(self, test_config):
            """Generic exception should be caught and reported."""
            service = ValidationService(test_config)

            with patch(
                "anki_miner.services.validation_service.subprocess.run",
                side_effect=RuntimeError("something broke"),
            ):
                success, message = service._check_ffmpeg()

            assert success is False
            assert "Unexpected error" in message

    class TestCheckFfmpegClassification:
        """Bundled/system/custom classification of the ffmpeg success message."""

        def setup_method(self):
            from anki_miner.utils import ffmpeg_resolver

            ffmpeg_resolver._clear_cache()

        def teardown_method(self):
            from anki_miner.utils import ffmpeg_resolver

            ffmpeg_resolver._clear_cache()

        def test_system_path_suffix(self, test_config):
            """No override + not frozen → resolves to bare literal → [system PATH]."""
            service = ValidationService(test_config)

            mock_result = MagicMock()
            mock_result.returncode = 0
            mock_result.stdout = "ffmpeg version 5.0"

            with patch("anki_miner.services.validation_service.subprocess.run", return_value=mock_result):
                success, message = service._check_ffmpeg()

            assert success is True
            assert "[system PATH]" in message

        def test_custom_path_suffix(self, test_config, tmp_path):
            """An existing ffmpeg_location override → absolute path → [custom path]."""
            from dataclasses import replace

            fake_ffmpeg = tmp_path / "ffmpeg"
            fake_ffmpeg.write_text("#!/bin/sh\n")
            config = replace(test_config, ffmpeg_location=fake_ffmpeg)
            service = ValidationService(config)

            mock_result = MagicMock()
            mock_result.returncode = 0
            mock_result.stdout = "ffmpeg version 5.0"

            with patch("anki_miner.services.validation_service.subprocess.run", return_value=mock_result):
                success, message = service._check_ffmpeg()

            assert success is True
            assert "[custom path]" in message
            assert "[system PATH]" not in message

        def test_bundled_suffix(self, test_config, tmp_path, monkeypatch):
            """Frozen bundle with a bundled binary under _MEIPASS → [bundled]."""
            bin_dir = tmp_path / "bin"
            bin_dir.mkdir()
            bundled = bin_dir / "ffmpeg"
            bundled.write_text("#!/bin/sh\n")
            bundled.chmod(0o755)  # resolver requires the exec bit on POSIX

            monkeypatch.setattr(sys, "frozen", True, raising=False)
            monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path), raising=False)

            service = ValidationService(test_config)

            mock_result = MagicMock()
            mock_result.returncode = 0
            mock_result.stdout = "ffmpeg version 5.0"

            with patch("anki_miner.services.validation_service.subprocess.run", return_value=mock_result):
                success, message = service._check_ffmpeg()

            assert success is True
            assert "[bundled]" in message

    class TestCheckFfprobe:
        """Tests for _check_ffprobe method (mirrors _check_ffmpeg)."""

        def test_success(self, test_config):
            service = ValidationService(test_config)

            mock_result = MagicMock()
            mock_result.returncode = 0
            mock_result.stdout = "ffprobe version 5.0"

            with patch("anki_miner.services.validation_service.subprocess.run", return_value=mock_result):
                success, message = service._check_ffprobe()

            assert success is True
            assert "ffprobe version" in message

        def test_not_found(self, test_config):
            service = ValidationService(test_config)

            with patch(
                "anki_miner.services.validation_service.subprocess.run",
                side_effect=FileNotFoundError(),
            ):
                success, message = service._check_ffprobe()

            assert success is False
            assert "not found" in message

        def test_timeout(self, test_config):
            service = ValidationService(test_config)

            with patch(
                "anki_miner.services.validation_service.subprocess.run",
                side_effect=subprocess.TimeoutExpired("ffprobe", 10),
            ):
                success, message = service._check_ffprobe()

            assert success is False
            assert "timed out" in message

        def test_non_zero_exit(self, test_config):
            service = ValidationService(test_config)

            mock_result = MagicMock()
            mock_result.returncode = 1

            with patch("anki_miner.services.validation_service.subprocess.run", return_value=mock_result):
                success, message = service._check_ffprobe()

            assert success is False
            assert "non-zero" in message

        def test_generic_exception(self, test_config):
            service = ValidationService(test_config)

            with patch(
                "anki_miner.services.validation_service.subprocess.run",
                side_effect=RuntimeError("something broke"),
            ):
                success, message = service._check_ffprobe()

            assert success is False
            assert "Unexpected error" in message

    class TestCheckDeckExistsExceptions:
        """Additional exception tests for _check_deck_exists."""

        def test_generic_exception(self, test_config):
            """Generic exception should be caught and reported."""
            service = ValidationService(test_config)

            with patch(
                "anki_miner.services._ankiconnect.requests.post",
                side_effect=RuntimeError("connection failed"),
            ):
                success, message = service._check_deck_exists()

            assert success is False
            assert "Error checking deck" in message

        def test_ankiconnect_error_response(self, test_config):
            """AnkiConnect error in deck check should be reported."""
            service = ValidationService(test_config)

            mock_response = MagicMock()
            mock_response.json.return_value = {
                "result": None,
                "error": "collection unavailable",
            }

            with patch(
                "anki_miner.services._ankiconnect.requests.post",
                return_value=mock_response,
            ):
                success, message = service._check_deck_exists()

            assert success is False
            assert "Error fetching decks" in message

    class TestCheckNoteTypeExistsExceptions:
        """Additional exception tests for _check_note_type_exists."""

        def test_generic_exception(self, test_config):
            """Generic exception should be caught and reported."""
            service = ValidationService(test_config)

            with patch(
                "anki_miner.services._ankiconnect.requests.post",
                side_effect=RuntimeError("connection failed"),
            ):
                success, message = service._check_note_type_exists()

            assert success is False
            assert "Error checking note type" in message

        def test_ankiconnect_error_response(self, test_config):
            """AnkiConnect error in note type check should be reported."""
            service = ValidationService(test_config)

            mock_response = MagicMock()
            mock_response.json.return_value = {
                "result": None,
                "error": "collection unavailable",
            }

            with patch(
                "anki_miner.services._ankiconnect.requests.post",
                return_value=mock_response,
            ):
                success, message = service._check_note_type_exists()

            assert success is False
            assert "Error fetching models" in message

    class TestTempFolderException:
        """Tests for temp folder creation failure."""

        def test_temp_folder_creation_exception(self, test_config):
            """Temp folder creation failure should produce a warning issue."""
            service = ValidationService(test_config)

            # Make all external checks pass
            anki_resp = MagicMock()
            anki_resp.status_code = 200
            anki_resp.json.return_value = {"result": 6, "error": None}

            deck_resp = MagicMock()
            deck_resp.json.return_value = {
                "result": [test_config.anki_deck_name],
                "error": None,
            }

            model_resp = MagicMock()
            model_resp.json.return_value = {
                "result": [test_config.anki_note_type],
                "error": None,
            }

            dispatch = {
                "version": anki_resp,
                "deckNames": deck_resp,
                "modelNames": model_resp,
            }

            def mock_post(url, **kwargs):
                action = kwargs.get("json", {}).get("action", "")
                return dispatch.get(action, MagicMock())

            ffmpeg_result = MagicMock()
            ffmpeg_result.returncode = 0
            ffmpeg_result.stdout = "ffmpeg version 6.0"

            with (
                patch(
                    "anki_miner.services._ankiconnect.requests.post",
                    side_effect=mock_post,
                ),
                patch(
                    "anki_miner.services.validation_service.subprocess.run",
                    return_value=ffmpeg_result,
                ),
                patch(
                    "anki_miner.services.validation_service.ensure_directory",
                    side_effect=OSError("permission denied"),
                ),
            ):
                result = service.validate_setup()

            assert any(i.component == "Temp Folder" for i in result.issues)
            assert any("permission denied" in i.message for i in result.issues)

    class TestValidateSetup:
        """Tests for validate_setup — mocking at real boundaries (requests.post, subprocess.run)."""

        def test_all_pass(self, test_config):
            """All checks pass when external services respond correctly."""
            from dataclasses import replace

            from anki_miner.config import ChainEntry

            # Disable optional feature flags so missing optional resource files
            # don't add warnings; the test focuses on the core Anki/ffmpeg path.
            test_config = replace(
                test_config,
                dictionary_chain=(ChainEntry(kind="jisho", dict_id=None, enabled=True),),
                use_pitch_accent=False,
                use_frequency_data=False,
            )
            service = ValidationService(test_config)

            # AnkiConnect version check
            anki_version_resp = MagicMock()
            anki_version_resp.status_code = 200
            anki_version_resp.json.return_value = {"result": 6, "error": None}

            # Deck names check
            deck_resp = MagicMock()
            deck_resp.json.return_value = {
                "result": ["Default", test_config.anki_deck_name],
                "error": None,
            }

            # Note type check
            model_resp = MagicMock()
            model_resp.json.return_value = {
                "result": ["Basic", test_config.anki_note_type],
                "error": None,
            }

            # Field names check
            field_names_resp = MagicMock()
            field_names_resp.json.return_value = {
                "result": list({v for v in test_config.anki_fields.values() if v}),
                "error": None,
            }

            dispatch = {
                "version": anki_version_resp,
                "deckNames": deck_resp,
                "modelNames": model_resp,
                "modelFieldNames": field_names_resp,
            }

            def mock_post(url, **kwargs):
                action = kwargs.get("json", {}).get("action", "")
                return dispatch.get(action, MagicMock())

            ffmpeg_result = MagicMock()
            ffmpeg_result.returncode = 0
            ffmpeg_result.stdout = "ffmpeg version 6.0"

            with (
                patch("anki_miner.services._ankiconnect.requests.post", side_effect=mock_post),
                patch(
                    "anki_miner.services.validation_service.subprocess.run",
                    return_value=ffmpeg_result,
                ),
            ):
                result = service.validate_setup()

            assert result.all_passed is True
            assert len(result.issues) == 0

        def test_ankiconnect_failure_skips_deck_and_note_checks(self, test_config):
            """When AnkiConnect fails, deck/note checks should be skipped."""
            service = ValidationService(test_config)

            import requests as req

            ffmpeg_result = MagicMock()
            ffmpeg_result.returncode = 0
            ffmpeg_result.stdout = "ffmpeg version 6.0"

            with (
                patch(
                    "anki_miner.services._ankiconnect.requests.post",
                    side_effect=req.exceptions.ConnectionError(),
                ),
                patch(
                    "anki_miner.services.validation_service.subprocess.run",
                    return_value=ffmpeg_result,
                ),
            ):
                result = service.validate_setup()

            assert result.ankiconnect_ok is False
            assert result.deck_exists is False
            assert result.note_type_exists is False
            assert result.ffmpeg_ok is True
            assert any(i.component == "AnkiConnect" for i in result.issues)

        def test_missing_deck_produces_warning_not_error(self, test_config):
            """A missing deck should surface as WARNING (auto-created at mining time), not ERROR."""
            service = ValidationService(test_config)

            anki_resp = MagicMock()
            anki_resp.status_code = 200
            anki_resp.json.return_value = {"result": 6, "error": None}

            # Return decks that do NOT include the configured deck name
            deck_resp = MagicMock()
            deck_resp.json.return_value = {"result": ["Default", "Other"], "error": None}

            model_resp = MagicMock()
            model_resp.json.return_value = {"result": [test_config.anki_note_type], "error": None}

            field_resp = MagicMock()
            field_resp.json.return_value = {
                "result": list({v for v in test_config.anki_fields.values() if v}),
                "error": None,
            }

            dispatch = {
                "version": anki_resp,
                "deckNames": deck_resp,
                "modelNames": model_resp,
                "modelFieldNames": field_resp,
            }

            def mock_post(url, **kwargs):
                action = kwargs.get("json", {}).get("action", "")
                return dispatch.get(action, MagicMock())

            ffmpeg_result = MagicMock()
            ffmpeg_result.returncode = 0
            ffmpeg_result.stdout = "ffmpeg version 6.0"

            with (
                patch("anki_miner.services._ankiconnect.requests.post", side_effect=mock_post),
                patch(
                    "anki_miner.services.validation_service.subprocess.run",
                    return_value=ffmpeg_result,
                ),
            ):
                result = service.validate_setup()

            assert result.deck_exists is False
            deck_issues = [i for i in result.issues if i.component == "Anki Deck"]
            assert len(deck_issues) == 1
            assert deck_issues[0].severity == "WARNING"
            assert "created automatically" in deck_issues[0].message
            # No ERROR-level issue for the deck
            assert not any(i.component == "Anki Deck" and i.severity == "ERROR" for i in result.issues)

        def test_ffmpeg_failure(self, test_config):
            """ffmpeg not found should be reported as error."""
            service = ValidationService(test_config)

            # AnkiConnect works
            anki_resp = MagicMock()
            anki_resp.status_code = 200
            anki_resp.json.return_value = {"result": 6, "error": None}

            deck_resp = MagicMock()
            deck_resp.json.return_value = {
                "result": [test_config.anki_deck_name],
                "error": None,
            }

            model_resp = MagicMock()
            model_resp.json.return_value = {
                "result": [test_config.anki_note_type],
                "error": None,
            }

            dispatch = {
                "version": anki_resp,
                "deckNames": deck_resp,
                "modelNames": model_resp,
            }

            def mock_post(url, **kwargs):
                action = kwargs.get("json", {}).get("action", "")
                return dispatch.get(action, MagicMock())

            with (
                patch("anki_miner.services._ankiconnect.requests.post", side_effect=mock_post),
                patch(
                    "anki_miner.services.validation_service.subprocess.run",
                    side_effect=FileNotFoundError(),
                ),
            ):
                result = service.validate_setup()

            assert result.ffmpeg_ok is False
            assert result.ankiconnect_ok is True
            assert any(i.component == "ffmpeg" for i in result.issues)
            # ffprobe shares the patched subprocess.run, so it fails too and is
            # reported as its own ERROR-severity component.
            assert result.ffprobe_ok is False
            assert any(i.component == "ffprobe" and i.severity == "ERROR" for i in result.issues)

        def test_ffprobe_failure_only(self, test_config):
            """ffprobe failing alone is surfaced as an ERROR and flips all_passed."""
            service = ValidationService(test_config)

            anki_resp = MagicMock()
            anki_resp.status_code = 200
            anki_resp.json.return_value = {"result": 6, "error": None}

            deck_resp = MagicMock()
            deck_resp.json.return_value = {"result": [test_config.anki_deck_name], "error": None}

            model_resp = MagicMock()
            model_resp.json.return_value = {"result": [test_config.anki_note_type], "error": None}

            field_resp = MagicMock()
            field_resp.json.return_value = {
                "result": list({v for v in test_config.anki_fields.values() if v}),
                "error": None,
            }

            dispatch = {
                "version": anki_resp,
                "deckNames": deck_resp,
                "modelNames": model_resp,
                "modelFieldNames": field_resp,
            }

            def mock_post(url, **kwargs):
                action = kwargs.get("json", {}).get("action", "")
                return dispatch.get(action, MagicMock())

            ok_result = MagicMock()
            ok_result.returncode = 0
            ok_result.stdout = "version 6.0"

            def mock_run(cmd, **kwargs):
                # cmd[0] is the resolved ffmpeg/ffprobe literal; fail only ffprobe.
                if "ffprobe" in cmd[0]:
                    raise FileNotFoundError()
                return ok_result

            with (
                patch("anki_miner.services._ankiconnect.requests.post", side_effect=mock_post),
                patch("anki_miner.services.validation_service.subprocess.run", side_effect=mock_run),
            ):
                result = service.validate_setup()

            assert result.ffmpeg_ok is True
            assert result.ffprobe_ok is False
            assert result.all_passed is False
            assert any(i.component == "ffprobe" and i.severity == "ERROR" for i in result.issues)
            assert not any(i.component == "ffmpeg" for i in result.issues)

    class TestCheckFieldNamesExist:
        """Tests for _check_field_names_exist method."""

        def test_all_fields_exist(self, test_config):
            service = ValidationService(test_config)

            mock_response = MagicMock()
            mock_response.json.return_value = {
                "result": [
                    "word",
                    "sentence",
                    "definition",
                    "picture",
                    "audio",
                    "expression_furigana",
                    "sentence_furigana",
                    "PitchPosition",
                    "PitchCategory",
                    "Frequency",
                ],
                "error": None,
            }

            with patch("anki_miner.services._ankiconnect.requests.post", return_value=mock_response):
                success, message = service._check_field_names_exist()

            assert success is True
            assert "All configured fields exist" in message

        def test_missing_fields_returns_failure(self, test_config):
            service = ValidationService(test_config)

            mock_response = MagicMock()
            mock_response.json.return_value = {
                "result": ["word", "sentence"],
                "error": None,
            }

            with patch("anki_miner.services._ankiconnect.requests.post", return_value=mock_response):
                success, message = service._check_field_names_exist()

            assert success is False
            assert "not found on note type" in message

        def test_error_response_returns_failure(self, test_config):
            service = ValidationService(test_config)

            mock_response = MagicMock()
            mock_response.json.return_value = {
                "result": None,
                "error": "model not found",
            }

            with patch("anki_miner.services._ankiconnect.requests.post", return_value=mock_response):
                success, message = service._check_field_names_exist()

            assert success is False
            assert "Error fetching fields" in message

        def test_exception_returns_failure(self, test_config):
            service = ValidationService(test_config)

            import requests

            with patch(
                "anki_miner.services._ankiconnect.requests.post",
                side_effect=requests.exceptions.ConnectionError(),
            ):
                success, message = service._check_field_names_exist()

            assert success is False
            assert "Error checking fields" in message


class TestOptionalResourceWarnings:
    """Warnings when an optional feature is enabled but its file is missing."""

    @staticmethod
    def _has_warning(result, component_substring):
        return any(issue.severity == "WARNING" and component_substring in issue.component for issue in result.issues)

    @staticmethod
    def _patch_external_checks(monkeypatch):
        """Stub network/binary checks so validate_setup focuses on file checks."""
        from anki_miner.services import validation_service

        monkeypatch.setattr(
            validation_service.ValidationService,
            "_check_ankiconnect",
            lambda self: (True, "ok"),
        )
        monkeypatch.setattr(
            validation_service.ValidationService,
            "_check_ffmpeg",
            lambda self: (True, "ok"),
        )
        monkeypatch.setattr(
            validation_service.ValidationService,
            "_check_ffprobe",
            lambda self: (True, "ok"),
        )
        monkeypatch.setattr(
            validation_service.ValidationService,
            "_check_deck_exists",
            lambda self: (True, "ok"),
        )
        monkeypatch.setattr(
            validation_service.ValidationService,
            "_check_note_type_exists",
            lambda self: (True, "ok"),
        )
        monkeypatch.setattr(
            validation_service.ValidationService,
            "_check_field_names_exist",
            lambda self: (True, "ok"),
        )

    def test_warns_when_indexed_dict_enabled_but_missing(self, test_config, monkeypatch, tmp_path):
        from dataclasses import replace

        self._patch_external_checks(monkeypatch)
        # Point dicts_root at an empty tmp_path so the validator finds nothing
        # on disk instead of looking at the developer's real ~/.anki_miner/dicts/.
        config = replace(test_config, dicts_root=tmp_path / "dicts")
        result = ValidationService(config).validate_setup()

        assert self._has_warning(result, "Offline Dictionary")
        assert result.all_passed  # warnings must not fail validation

    def test_no_warning_when_indexed_dict_present(self, test_config, monkeypatch, tmp_path):
        from dataclasses import replace

        from anki_miner.config import ChainEntry

        self._patch_external_checks(monkeypatch)

        # Point dicts_root at tmp_path/dicts and stage the dict folder so the
        # validator finds the index.sqlite.
        dicts_root = tmp_path / "dicts"
        dict_id = "test-dict"
        target = dicts_root / dict_id
        target.mkdir(parents=True)
        (target / "index.sqlite").write_bytes(b"placeholder")

        chain = (
            ChainEntry(kind="indexed", dict_id=dict_id, enabled=True),
            ChainEntry(kind="jisho", dict_id=None, enabled=True),
        )
        config = replace(test_config, dictionary_chain=chain, dicts_root=dicts_root)
        result = ValidationService(config).validate_setup()

        assert not self._has_warning(result, "Offline Dictionary")

    def test_no_warning_when_indexed_chain_entries_all_disabled(self, test_config, monkeypatch):
        from dataclasses import replace

        from anki_miner.config import ChainEntry

        self._patch_external_checks(monkeypatch)
        chain = (
            ChainEntry(kind="indexed", dict_id="jmdict-english", enabled=False),
            ChainEntry(kind="jisho", dict_id=None, enabled=True),
        )
        config = replace(test_config, dictionary_chain=chain)
        result = ValidationService(config).validate_setup()

        assert not self._has_warning(result, "Offline Dictionary")

    def test_warns_when_pitch_accent_enabled_but_file_missing(self, test_config, monkeypatch, tmp_path):
        from dataclasses import replace

        self._patch_external_checks(monkeypatch)
        config = replace(
            test_config,
            use_pitch_accent=True,
            pitch_accent_path=tmp_path / "missing_pitch.csv",
        )
        result = ValidationService(config).validate_setup()

        assert self._has_warning(result, "Pitch Accent")

    def test_warns_when_frequency_enabled_but_file_missing(self, test_config, monkeypatch, tmp_path):
        from dataclasses import replace

        self._patch_external_checks(monkeypatch)
        config = replace(
            test_config,
            use_frequency_data=True,
            frequency_list_path=tmp_path / "missing_freq.csv",
        )
        result = ValidationService(config).validate_setup()

        assert self._has_warning(result, "Frequency Data")

    def test_no_warning_when_features_disabled(self, test_config, monkeypatch):
        from dataclasses import replace

        from anki_miner.config import ChainEntry

        self._patch_external_checks(monkeypatch)
        # Disable every indexed entry so the chain validation skips itself.
        chain = (ChainEntry(kind="jisho", dict_id=None, enabled=True),)
        config = replace(
            test_config,
            dictionary_chain=chain,
            use_pitch_accent=False,
            use_frequency_data=False,
        )
        result = ValidationService(config).validate_setup()

        assert not self._has_warning(result, "Offline Dictionary")
        assert not self._has_warning(result, "Pitch Accent")
        assert not self._has_warning(result, "Frequency Data")
