"""Tests for subtitle exception classes."""

import pytest

from anki_miner.exceptions import AlassNotFoundError as AlassNotFoundErrorPkg
from anki_miner.exceptions import SubtitleRetimeError as SubtitleRetimeErrorPkg
from anki_miner.exceptions.subtitle import AlassNotFoundError, SubtitleRetimeError


class TestAlassNotFoundError:
    def test_subclasses_exception(self) -> None:
        assert issubclass(AlassNotFoundError, Exception)

    def test_importable_from_module(self) -> None:
        # already imported at top; just assert the class is what we expect
        assert AlassNotFoundError.__name__ == "AlassNotFoundError"

    def test_importable_from_package(self) -> None:
        assert AlassNotFoundErrorPkg is AlassNotFoundError

    def test_carries_message(self) -> None:
        err = AlassNotFoundError("alass not found")
        assert str(err) == "alass not found"

    def test_raise_and_catch(self) -> None:
        with pytest.raises(AlassNotFoundError, match="alass not found"):
            raise AlassNotFoundError("alass not found")


class TestSubtitleRetimeError:
    def test_subclasses_exception(self) -> None:
        assert issubclass(SubtitleRetimeError, Exception)

    def test_importable_from_module(self) -> None:
        assert SubtitleRetimeError.__name__ == "SubtitleRetimeError"

    def test_importable_from_package(self) -> None:
        assert SubtitleRetimeErrorPkg is SubtitleRetimeError

    def test_carries_message(self) -> None:
        err = SubtitleRetimeError("retime failed")
        assert str(err) == "retime failed"

    def test_raise_and_catch(self) -> None:
        with pytest.raises(SubtitleRetimeError, match="retime failed"):
            raise SubtitleRetimeError("retime failed")
