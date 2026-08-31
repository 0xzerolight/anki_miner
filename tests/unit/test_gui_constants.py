"""The dialog filters are derived from the pairing extension sets, not restated."""

from anki_miner.gui.constants import SUBTITLE_FILE_FILTER
from anki_miner.utils.file_pairing import DEFAULT_SUBTITLE_PRIORITY


def test_subtitle_filter_offers_every_pairable_extension():
    for extension in DEFAULT_SUBTITLE_PRIORITY:
        assert f"*{extension}" in SUBTITLE_FILE_FILTER


def test_subtitle_filter_keeps_the_all_files_escape_hatch():
    assert SUBTITLE_FILE_FILTER.endswith(";;All Files (*)")
