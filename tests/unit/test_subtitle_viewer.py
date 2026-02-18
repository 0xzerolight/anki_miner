"""Tests for subtitle_viewer module."""

from anki_miner.gui.widgets.subtitle_viewer import SubtitleViewer


class TestFormatTime:
    """Tests for SubtitleViewer._format_time static method."""

    def test_zero_ms(self):
        """Should format 0ms as 00:00."""
        assert SubtitleViewer._format_time(0) == "00:00"

    def test_one_second(self):
        """Should format 1000ms as 00:01."""
        assert SubtitleViewer._format_time(1000) == "00:01"

    def test_one_minute(self):
        """Should format 60000ms as 01:00."""
        assert SubtitleViewer._format_time(60000) == "01:00"

    def test_mixed_time(self):
        """Should format 90500ms as 01:30."""
        assert SubtitleViewer._format_time(90500) == "01:30"

    def test_large_time(self):
        """Should format large times correctly."""
        # 25 minutes 13 seconds = 1513000 ms
        assert SubtitleViewer._format_time(1513000) == "25:13"

    def test_negative_ms(self):
        """Should treat negative values as 00:00."""
        assert SubtitleViewer._format_time(-1000) == "00:00"

    def test_sub_second(self):
        """Should truncate sub-second values."""
        assert SubtitleViewer._format_time(999) == "00:00"

    def test_over_one_hour(self):
        """Should handle times over 60 minutes."""
        # 75 minutes = 4500000 ms
        assert SubtitleViewer._format_time(4500000) == "75:00"
