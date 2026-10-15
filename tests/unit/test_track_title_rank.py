"""MS-06 / F-04: one title vocabulary ranks subtitle streams and sidecar names."""

from __future__ import annotations

import pytest

from anki_miner.utils.audio_track_detector import (
    SubtitleStream,
    TrackTitle,
    primary_subtitle_key,
    subtitle_stream_title,
    track_title_rank,
)

JA = frozenset({"jpn", "ja", "japanese", "jp"})


def _stream(sub_index: int, *, lang: str | None = "jpn", title: str | None = None, forced: bool = False):
    return SubtitleStream(
        index=sub_index + 2,
        sub_index=sub_index,
        codec_name="ass",
        language_tag=lang,
        title=title,
        is_text=True,
        is_forced=forced,
    )


@pytest.mark.parametrize(
    ("title", "expected"),
    [
        ("Full Subtitles", TrackTitle.FULL),
        ("Dialogue", TrackTitle.FULL),
        ("English [Full]", TrackTitle.FULL),
        (None, TrackTitle.PLAIN),
        ("", TrackTitle.PLAIN),
        ("English", TrackTitle.PLAIN),
        ("日本語", TrackTitle.PLAIN),
        ("English (SDH)", TrackTitle.ANNOTATED),
        ("CC", TrackTitle.ANNOTATED),
        ("Hearing Impaired", TrackTitle.ANNOTATED),
        ("Signs & Songs", TrackTitle.PARTIAL),
        ("S&S", TrackTitle.PARTIAL),
        ("Songs", TrackTitle.PARTIAL),
        ("Forced", TrackTitle.PARTIAL),
        ("Commentary", TrackTitle.PARTIAL),
        ("Karaoke", TrackTitle.PARTIAL),
        ("OP/ED", TrackTitle.PARTIAL),
        (".en.forced", TrackTitle.PARTIAL),
        (".en.sdh", TrackTitle.ANNOTATED),
        ("_track3_[eng]", TrackTitle.PLAIN),
    ],
)
def test_title_vocabulary(title, expected):
    assert track_title_rank(title) is expected


@pytest.mark.parametrize("title", ["Subtitle", "Designs", "Fullmetal", "Hide"])
def test_a_word_containing_a_keyword_is_not_the_keyword(title):
    assert track_title_rank(title) is TrackTitle.PLAIN


def test_a_forced_disposition_is_partial_whatever_the_title():
    assert subtitle_stream_title(_stream(0, title="Full", forced=True)) is TrackTitle.PARTIAL
    assert subtitle_stream_title(_stream(0, title="Full")) is TrackTitle.FULL


def test_untitled_streams_keep_the_order_used_before_ms06():
    # The old key was (is_forced, not mining-language, sub_index).
    streams = [_stream(0, lang="eng"), _stream(1, forced=True), _stream(2), _stream(3)]
    ranked = sorted(streams, key=lambda s: primary_subtitle_key(s, JA))
    assert [s.sub_index for s in ranked] == [2, 3, 0, 1]


def test_a_signs_track_ranks_after_the_full_track_in_the_mining_language():
    signs = _stream(0, title="Signs & Songs")
    full = _stream(1, title="Full Subtitles")
    assert min([signs, full], key=lambda s: primary_subtitle_key(s, JA)) is full


def test_full_beats_plain_beats_sdh_within_one_language():
    sdh, plain, full = _stream(0, title="SDH"), _stream(1), _stream(2, title="Dialogue")
    assert sorted([sdh, plain, full], key=lambda s: primary_subtitle_key(s, JA)) == [full, plain, sdh]


def test_a_lone_signs_track_is_ranked_not_refused():
    signs = _stream(0, title="Signs")
    assert min([signs], key=lambda s: primary_subtitle_key(s, JA)) is signs


def test_the_key_follows_the_mining_language_it_is_given():
    spanish = frozenset({"spa", "es", "spanish", "esp"})
    english, castilian = _stream(0, lang="eng", title="Full"), _stream(1, lang="spa")
    assert min([english, castilian], key=lambda s: primary_subtitle_key(s, spanish)) is castilian
