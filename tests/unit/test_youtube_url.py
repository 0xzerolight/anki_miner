"""Tests for anki_miner.utils.youtube_url — YouTube URL classification util."""

from anki_miner.utils.youtube_url import YouTubeUrlInfo, classify_youtube_url

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _video(video_id: str) -> YouTubeUrlInfo:
    return YouTubeUrlInfo(kind="video", video_id=video_id, playlist_id=None)


def _playlist(playlist_id: str) -> YouTubeUrlInfo:
    return YouTubeUrlInfo(kind="playlist", video_id=None, playlist_id=playlist_id)


def _video_in_playlist(video_id: str, playlist_id: str) -> YouTubeUrlInfo:
    return YouTubeUrlInfo(kind="video_in_playlist", video_id=video_id, playlist_id=playlist_id)


_UNKNOWN = YouTubeUrlInfo(kind="unknown", video_id=None, playlist_id=None)

# A canonical 11-char video ID and a canonical 34-char playlist ID used across tests.
VID = "dQw4w9WgXcQ"
PL = "PLbpi6ZahtOH6Ar_3GPy3MkEfz1LkCiX09"


# ---------------------------------------------------------------------------
# Plain watch URL  (/watch?v=<id>)
# ---------------------------------------------------------------------------


class TestPlainWatchUrl:
    def test_https_watch(self):
        assert classify_youtube_url(f"https://www.youtube.com/watch?v={VID}") == _video(VID)

    def test_http_watch(self):
        assert classify_youtube_url(f"http://www.youtube.com/watch?v={VID}") == _video(VID)

    def test_no_www_watch(self):
        assert classify_youtube_url(f"https://youtube.com/watch?v={VID}") == _video(VID)

    def test_scheme_less_www_watch(self):
        assert classify_youtube_url(f"www.youtube.com/watch?v={VID}") == _video(VID)

    def test_scheme_less_no_www_watch(self):
        assert classify_youtube_url(f"youtube.com/watch?v={VID}") == _video(VID)

    def test_m_youtube_watch(self):
        assert classify_youtube_url(f"https://m.youtube.com/watch?v={VID}") == _video(VID)

    def test_music_youtube_watch(self):
        assert classify_youtube_url(f"https://music.youtube.com/watch?v={VID}") == _video(VID)


# ---------------------------------------------------------------------------
# Watch URL with list param  (/watch?v=<id>&list=<PL>)
# ---------------------------------------------------------------------------


class TestWatchWithList:
    def test_watch_with_playlist(self):
        assert classify_youtube_url(f"https://www.youtube.com/watch?v={VID}&list={PL}") == _video_in_playlist(VID, PL)

    def test_watch_list_order_reversed(self):
        assert classify_youtube_url(f"https://www.youtube.com/watch?list={PL}&v={VID}") == _video_in_playlist(VID, PL)

    def test_watch_with_extra_params(self):
        assert classify_youtube_url(
            f"https://www.youtube.com/watch?v={VID}&list={PL}&index=3&t=42"
        ) == _video_in_playlist(VID, PL)


# ---------------------------------------------------------------------------
# Pure /playlist?list= URL
# ---------------------------------------------------------------------------


class TestPurePlaylistUrl:
    def test_playlist_path(self):
        assert classify_youtube_url(f"https://www.youtube.com/playlist?list={PL}") == _playlist(PL)

    def test_playlist_path_no_www(self):
        assert classify_youtube_url(f"https://youtube.com/playlist?list={PL}") == _playlist(PL)

    def test_playlist_scheme_less(self):
        assert classify_youtube_url(f"youtube.com/playlist?list={PL}") == _playlist(PL)


# ---------------------------------------------------------------------------
# youtu.be/<id>
# ---------------------------------------------------------------------------


class TestYoutuBe:
    def test_youtu_be_plain(self):
        assert classify_youtube_url(f"https://youtu.be/{VID}") == _video(VID)

    def test_youtu_be_with_list(self):
        assert classify_youtube_url(f"https://youtu.be/{VID}?list={PL}") == _video_in_playlist(VID, PL)

    def test_youtu_be_scheme_less(self):
        assert classify_youtube_url(f"youtu.be/{VID}") == _video(VID)

    def test_youtu_be_http(self):
        assert classify_youtube_url(f"http://youtu.be/{VID}") == _video(VID)


# ---------------------------------------------------------------------------
# /shorts/<id>
# ---------------------------------------------------------------------------


class TestShortsUrl:
    def test_shorts_plain(self):
        assert classify_youtube_url(f"https://www.youtube.com/shorts/{VID}") == _video(VID)

    def test_shorts_with_list(self):
        assert classify_youtube_url(f"https://www.youtube.com/shorts/{VID}?list={PL}") == _video_in_playlist(VID, PL)


