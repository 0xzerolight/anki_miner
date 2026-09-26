"""Tests for the shared cooperative-cancellation helper.

Not test_cancel.py: that file belongs to lead-episode-processor.
"""

from __future__ import annotations

import pytest

from anki_miner.exceptions import OperationCancelled, SetupError, raise_if_cancelled


class TestRaiseIfCancelled:
    def test_none_check_is_a_noop(self) -> None:
        raise_if_cancelled(None)

    def test_false_check_is_a_noop(self) -> None:
        raise_if_cancelled(lambda: False)

    def test_true_check_raises_with_default_message(self) -> None:
        with pytest.raises(OperationCancelled, match="Import cancelled") as excinfo:
            raise_if_cancelled(lambda: True)
        assert isinstance(excinfo.value, SetupError)

    def test_true_check_raises_with_custom_message(self) -> None:
        with pytest.raises(OperationCancelled, match="Reading load cancelled"):
            raise_if_cancelled(lambda: True, "Reading load cancelled")
