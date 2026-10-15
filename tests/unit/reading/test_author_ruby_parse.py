"""SK-31: an EPUB's own furigana sets the card reading, through the language profile.

Runs the real Japanese parser (fugashi + unidic-lite, as
``test_subtitle_parser_text_units.py`` does). 今日 parses as きょう; an author
who prints こんにち over it means the literary reading.
"""

from __future__ import annotations

import pytest

from anki_miner.languages.registry import get_profile


@pytest.mark.parametrize(("code", "takes"), [("ja", True), ("es", False), ("zh", False), ("ko", False)])
def test_only_a_furigana_profile_takes_author_ruby(code: str, takes: bool) -> None:
    assert get_profile(code).takes_author_ruby is takes
