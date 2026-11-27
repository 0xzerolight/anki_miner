"""ANKI_MINER_SMOKE=dictimport: the smoke imports through a real worker pool and reports its verdict."""

from __future__ import annotations

import pytest

from anki_miner.gui import app
from anki_miner.services.dictionary.importers import yomitan_importer


def test_the_smoke_imports_every_bank_through_two_workers(capsys: pytest.CaptureFixture[str]) -> None:
    assert app._run_dictimport_bundled_smoke() == 0
    assert "BUNDLED_SMOKE_PASS: dictimport rendered 4 term banks in 2 worker processes" in capsys.readouterr().out


def test_an_import_failure_is_a_fail_line(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    def broken(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("pool did not start")

    monkeypatch.setattr(yomitan_importer, "import_yomitan_zip", broken)
    assert app._run_dictimport_bundled_smoke() == 1
    assert "BUNDLED_SMOKE_FAIL: dictimport RuntimeError: pool did not start" in capsys.readouterr().err
