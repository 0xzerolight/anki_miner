"""The dialog filters are derived from the pairing extension sets, not restated."""

import pysubs2
import pytest

from anki_miner.gui.constants import RETIME_SUBTITLE_EXTENSIONS, RETIME_SUBTITLE_FILE_FILTER, SUBTITLE_FILE_FILTER
from anki_miner.utils.file_pairing import DEFAULT_SUBTITLE_PRIORITY


def test_subtitle_filter_offers_every_pairable_extension():
    for extension in DEFAULT_SUBTITLE_PRIORITY:
        assert f"*{extension}" in SUBTITLE_FILE_FILTER


def test_subtitle_filter_keeps_the_all_files_escape_hatch():
    assert SUBTITLE_FILE_FILTER.endswith(";;All Files (*)")
    assert RETIME_SUBTITLE_FILE_FILTER.endswith(";;All Files (*)")


def test_the_mining_formats_include_sami():
    assert ".smi" in DEFAULT_SUBTITLE_PRIORITY


def test_retime_takes_every_mining_format_but_sami():
    """Retime writes its output in the input's format, and pysubs2 cannot write SAMI."""
    assert sorted(RETIME_SUBTITLE_EXTENSIONS) == [".ass", ".srt", ".ssa", ".vtt"]
    assert "*.smi" not in RETIME_SUBTITLE_FILE_FILTER
    for extension in RETIME_SUBTITLE_EXTENSIONS:
        assert f"*{extension}" in RETIME_SUBTITLE_FILE_FILTER


def test_pysubs2_still_cannot_write_sami(tmp_path):
    """The reason .smi stays out of Retime; if this starts passing a write, Retime can take it."""
    subs = pysubs2.SSAFile()
    subs.append(pysubs2.SSAEvent(start=0, end=1000, text="가"))
    with pytest.raises(NotImplementedError):
        subs.save(str(tmp_path / "out.smi"))
