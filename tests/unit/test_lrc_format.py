"""LRC lyrics read as subtitles (MS-08)."""

from __future__ import annotations

import pysubs2
import pytest

from anki_miner.services.lrc_format import LAST_LINE_MS, MAX_LINE_MS, parse_lrc, register_lrc_format


class TestParseLrc:
    def test_a_line_ends_where_the_next_starts_and_the_last_lasts_five_seconds(self):
        text = "[ti:Canción]\n[ar:Artista]\n[00:01.00]Hola\n[00:04.50]Adiós\n"

        assert parse_lrc(text) == [(1000, 4500, "Hola"), (4500, 9500, "Adiós")]
        assert LAST_LINE_MS == 5000

    def test_a_line_with_several_stamps_repeats(self):
        assert parse_lrc("[00:01.00][00:10.00]Coro\n[00:05.00]Verso\n") == [
            (1000, 5000, "Coro"),
            (5000, 10_000, "Verso"),
            (10_000, 15_000, "Coro"),
        ]

    def test_a_blank_stamp_only_ends_the_line_before_it(self):
        assert parse_lrc("[00:01.00]Uno\n[00:03.00]\n[00:20.00]Dos\n") == [(1000, 3000, "Uno"), (20_000, 25_000, "Dos")]

    def test_no_line_lasts_longer_than_ten_seconds(self):
        assert parse_lrc("[00:01.00]Uno\n[00:30.00]Dos\n") == [(1000, 11_000, "Uno"), (30_000, 35_000, "Dos")]
        assert MAX_LINE_MS == 10_000

    def test_word_stamps_are_removed(self):
        assert parse_lrc("[00:01.00]<00:01.00>Hola <00:01.50>amigo\n") == [(1000, 6000, "Hola amigo")]

    def test_lines_at_one_time_join_line_by_line(self):
        assert parse_lrc("[00:01.00]猫が好き\n[00:01.00]I like cats\n[00:03.00]次\n") == [
            (1000, 3000, "猫が好き\\NI like cats"),
            (3000, 8000, "次"),
        ]

    @pytest.mark.parametrize(
        ("stamp", "start_ms"),
        [
            ("[01:02]", 62_000),
            ("[01:02.5]", 62_500),
            ("[01:02.25]", 62_250),
            ("[01:02.125]", 62_125),
            ("[01:02:50]", 62_500),
            ("[75:00.00]", 4_500_000),
        ],
    )
    def test_stamp_shapes(self, stamp, start_ms):
        assert parse_lrc(f"{stamp}x\n")[0][0] == start_ms

    def test_metadata_and_free_text_are_ignored(self):
        assert parse_lrc("[offset:+500]\n[length: 03:20]\nNot a lyric\n[00:02.00]Sí\n") == [(2000, 7000, "Sí")]

    def test_a_byte_order_mark_is_ignored(self):
        assert parse_lrc("\ufeff[00:01.00]Hola\r\n") == [(1000, 6000, "Hola")]


class TestPysubs2Registration:
    def test_pysubs2_loads_an_lrc_file(self, tmp_path):
        register_lrc_format()
        path = tmp_path / "song.lrc"
        path.write_text("\ufeff[ar:Artista]\n[00:01.00]Hola\n[00:04.00]Adiós\n", encoding="utf-8")

        subs = pysubs2.load(str(path))

        assert subs.format == "lrc"
        assert [(event.start, event.end, event.text) for event in subs] == [(1000, 4000, "Hola"), (4000, 9000, "Adiós")]

    def test_the_extension_names_the_format(self):
        register_lrc_format()

        assert pysubs2.formats.get_format_identifier(".lrc") == "lrc"

    def test_registering_twice_is_harmless(self):
        register_lrc_format()
        register_lrc_format()

        assert pysubs2.formats.get_format_class("lrc").__name__ == "LrcFormat"

    @pytest.mark.parametrize(
        ("content", "expected"),
        [
            ("1\n00:00:01,000 --> 00:00:02,000\n[00:12] hi\n\n", "srt"),
            ("[10][20]hi\n", "mpl2"),
            ("[00:00.000 -> 00:05.000]  hi\n", "whisper_jax"),
        ],
        ids=["srt-with-a-stamp-like-line", "mpl2", "whisper-jax"],
    )
    def test_other_formats_still_detect_as_themselves(self, content, expected):
        register_lrc_format()

        assert pysubs2.formats.autodetect_format(content) == expected

    def test_pysubs2_cannot_write_lrc(self, tmp_path):
        """Why .lrc stays out of Retime, which writes its output in the input's format."""
        register_lrc_format()
        subs = pysubs2.SSAFile()
        subs.append(pysubs2.SSAEvent(start=0, end=1000, text="x"))

        with pytest.raises(NotImplementedError):
            subs.save(str(tmp_path / "out.lrc"))
