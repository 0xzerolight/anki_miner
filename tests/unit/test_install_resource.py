"""install_resource: one catalogue resource, the body ResourceDownloadWorker.run and --api setup share."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from anki_miner.gui.workers import resource_download_worker as worker_mod
from anki_miner.services.resource_catalog import ResourceSpec

DICT = ResourceSpec(id="test-dict", kind="dict", display_name="Test", url="https://example.test/d.zip", license_note="")


@pytest.fixture
def staged(tmp_path, monkeypatch) -> Path:
    path = tmp_path / "dl" / "x.part"
    path.parent.mkdir()
    path.write_bytes(b"zip")
    monkeypatch.setattr(worker_mod, "download_to_temp", lambda url, **kwargs: path)
    monkeypatch.setattr(worker_mod, "sweep_superseded_dicts", lambda root, **kwargs: ([], []))
    return path


def _install(tmp_path, staged, events=None, **kwargs):
    return worker_mod.install_resource(
        DICT,
        dicts_root=tmp_path / "dicts",
        freqs_root=tmp_path / "freqs",
        pitch_root=tmp_path / "pitch",
        download_dir=staged.parent,
        language="ja",
        reporter=worker_mod.phase_reporter(DICT, (events if events is not None else []).append),
        cancelled=kwargs.pop("cancelled", lambda: False),
        **kwargs,
    )


def test_imports_one_dictionary_into_its_slot(tmp_path, staged, monkeypatch) -> None:
    seen = {}

    def fake_dict(zip_path, dest_root, **kwargs):
        seen.update(kwargs, dest_root=dest_root)
        return SimpleNamespace(dict_id=kwargs["dict_id"], source_name="Test", entry_count=3)

    monkeypatch.setattr(worker_mod, "import_yomitan_zip", fake_dict)
    events: list = []
    result = _install(tmp_path, staged, events)
    assert result is not None and result.ok and result.dict_id == "test-dict" and result.detail == "3 entries"
    assert seen["dest_root"] == tmp_path / "dicts" and seen["overwrite"] is True
    assert [e.phase.value for e in events] == ["installing"]


def test_a_cancel_after_the_download_returns_none_and_removes_it(tmp_path, staged) -> None:
    assert _install(tmp_path, staged, cancelled=lambda: True) is None
    assert not staged.exists()


def test_a_refused_promotion_raises(tmp_path, staged) -> None:
    with pytest.raises(worker_mod.PromotionBlocked):
        _install(tmp_path, staged, promotion_allowed=lambda: False)
    assert not staged.exists()


def test_an_import_failure_is_a_failed_result(tmp_path, staged, monkeypatch) -> None:
    def broken(*args, **kwargs):
        raise RuntimeError("bad zip")

    monkeypatch.setattr(worker_mod, "import_yomitan_zip", broken)
    result = _install(tmp_path, staged)
    assert result is not None and result.ok is False and result.detail == "bad zip"


@pytest.mark.parametrize(
    ("kind", "url", "pin", "slot"),
    [
        ("dict", "https://x/d.zip", False, "s"),
        ("pitch", "https://x/p.txt", False, "s"),
        ("freq", "https://x/f.txt", False, "s"),
        ("freq", "https://x/f.zip", True, "s"),
        ("freq", "https://x/f.zip", False, None),
        ("freq", "https://x/api?download=1", False, None),
    ],
)
def test_pinned_slot(kind, url, pin, slot) -> None:
    spec = ResourceSpec(id="s", kind=kind, display_name="S", url=url, license_note="", pin_slot=pin)
    assert worker_mod.pinned_slot(spec) == slot
