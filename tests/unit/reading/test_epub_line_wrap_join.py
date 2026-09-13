"""S14: a pretty-printed line wrap is a word boundary in a space-delimited language."""

from __future__ import annotations

from anki_miner.languages.ko.script import KO_SENTENCE_RULES
from anki_miner.languages.registry import get_profile
from anki_miner.services.reading.epub_source import _parse_content, _walk_body

XHTML = (
    b'<?xml version="1.0" encoding="utf-8"?>'
    b'<html xmlns="http://www.w3.org/1999/xhtml"><body>'
    b"<p>op zoek\n      naar het huis</p>"
    b"<p>\xe6\x97\xa5\xe6\x9c\xac\xe8\xaa\x9e\xe3\x81\xae\n   \xe6\x96\x87\xe7\xab\xa0</p>"
    b"</body></html>"
)


def _paragraphs(line_join: str) -> list[str]:
    body, _is_cover = _parse_content(XHTML)
    paragraphs, _gaiji = _walk_body(body, line_join=line_join)
    return paragraphs


def test_default_join_is_unchanged():
    assert _paragraphs("") == ["op zoeknaar het huis", "日本語の文章"]


def test_space_aware_join_keeps_the_word_boundary():
    assert _paragraphs(" ")[0] == "op zoek naar het huis"


def test_the_join_follows_the_language_rules():
    from anki_miner.services.reading.epub_source import _line_join

    assert _line_join(KO_SENTENCE_RULES) == " "
    assert _line_join(get_profile("zh").sentence_rules) == ""
    assert _line_join(None) == ""
