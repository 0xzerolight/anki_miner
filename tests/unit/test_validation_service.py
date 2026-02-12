"""Tests for validation_service module."""

import subprocess
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

            with patch(
                "anki_miner.services.validation_service.requests.post", return_value=mock_response
            ):
                success, message = service._check_ankiconnect()

            assert success is True
            assert "v6" in message

        def test_connection_error(self, test_config):
            service = ValidationService(test_config)

            import requests

            with patch(
                "anki_miner.services.validation_service.requests.post",
                side_effect=requests.exceptions.ConnectionError(),
            ):
                success, message = service._check_ankiconnect()

            assert success is False
            assert "Cannot connect" in message

        def test_timeout(self, test_config):
            service = ValidationService(test_config)

            import requests

            with patch(
                "anki_miner.services.validation_service.requests.post",
                side_effect=requests.exceptions.Timeout(),
            ):
                success, message = service._check_ankiconnect()

            assert success is False
            assert "timed out" in message

        def test_non_200_status(self, test_config):
            service = ValidationService(test_config)

            mock_response = MagicMock()
            mock_response.status_code = 500

            with patch(
                "anki_miner.services.validation_service.requests.post", return_value=mock_response
            ):
                success, message = service._check_ankiconnect()

            assert success is False
            assert "non-200" in message

        def test_ankiconnect_error(self, test_config):
            service = ValidationService(test_config)

            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.json.return_value = {"result": None, "error": "Some error"}

            with patch(
                "anki_miner.services.validation_service.requests.post", return_value=mock_response
            ):
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

            with patch(
                "anki_miner.services.validation_service.subprocess.run", return_value=mock_result
            ):
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

            with patch(
                "anki_miner.services.validation_service.subprocess.run", return_value=mock_result
            ):
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

            with patch(
                "anki_miner.services.validation_service.requests.post", return_value=mock_response
            ):
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

            with patch(
                "anki_miner.services.validation_service.requests.post", return_value=mock_response
            ):
                success, message = service._check_deck_exists()

            assert success is False
            assert "not found" in message.lower()

    class TestCheckNoteTypeExists:
        """Tests for _check_note_type_exists method."""

        def test_note_type_found(self, test_config):
            service = ValidationService(test_config)

            mock_response = MagicMock()
            mock_response.json.return_value = {
                "result": ["Basic", test_config.anki_note_type, "Cloze"],
                "error": None,
            }

            with patch(
                "anki_miner.services.validation_service.requests.post", return_value=mock_response
            ):
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

            with patch(
                "anki_miner.services.validation_service.requests.post", return_value=mock_response
            ):
                success, message = service._check_note_type_exists()

            assert success is False
            assert "not found" in message.lower()

    class TestValidateSetup:
        """Tests for validate_setup — mocking at real boundaries (requests.post, subprocess.run)."""

        def test_all_pass(self, test_config):
            """All checks pass when external services respond correctly."""
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

            dispatch = {
                "version": anki_version_resp,
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
                    "anki_miner.services.validation_service.requests.post", side_effect=mock_post
                ),
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
                    "anki_miner.services.validation_service.requests.post",
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
                patch(
                    "anki_miner.services.validation_service.requests.post", side_effect=mock_post
                ),
                patch(
                    "anki_miner.services.validation_service.subprocess.run",
                    side_effect=FileNotFoundError(),
                ),
            ):
                result = service.validate_setup()

            assert result.ffmpeg_ok is False
            assert result.ankiconnect_ok is True
            assert any(i.component == "ffmpeg" for i in result.issues)