# ---------------------------------------------------------------------------
# /live/<id>
# ---------------------------------------------------------------------------


class TestLiveUrl:
    def test_live_plain(self):
        assert classify_youtube_url(f"https://www.youtube.com/live/{VID}") == _video(VID)

    def test_live_with_list(self):
        assert classify_youtube_url(f"https://www.youtube.com/live/{VID}?list={PL}") == _video_in_playlist(VID, PL)


# ---------------------------------------------------------------------------
# YouTube Mix (list=RD…) — should classify as "video" when a video id present
# ---------------------------------------------------------------------------


class TestMixList:
    def test_mix_list_ignored_when_video_present(self):
        """list=RD… auto-generated mixes are unbounded; ignore list, treat as video."""
        result = classify_youtube_url(f"https://www.youtube.com/watch?v={VID}&list=RD{VID}")
        assert result == _video(VID)
        assert result.playlist_id is None

    def test_mix_list_no_video_unknown(self):
        """A mix-only URL with no video id is unknown."""
        result = classify_youtube_url("https://www.youtube.com/watch?list=RDQMUoVIlO5WY2oYq9qSvqL5OThGY")
        assert result == _UNKNOWN

    def test_mix_short_prefix_only(self):
        """list=RD (just the two-char prefix, nothing after) is still a mix → unknown."""
        result = classify_youtube_url(f"https://www.youtube.com/watch?v={VID}&list=RD")
        assert result == _video(VID)

    def test_youtu_be_mix_ignored(self):
        result = classify_youtube_url(f"https://youtu.be/{VID}?list=RDsomethinglong")
        assert result == _video(VID)


# ---------------------------------------------------------------------------
# Malformed / invalid video ID lengths
# ---------------------------------------------------------------------------


class TestMalformedVideoId:
    def test_video_id_too_short(self):
        """10-char id is not a valid YouTube video id."""
        result = classify_youtube_url("https://www.youtube.com/watch?v=dQw4w9WgXc")
        assert result == _UNKNOWN

    def test_video_id_too_long(self):
        """12-char id is not a valid YouTube video id."""
        result = classify_youtube_url("https://www.youtube.com/watch?v=dQw4w9WgXcQQ")
        assert result == _UNKNOWN

    def test_video_id_invalid_char(self):
        """ID containing a character outside [A-Za-z0-9_-] is invalid."""
        result = classify_youtube_url("https://www.youtube.com/watch?v=dQw4w9WgX!!")
        assert result == _UNKNOWN

    def test_empty_video_id_param(self):
        result = classify_youtube_url("https://www.youtube.com/watch?v=")
        assert result == _UNKNOWN


# ---------------------------------------------------------------------------
# Playlist id too short → ignore list param
# ---------------------------------------------------------------------------


class TestShortPlaylistId:
    def test_playlist_id_too_short_with_video(self):
        """list param under 12 chars is not a valid playlist id; degrade to video."""
        short_pl = "PLABCDE"  # 7 chars
        result = classify_youtube_url(f"https://www.youtube.com/watch?v={VID}&list={short_pl}")
        assert result == _video(VID)

    def test_playlist_id_too_short_no_video(self):
        """Pure playlist URL with too-short id → unknown."""
        short_pl = "PLABCDE"
        result = classify_youtube_url(f"https://www.youtube.com/playlist?list={short_pl}")
        assert result == _UNKNOWN

    def test_playlist_id_invalid_char(self):
        """list param with invalid chars → ignore."""
        result = classify_youtube_url(f"https://www.youtube.com/watch?v={VID}&list=PL!nvalid")
        assert result == _video(VID)


# ---------------------------------------------------------------------------
# Non-YouTube URL → unknown
# ---------------------------------------------------------------------------


class TestNonYoutubeUrl:
    def test_vimeo(self):
        assert classify_youtube_url("https://vimeo.com/123456789") == _UNKNOWN

    def test_random_url(self):
        assert classify_youtube_url("https://example.com/watch?v=dQw4w9WgXcQ") == _UNKNOWN

    def test_just_domain_lookalike(self):
        assert classify_youtube_url("https://notyoutube.com/watch?v=dQw4w9WgXcQ") == _UNKNOWN


# ---------------------------------------------------------------------------
# Empty / garbage strings
# ---------------------------------------------------------------------------


class TestEmptyAndGarbage:
    def test_empty_string(self):
        assert classify_youtube_url("") == _UNKNOWN

    def test_whitespace_only(self):
        assert classify_youtube_url("   ") == _UNKNOWN

    def test_garbage(self):
        assert classify_youtube_url("not-a-url-at-all") == _UNKNOWN

    def test_none_like_string(self):
        assert classify_youtube_url("None") == _UNKNOWN
