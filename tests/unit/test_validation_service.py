"""Tests for validation_service module."""

import subprocess
import sys
import threading
import time
from dataclasses import replace
from datetime import date
from unittest.mock import MagicMock, patch

import pytest

from anki_miner.services import validation_service
from anki_miner.services.validation_service import ValidationService
from anki_miner.utils import ytdlp_resolver


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

            with patch("anki_miner.services.validation_service.subprocess.run", return_value=mock_result) as mock_run:
                success, message = service._check_ffmpeg()

            assert success is True
            assert "ffmpeg version" in message
            assert mock_run.call_args.kwargs["stdin"] is subprocess.DEVNULL

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
            assert "created automatically" not in message

        def test_missing_deck_message_no_longer_promises_autocreate(self, test_config):
            """The message must not promise the removed auto-creation behaviour."""
            from dataclasses import replace  # noqa: PLC0415 — module convention

            service = ValidationService(replace(test_config, anki_deck_name="Nope"))

            mock_response = MagicMock()
            mock_response.json.return_value = {"result": ["Default", "Real"], "error": None}

            with patch("anki_miner.services._ankiconnect.requests.post", return_value=mock_response):
                success, message = service._check_deck_exists()

            assert success is False
            assert "Nope" in message
            assert "created automatically" not in message

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
            fake_ffmpeg.chmod(0o755)
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

        def test_missing_fields_fail_only_field_mapping_check(self, test_config, monkeypatch):
            service = ValidationService(test_config)
            monkeypatch.setattr(service, "_check_ankiconnect", lambda: (True, "ok"))
            monkeypatch.setattr(service, "_check_ffmpeg", lambda: (True, "ok"))
            monkeypatch.setattr(service, "_check_ffprobe", lambda: (True, "ok"))
            monkeypatch.setattr(service, "_check_alass", lambda: (True, "ok"))
            monkeypatch.setattr(service, "_check_ytdlp", lambda: (True, "2026.08.01 [venv]"))
            monkeypatch.setattr(service, "_check_deck_exists", lambda: (True, "ok"))
            monkeypatch.setattr(service, "_check_note_type_exists", lambda: (True, "ok"))
            monkeypatch.setattr(
                service,
                "_check_field_names_exist",
                lambda: (False, "Field(s) Picture not found"),
            )
            monkeypatch.setattr(service, "_check_offline_dictionary", lambda: (True, "ok"))

            result = service.validate_setup()

            assert result.field_mapping_ok is False
            assert result.all_passed is False
            assert result.ankiconnect_ok is True
            assert result.ffmpeg_ok is True
            assert result.ffprobe_ok is True
            assert result.deck_exists is True
            assert result.note_type_exists is True
            assert [(issue.component, issue.severity) for issue in result.issues] == [("Field Mapping", "WARNING")]

        def test_all_pass(self, test_config, tmp_path):
            """All checks pass when external services respond correctly."""
            from dataclasses import replace

            from anki_miner.config import ChainEntry

            # A usable offline dictionary is part of a clean bill of health now
            # (D26), so stage one rather than emptying the chain — an empty chain
            # is itself a warning.
            dicts_root = tmp_path / "dicts"
            TestOptionalResourceWarnings._stage_dict(dicts_root, "test-dict")
            test_config = replace(
                test_config,
                dicts_root=dicts_root,
                dictionary_chain=(
                    ChainEntry(kind="indexed", dict_id="test-dict", enabled=True),
                    ChainEntry(kind="jisho", dict_id=None, enabled=True),
                ),
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
                "result": [test_config.anki_fields["word"]]
                + sorted({v for v in test_config.anki_fields.values() if v} - {test_config.anki_fields["word"]}),
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

        def test_missing_deck_produces_error_not_warning(self, test_config):
            """A missing deck fails the run (no auto-creation), so it must surface as ERROR."""
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
                "result": [test_config.anki_fields["word"]]
                + sorted({v for v in test_config.anki_fields.values() if v} - {test_config.anki_fields["word"]}),
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
            assert deck_issues[0].severity == "ERROR"
            assert "created automatically" not in deck_issues[0].message
            # No WARNING-level issue for the deck
            assert not any(i.component == "Anki Deck" and i.severity == "WARNING" for i in result.issues)

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
                "result": [test_config.anki_fields["word"]]
                + sorted({v for v in test_config.anki_fields.values() if v} - {test_config.anki_fields["word"]}),
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

        def test_active_card_type_marker_is_checked(self, test_config):
            from dataclasses import replace

            service = ValidationService(replace(test_config, card_type="click"))
            mock_response = MagicMock()
            mock_response.json.return_value = {
                "result": [value for value in test_config.anki_fields.values() if value],
                "error": None,
            }

            with patch("anki_miner.services._ankiconnect.requests.post", return_value=mock_response):
                success, message = service._check_field_names_exist()

            assert success is False
            assert "IsClickCard" in message

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

    @staticmethod
    def _stage_dict(dicts_root, dict_id, *, entries=1234, schema_version=None):
        """Write a dictionary slot the registry scan can read without SQLite.

        The meta sidecar is authoritative while it is newer than
        ``index.sqlite``, so a placeholder byte plus a sidecar is a complete,
        fast stand-in for a real index.
        """
        import json

        from anki_miner.services.dictionary.storage import SCHEMA_VERSION

        target = dicts_root / dict_id
        target.mkdir(parents=True)
        (target / "index.sqlite").write_bytes(b"placeholder")
        (target / "meta.json").write_text(
            json.dumps(
                {
                    "source_name": dict_id,
                    "format": "yomitan",
                    "schema_version": str(SCHEMA_VERSION if schema_version is None else schema_version),
                    "entry_count": str(entries),
                }
            ),
            encoding="utf-8",
        )
        return target

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

        dicts_root = tmp_path / "dicts"
        self._stage_dict(dicts_root, "test-dict")

        chain = (
            ChainEntry(kind="indexed", dict_id="test-dict", enabled=True),
            ChainEntry(kind="jisho", dict_id=None, enabled=True),
        )
        config = replace(test_config, dictionary_chain=chain, dicts_root=dicts_root)
        result = ValidationService(config).validate_setup()

        assert not self._has_warning(result, "Offline Dictionary")
        # The success text is kept so a readiness screen can name the dictionary
        # without re-scanning; it is discarded everywhere else.
        assert "test-dict" in result.tool_versions["offline-dictionary"]

    def test_warns_when_indexed_chain_entries_all_disabled(self, test_config, monkeypatch, tmp_path):
        """A disabled-only chain is *not* readiness (D26).

        This used to assert the opposite. Every enabled indexed slot being
        switched off is exactly the state that mines cards with no definition,
        and calling it healthy is what let first-run setup finish in a state
        guaranteed to fail the first mine.
        """
        from dataclasses import replace

        from anki_miner.config import ChainEntry

        self._patch_external_checks(monkeypatch)
        chain = (
            ChainEntry(kind="indexed", dict_id="jmdict-english", enabled=False),
            ChainEntry(kind="jisho", dict_id=None, enabled=True),
        )
        config = replace(test_config, dictionary_chain=chain, dicts_root=tmp_path / "dicts")
        result = ValidationService(config).validate_setup()

        assert self._has_warning(result, "Offline Dictionary")

    def test_warns_when_no_offline_dictionary_is_configured(self, test_config, monkeypatch, tmp_path):
        from dataclasses import replace

        from anki_miner.config import ChainEntry

        self._patch_external_checks(monkeypatch)
        chain = (ChainEntry(kind="jisho", dict_id=None, enabled=True),)
        config = replace(test_config, dictionary_chain=chain, dicts_root=tmp_path / "dicts")
        result = ValidationService(config).validate_setup()

        assert self._has_warning(result, "Offline Dictionary")
        # Pitch/frequency "resource missing" warnings were removed with their
        # flags; assert they never surface.
        assert not self._has_warning(result, "Pitch Accent")
        assert not self._has_warning(result, "Frequency Data")

    def test_check_offline_dictionary_reports_each_failure_shape(self, test_config, tmp_path):
        """Four unusable states, four different repairs, four distinct messages."""
        from dataclasses import replace

        from anki_miner.config import ChainEntry

        dicts_root = tmp_path / "dicts"
        self._stage_dict(dicts_root, "stale-dict", schema_version=1)
        self._stage_dict(dicts_root, "empty-dict", entries=0)
        self._stage_dict(dicts_root, "good-dict")

        def _check(dict_id, enabled=True):
            chain = (ChainEntry(kind="indexed", dict_id=dict_id, enabled=enabled),)
            config = replace(test_config, dictionary_chain=chain, dicts_root=dicts_root)
            return ValidationService(config).check_offline_dictionary()

        assert _check("good-dict")[0] is True

        ok, message = _check("good-dict", enabled=False)
        assert ok is False
        assert "No offline dictionary is enabled" in message

        ok, message = _check("absent-dict")
        assert ok is False
        assert "not found on disk" in message

        ok, message = _check("stale-dict")
        assert ok is False
        assert "reimporting" in message

        ok, message = _check("empty-dict")
        assert ok is False
        assert "no entries" in message


class TestCheckAlass:
    """Tests for _check_alass method — alass is optional (non-fatal)."""

    def test_success(self, test_config):
        """Present alass binary → ok result with version info."""
        service = ValidationService(test_config)

        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "alass 0.6.0"

        with patch("anki_miner.services.validation_service.subprocess.run", return_value=mock_result):
            ok, message = service._check_alass()

        assert ok is True
        assert "alass" in message

    def test_not_found(self, test_config):
        """Missing alass binary → ok=False with descriptive message."""
        service = ValidationService(test_config)

        with patch(
            "anki_miner.services.validation_service.subprocess.run",
            side_effect=FileNotFoundError(),
        ):
            ok, message = service._check_alass()

        assert ok is False
        assert "alass" in message.lower()

    def test_timeout(self, test_config):
        """alass check timeout → ok=False."""
        service = ValidationService(test_config)

        with patch(
            "anki_miner.services.validation_service.subprocess.run",
            side_effect=subprocess.TimeoutExpired("alass", 10),
        ):
            ok, message = service._check_alass()

        assert ok is False
        assert "timed out" in message

    def test_uses_double_dash_version_flag(self, test_config):
        """alass wants --version, unlike ffmpeg's single-dash -version.

        Both now share _check_tool, so the flag has to be passed per tool; getting it
        wrong would make a present binary look broken.
        """
        service = ValidationService(test_config)
        mock_result = MagicMock(returncode=0, stdout="alass 0.6.0")

        with patch("anki_miner.services.validation_service.subprocess.run", return_value=mock_result) as run:
            service._check_alass()

        assert run.call_args.args[0][1] == "--version"


class TestCheckYtdlp:
    """yt-dlp is optional (YouTube tab only), so absence is a WARNING."""

    def test_success(self, test_config):
        service = ValidationService(test_config)
        mock_result = MagicMock(returncode=0, stdout="2026.06.09\n")

        with patch("anki_miner.services.validation_service.subprocess.run", return_value=mock_result) as run:
            ok, message = service._check_ytdlp()

        assert ok is True
        assert "2026.06.09" in message
        # App-owned invocation: --ignore-config so the user's yt-dlp config cannot
        # reach a command this app issues on its own behalf.
        assert run.call_args.args[0][1:] == ["--ignore-config", "--version"]

    def test_not_found_points_at_the_installer(self, test_config):
        service = ValidationService(test_config)

        with patch(
            "anki_miner.services.validation_service.subprocess.run",
            side_effect=FileNotFoundError(),
        ):
            ok, message = service._check_ytdlp()

        assert ok is False
        assert "Update yt-dlp now" in message

    def test_unverified_managed_binary_reports_instead_of_raising(self, test_config):
        """resolve_ytdlp can raise; validate_setup documents itself as never raising.

        Every other check resolves outside its try, which is safe only because those
        resolvers cannot raise. This one must resolve inside, or an unverified
        app-managed binary on PATH takes the whole startup validation down over an
        optional tool.
        """
        service = ValidationService(test_config)

        with patch(
            "anki_miner.services.validation_service.resolve_ytdlp",
            side_effect=FileNotFoundError("Refusing unverified managed yt-dlp executable on PATH"),
        ):
            ok, message = service._check_ytdlp()

        assert ok is False
        assert "unverified" in message.lower()

    def test_busy_lock_reports_instead_of_waiting(self, test_config, monkeypatch):
        """A running yt-dlp task must not park the validation worker for hours.

        The generation lock is held for the whole managed-binary transfer (up to the
        3h supervisor timeout); waiting on it froze System Health behind a download.
        """
        monkeypatch.setattr(validation_service, "_YTDLP_LOCK_WAIT_SECONDS", 0.2)
        service = ValidationService(test_config)
        released = threading.Event()
        holding = threading.Event()

        def hold() -> None:
            with ytdlp_resolver.managed_ytdlp_lock():
                holding.set()
                released.wait(10)

        holder = threading.Thread(target=hold)
        holder.start()
        try:
            assert holding.wait(10)
            with patch(
                "anki_miner.services.validation_service.resolve_ytdlp",
                side_effect=AssertionError("must not resolve while another task holds the lock"),
            ):
                ok, message = service._check_ytdlp()
        finally:
            released.set()
            holder.join(10)
        assert not holder.is_alive()

        assert ok is False
        assert "busy" in message.lower()

    def test_transient_holder_is_waited_out_rather_than_reported_busy(self, test_config, monkeypatch):
        """A sub-second holder must not cost a healthy install its version.

        At startup the validation worker and the scheduled yt-dlp auto-update
        overlap; a cold-cache SHA-256 of the managed binary or a ``--version``
        probe holds the lock about a second. A non-blocking acquire turned that
        into a WARNING and an empty tool version on a working install.
        """
        monkeypatch.setattr(validation_service, "_YTDLP_LOCK_WAIT_SECONDS", 10.0)
        service = ValidationService(test_config)
        holding = threading.Event()

        def hold() -> None:
            with ytdlp_resolver.managed_ytdlp_lock():
                holding.set()
                time.sleep(0.2)

        holder = threading.Thread(target=hold)
        holder.start()
        try:
            assert holding.wait(10)
            mock_result = MagicMock(returncode=0, stdout="2026.06.09\n")
            with patch("anki_miner.services.validation_service.subprocess.run", return_value=mock_result):
                ok, message = service._check_ytdlp()
        finally:
            holder.join(10)
        assert not holder.is_alive()

        assert ok is True
        assert "2026.06.09" in message

    def test_validate_setup_does_not_raise_on_unverified_binary(self, test_config):
        service = ValidationService(test_config)

        with (
            patch(
                "anki_miner.services.validation_service.resolve_ytdlp",
                side_effect=FileNotFoundError("Refusing unverified managed yt-dlp executable on PATH"),
            ),
            patch.object(ValidationService, "_check_ankiconnect", return_value=(False, "down")),
        ):
            result = service.validate_setup()

        assert any(issue.component == "yt-dlp" and issue.severity == "WARNING" for issue in result.issues)


class TestYtdlpStalenessWarning:
    """The nudge is gated on auto-update being OFF."""

    def test_silent_when_auto_update_enabled(self, test_config):
        service = ValidationService(replace(test_config, auto_update_ytdlp=True))
        assert service._ytdlp_staleness_warning("2020.01.01 [app-managed]") is None

    def test_warns_when_opted_out_and_binary_is_old(self, test_config):
        service = ValidationService(replace(test_config, auto_update_ytdlp=False))
        message = service._ytdlp_staleness_warning("2020.01.01 [system PATH]")
        assert message is not None
        assert "2020.01.01" in message
        assert "Settings → YouTube" in message

    def test_silent_for_a_recent_binary(self, test_config):
        service = ValidationService(replace(test_config, auto_update_ytdlp=False))
        today = date.today().strftime("%Y.%m.%d")
        assert service._ytdlp_staleness_warning(f"{today} [app-managed]") is None

    @pytest.mark.parametrize("version_text", ["", "nightly", "2026.06 [x]", "not-a-date [x]"])
    def test_silent_on_unparseable_versions(self, test_config, version_text):
        """Nightly/dev builds must not produce a false alarm."""
        service = ValidationService(replace(test_config, auto_update_ytdlp=False))
        assert service._ytdlp_staleness_warning(version_text) is None

    def test_missing_alass_produces_warning_not_error_in_validate_setup(self, test_config, monkeypatch):
        """Missing alass must NOT make validate_setup fail — it must surface as WARNING."""
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
        # Simulate missing alass
        monkeypatch.setattr(
            validation_service.ValidationService,
            "_check_alass",
            lambda self: (False, "alass not found — subtitle retiming will be unavailable"),
        )

        from dataclasses import replace

        from anki_miner.config import ChainEntry

        config = replace(
            test_config,
            dictionary_chain=(ChainEntry(kind="jisho", dict_id=None, enabled=True),),
        )
        result = ValidationService(config).validate_setup()

        # Must not block startup
        assert result.all_passed is True
        # Must surface as a WARNING issue
        alass_issues = [i for i in result.issues if i.component == "alass"]
        assert len(alass_issues) == 1
        assert alass_issues[0].severity == "WARNING"
        # Must NOT be an ERROR
        assert not any(i.component == "alass" and i.severity == "ERROR" for i in result.issues)

    def test_present_alass_produces_no_issue_in_validate_setup(self, test_config, monkeypatch):
        """A present alass binary should add no issue to validate_setup results."""
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
        monkeypatch.setattr(
            validation_service.ValidationService,
            "_check_alass",
            lambda self: (True, "alass 0.6.0 [system PATH]"),
        )

        from dataclasses import replace

        from anki_miner.config import ChainEntry

        config = replace(
            test_config,
            dictionary_chain=(ChainEntry(kind="jisho", dict_id=None, enabled=True),),
        )
        result = ValidationService(config).validate_setup()

        assert result.all_passed is True
        assert not any(i.component == "alass" for i in result.issues)


class TestPublicChecks:
    """Public thin wrappers over the private check methods (Task 3 setup wizard)."""

    def test_check_ankiconnect_delegates_to_private(self, test_config):
        service = ValidationService(test_config)
        with patch.object(service, "_check_ankiconnect", return_value=(True, "v6 ok")) as priv:
            assert service.check_ankiconnect() == (True, "v6 ok")
        priv.assert_called_once_with()

    def test_check_ankiconnect_returns_same_tuple_on_success(self, test_config):
        service = ValidationService(test_config)
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"result": 6, "error": None}
        with patch("anki_miner.services._ankiconnect.requests.post", return_value=mock_response):
            public = service.check_ankiconnect()
            private = service._check_ankiconnect()
        assert public == private
        assert public[0] is True

    def test_check_field_names_delegates_to_private(self, test_config):
        service = ValidationService(test_config)
        with patch.object(service, "_check_field_names_exist", return_value=(False, "missing X")) as priv:
            assert service.check_field_names() == (False, "missing X")
        priv.assert_called_once_with()

    def test_check_field_names_returns_same_tuple(self, test_config):
        service = ValidationService(test_config)
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"result": ["word", "sentence"], "error": None}
        with patch("anki_miner.services._ankiconnect.requests.post", return_value=mock_response):
            public = service.check_field_names()
        assert isinstance(public, tuple) and len(public) == 2
        assert isinstance(public[0], bool) and isinstance(public[1], str)

    def test_check_resource_readiness_gathers_all_three_families(self, test_config):
        from anki_miner.services.validation_service import ResourceReadiness

        service = ValidationService(test_config)
        with (
            patch.object(service, "_check_offline_dictionary", return_value=(True, "JMdict (100 entries)")),
            patch.object(service, "_check_frequency_sources", return_value=(None, "")),
            patch.object(service, "_check_pitch_sources", return_value=(False, "needs reimport")),
        ):
            readiness = service.check_resource_readiness()

        assert isinstance(readiness, ResourceReadiness)
        assert readiness.dictionary == (True, "JMdict (100 entries)")
        assert readiness.frequency == (None, "")
        assert readiness.pitch == (False, "needs reimport")

    def test_check_resource_readiness_reuses_the_public_dictionary_check(self, test_config):
        service = ValidationService(test_config)
        with (
            patch.object(service, "check_offline_dictionary", return_value=(True, "ok")) as pub,
            patch.object(service, "_check_frequency_sources", return_value=(None, "")),
            patch.object(service, "_check_pitch_sources", return_value=(None, "")),
        ):
            service.check_resource_readiness()
        pub.assert_called_once_with()


class TestOptionalIndexedResourceChecks:
    """Frequency and pitch: reported when broken, silent when simply absent.

    The removed ``use_frequency_data`` / ``use_pitch_accent`` flags made "wanted
    but missing" unrepresentable, and that stands. "Wanted but BROKEN by an
    upgrade" is a different state and is the only one these report.
    """

    @staticmethod
    def _build_freq(root, source_id, *, stale=False, entries=1, name=None):
        from anki_miner.services.frequency import storage

        storage.build_index(
            root / source_id / "index.sqlite",
            [("猫", "ねこ", 100, None)][:entries],
            {
                "schema_version": str(storage.SCHEMA_VERSION - 1 if stale else storage.SCHEMA_VERSION),
                "format": "csv",
                "source_name": name or source_id,
                "entry_count": str(entries),
            },
        )

    @staticmethod
    def _build_pitch(root, source_id, *, stale=False, entries=1, name=None):
        from anki_miner.services.pitch_accent import storage

        storage.build_index(
            root / source_id / "index.sqlite",
            [("ねこ", "猫", "1", "", "")][:entries],
            {
                "schema_version": str(storage.SCHEMA_VERSION - 1 if stale else storage.SCHEMA_VERSION),
                "format": "csv",
                "source_name": name or source_id,
                "source_revision": "",
                "import_date": "2026-01-01T00:00:00+00:00",
                "entry_count": str(entries),
            },
        )

    def test_unconfigured_returns_none_not_a_failure(self, test_config, tmp_path):
        config = replace(test_config, freqs_root=tmp_path / "freqs", pitch_root=tmp_path / "pitch")
        service = ValidationService(config)

        assert service._check_frequency_sources() == (None, "")
        assert service._check_pitch_sources() == (None, "")

    def test_usable_source_reports_its_name_and_count(self, test_config, tmp_path):
        from anki_miner.config import FreqEntry

        freqs_root = tmp_path / "freqs"
        self._build_freq(freqs_root, "jpdb", name="JPDB")
        config = replace(test_config, freqs_root=freqs_root, frequency_chain=(FreqEntry("jpdb"),))

        ok, message = ValidationService(config)._check_frequency_sources()

        assert ok is True
        assert "JPDB" in message
        assert "1 entries" in message

    def test_stale_source_warns_and_names_reimport_all(self, test_config, tmp_path):
        from anki_miner.config import FreqEntry

        freqs_root = tmp_path / "freqs"
        self._build_freq(freqs_root, "jpdb", stale=True, name="JPDB")
        config = replace(test_config, freqs_root=freqs_root, frequency_chain=(FreqEntry("jpdb"),))

        ok, message = ValidationService(config)._check_frequency_sources()

        assert ok is False
        assert "JPDB" in message
        assert "Settings → Frequency → Reimport All" in message

    def test_empty_source_warns(self, test_config, tmp_path):
        from anki_miner.config import PitchSourceEntry

        pitch_root = tmp_path / "pitch"
        self._build_pitch(pitch_root, "nhk", entries=0, name="NHK")
        config = replace(test_config, pitch_root=pitch_root, pitch_chain=(PitchSourceEntry("nhk"),))

        ok, message = ValidationService(config)._check_pitch_sources()

        assert ok is False
        assert "no entries" in message

    def test_enabled_but_absent_from_disk_stays_silent(self, test_config, tmp_path):
        """The deliberately-removed case: a deletion is not a validation failure."""
        from anki_miner.config import FreqEntry, PitchSourceEntry

        config = replace(
            test_config,
            freqs_root=tmp_path / "freqs",
            pitch_root=tmp_path / "pitch",
            frequency_chain=(FreqEntry("gone"),),
            pitch_chain=(PitchSourceEntry("gone"),),
        )
        service = ValidationService(config)

        assert service._check_frequency_sources() == (None, "")
        assert service._check_pitch_sources() == (None, "")

    def test_stale_pitch_reaches_validate_setup_as_a_warning(self, test_config, tmp_path, monkeypatch):
        from anki_miner.config import PitchSourceEntry

        TestOptionalResourceWarnings._patch_external_checks(monkeypatch)
        pitch_root = tmp_path / "pitch"
        self._build_pitch(pitch_root, "nhk", stale=True, name="NHK")
        config = replace(
            test_config,
            pitch_root=pitch_root,
            pitch_chain=(PitchSourceEntry("nhk"),),
            media_temp_folder=tmp_path / "temp",
        )

        result = ValidationService(config).validate_setup()

        assert TestOptionalResourceWarnings._has_warning(result, "Pitch Sources")
        # The System Health row keys off the component string; a mismatch there
        # silently drops the warning off the screen.
        assert any(issue.component == "Pitch Sources" for issue in result.issues)
        assert "pitch-sources" not in result.tool_versions

    def test_unconfigured_family_records_an_empty_version_string(self, test_config, tmp_path, monkeypatch):
        """System Health renders the row as unknown off exactly this."""
        TestOptionalResourceWarnings._patch_external_checks(monkeypatch)
        config = replace(
            test_config,
            freqs_root=tmp_path / "freqs",
            pitch_root=tmp_path / "pitch",
            media_temp_folder=tmp_path / "temp",
        )

        result = ValidationService(config).validate_setup()

        assert result.tool_versions["frequency-sources"] == ""
        assert result.tool_versions["pitch-sources"] == ""
        assert result.tool_versions["audio-packs"] == ""
        assert not TestOptionalResourceWarnings._has_warning(result, "Frequency Sources")
        assert not TestOptionalResourceWarnings._has_warning(result, "Pitch Sources")
        assert not TestOptionalResourceWarnings._has_warning(result, "Audio Packs")

    # -- audio packs ----------------------------------------------------

    @staticmethod
    def _build_pack(root, pack_id, *, stale=False, entries=1, name=None, source_dir=None):
        from anki_miner.services.audio_packs import storage

        pack_source = source_dir if source_dir is not None else root / pack_id / "media"
        pack_source.mkdir(parents=True, exist_ok=True)
        db = root / pack_id / "index.sqlite"
        storage.create_index(db)
        storage.write_meta(
            db,
            {
                "pack_id": pack_id,
                "source": name or pack_id,
                "format": "ajt",
                "entry_count": str(entries),
                "schema_version": str(storage.SCHEMA_VERSION - 1 if stale else storage.SCHEMA_VERSION),
                "pack_dir": str(pack_source),
            },
        )

    def test_online_only_audio_chain_is_unconfigured_not_a_failure(self, test_config, tmp_path):
        """A chain of only JPod101/Google TTS has no index to be stale."""
        from anki_miner.config import AudioSourceEntry

        config = replace(
            test_config,
            audio_packs_root=tmp_path / "audio_packs",
            expression_audio_chain=(AudioSourceEntry(kind="jpod101"), AudioSourceEntry(kind="googletts")),
        )

        assert ValidationService(config)._check_audio_packs() == (None, "")

    def test_usable_pack_reports_its_name_and_count(self, test_config, tmp_path):
        from anki_miner.config import AudioSourceEntry

        packs_root = tmp_path / "audio_packs"
        self._build_pack(packs_root, "nhk16", entries=5000, name="NHK 2016")
        config = replace(
            test_config,
            anki_fields={**test_config.anki_fields, "expression_audio": "ExpressionAudio"},
            audio_packs_root=packs_root,
            expression_audio_chain=(AudioSourceEntry(kind="pack", pack_id="nhk16"),),
        )

        ok, message = ValidationService(config)._check_audio_packs()

        assert ok is True
        assert "NHK 2016" in message
        assert "5,000 entries" in message

    def test_stale_pack_warns_and_names_reimport_all(self, test_config, tmp_path):
        from anki_miner.config import AudioSourceEntry

        packs_root = tmp_path / "audio_packs"
        self._build_pack(packs_root, "nhk16", stale=True, name="NHK 2016")
        config = replace(
            test_config,
            anki_fields={**test_config.anki_fields, "expression_audio": "ExpressionAudio"},
            audio_packs_root=packs_root,
            expression_audio_chain=(AudioSourceEntry(kind="pack", pack_id="nhk16"),),
        )

        ok, message = ValidationService(config)._check_audio_packs()

        assert ok is False
        assert "NHK 2016" in message
        assert "Settings → Audio → Reimport All" in message

    def test_audio_packs_report_not_configured_when_field_unmapped(self, test_config, tmp_path):
        """A pack is only ever consulted when expression_audio is mapped too.

        An enabled, schema-current, usable pack must still report itself as
        unconfigured (not an OK-green) when the field the fetcher gates on is
        unmapped — the same two-part condition the fetcher and the pre-run
        stale-reimport gate use.
        """
        from anki_miner.config import AudioSourceEntry

        packs_root = tmp_path / "audio_packs"
        self._build_pack(packs_root, "nhk16", entries=5000, name="NHK 2016")
        assert not test_config.anki_fields.get("expression_audio")
        config = replace(
            test_config,
            audio_packs_root=packs_root,
            expression_audio_chain=(AudioSourceEntry(kind="pack", pack_id="nhk16"),),
        )

        assert ValidationService(config)._check_audio_packs() == (None, "")

    def test_pack_absent_from_disk_stays_silent(self, test_config, tmp_path):
        from anki_miner.config import AudioSourceEntry

        config = replace(
            test_config,
            audio_packs_root=tmp_path / "audio_packs",
            expression_audio_chain=(AudioSourceEntry(kind="pack", pack_id="gone"),),
        )

        assert ValidationService(config)._check_audio_packs() == (None, "")

    def test_pack_whose_audio_folder_moved_stays_silent(self, test_config, tmp_path):
        """Unreachable audio degrades to the online sources; not upgrade damage."""
        from anki_miner.config import AudioSourceEntry

        packs_root = tmp_path / "audio_packs"
        self._build_pack(packs_root, "nhk16", name="NHK 2016", source_dir=tmp_path / "on_a_usb_stick")
        (tmp_path / "on_a_usb_stick").rmdir()
        config = replace(
            test_config,
            audio_packs_root=packs_root,
            expression_audio_chain=(AudioSourceEntry(kind="pack", pack_id="nhk16"),),
        )

        assert ValidationService(config)._check_audio_packs() == (None, "")

    def test_stale_pack_reaches_validate_setup_as_a_warning(self, test_config, tmp_path, monkeypatch):
        from anki_miner.config import AudioSourceEntry

        TestOptionalResourceWarnings._patch_external_checks(monkeypatch)
        packs_root = tmp_path / "audio_packs"
        self._build_pack(packs_root, "nhk16", stale=True, name="NHK 2016")
        config = replace(
            test_config,
            anki_fields={**test_config.anki_fields, "expression_audio": "ExpressionAudio"},
            audio_packs_root=packs_root,
            expression_audio_chain=(AudioSourceEntry(kind="pack", pack_id="nhk16"),),
            media_temp_folder=tmp_path / "temp",
        )

        result = ValidationService(config).validate_setup()

        assert TestOptionalResourceWarnings._has_warning(result, "Audio Packs")
        # The System Health row keys off the component string; a mismatch there
        # silently drops the warning off the screen.
        assert any(issue.component == "Audio Packs" for issue in result.issues)
        assert "audio-packs" not in result.tool_versions
