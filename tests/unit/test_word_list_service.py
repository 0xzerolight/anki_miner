"""Tests for WordListService."""

import ast
from pathlib import Path

import pytest

import anki_miner
from anki_miner.exceptions import SetupError
from anki_miner.languages.registry import get_profile
from anki_miner.services.word_list_service import WordListService
from anki_miner.utils.subtitle_encoding import script_check_kwarg

REPO_ROOT = Path(anki_miner.__file__).resolve().parent.parent

#: Both sites that hand a user's blacklist/whitelist to the service.
EXPECTED_SITES = {
    "anki_miner/gui/utils/service_factory.py",
    "anki_miner/gui/workers/deck_filter_worker.py",
}


def _ladder(language: str) -> dict:
    """The kwargs a construction site passes for *language*'s profile."""
    from dataclasses import replace

    from anki_miner.config import AnkiMinerConfig
    from anki_miner.gui.utils.service_factory import import_decode_ladder

    encodings = import_decode_ladder(replace(AnkiMinerConfig(), language=language))
    return {
        "encodings": encodings,
        **script_check_kwarg(encodings, get_profile(language).script),
    }


class TestLoad:
    """Tests for load method."""

    def test_loads_blacklist(self, tmp_path):
        """Should read words from a blacklist file."""
        bl = tmp_path / "blacklist.txt"
        bl.write_text("食べる\n飲む\n走る\n", encoding="utf-8")

        service = WordListService(blacklist_path=bl)
        service.load()

        assert service.is_blacklisted("食べる") is True
        assert service.is_blacklisted("飲む") is True
        assert service.is_blacklisted("走る") is True
        assert service.is_blacklisted("歩く") is False

    def test_loads_whitelist(self, tmp_path):
        """Should read words from a whitelist file."""
        wl = tmp_path / "whitelist.txt"
        wl.write_text("新しい\n古い\n", encoding="utf-8")

        service = WordListService(whitelist_path=wl)
        service.load()

        assert service.is_whitelisted("新しい") is True
        assert service.is_whitelisted("古い") is True
        assert service.is_whitelisted("赤い") is False

    def test_ignores_blank_lines_and_comments(self, tmp_path):
        """Should skip blank lines and lines starting with #."""
        bl = tmp_path / "blacklist.txt"
        bl.write_text(
            "# This is a comment\n食べる\n\n# Another comment\n飲む\n  \n",
            encoding="utf-8",
        )

        service = WordListService(blacklist_path=bl)
        service.load()

        assert service.is_blacklisted("食べる") is True
        assert service.is_blacklisted("飲む") is True
        assert service.is_blacklisted("# This is a comment") is False
        assert service.is_blacklisted("") is False

    def test_missing_file_raises_setup_error(self, tmp_path):
        """Should raise SetupError for nonexistent file."""
        service = WordListService(blacklist_path=tmp_path / "nonexistent.txt")

        with pytest.raises(SetupError, match="Your word list file is missing."):
            service.load()

    def test_empty_file(self, tmp_path):
        """Should treat all lookups as not-blacklisted for an empty file."""
        bl = tmp_path / "empty.txt"
        bl.write_text("", encoding="utf-8")

        service = WordListService(blacklist_path=bl)
        service.load()

        assert service.is_blacklisted("anything") is False

    def test_none_paths_skip_loading(self):
        """Should succeed with no files when paths are None."""
        service = WordListService(blacklist_path=None, whitelist_path=None)
        service.load()

        assert service.is_blacklisted("anything") is False
        assert service.is_whitelisted("anything") is False
        assert service.is_available() is True


class TestIsAvailable:
    """Tests for is_available method."""

    def test_false_before_load(self, tmp_path):
        """Should return False before load is called."""
        service = WordListService(blacklist_path=tmp_path / "bl.txt")
        assert service.is_available() is False

    def test_true_after_load(self, tmp_path):
        """Should return True after successful load."""
        bl = tmp_path / "bl.txt"
        bl.write_text("食べる\n", encoding="utf-8")

        service = WordListService(blacklist_path=bl)
        service.load()

        assert service.is_available() is True


class TestBlacklist:
    """Tests for blacklist lookups."""

    def test_is_blacklisted(self, tmp_path):
        """Should return True for blacklisted words."""
        bl = tmp_path / "bl.txt"
        bl.write_text("食べる\n飲む\n", encoding="utf-8")

        service = WordListService(blacklist_path=bl)
        service.load()

        assert service.is_blacklisted("食べる") is True
        assert service.is_blacklisted("走る") is False


class TestWhitelist:
    """Tests for whitelist lookups."""

    def test_is_whitelisted(self, tmp_path):
        """Should return True for whitelisted words."""
        wl = tmp_path / "wl.txt"
        wl.write_text("新しい\n古い\n", encoding="utf-8")

        service = WordListService(whitelist_path=wl)
        service.load()

        assert service.is_whitelisted("新しい") is True
        assert service.is_whitelisted("食べる") is False


class TestBothLists:
    """Tests with both blacklist and whitelist loaded."""

    def test_independent_lists(self, tmp_path):
        """Blacklist and whitelist should be independent sets."""
        bl = tmp_path / "bl.txt"
        bl.write_text("食べる\n", encoding="utf-8")
        wl = tmp_path / "wl.txt"
        wl.write_text("飲む\n", encoding="utf-8")

        service = WordListService(blacklist_path=bl, whitelist_path=wl)
        service.load()

        assert service.is_blacklisted("食べる") is True
        assert service.is_whitelisted("食べる") is False
        assert service.is_blacklisted("飲む") is False
        assert service.is_whitelisted("飲む") is True


class TestReadWordFileException:
    """Tests for error handling in _read_word_file."""

    def test_raises_setup_error_on_read_failure(self, tmp_path):
        """Should raise SetupError when file reading fails after existence check."""
        from unittest.mock import patch

        bl = tmp_path / "blacklist.txt"
        bl.write_text("食べる\n", encoding="utf-8")
        service = WordListService(blacklist_path=bl)

        with (
            patch.object(
                type(bl),
                "open",
                side_effect=UnicodeDecodeError("utf-8", b"", 0, 1, "invalid"),
            ),
            pytest.raises(SetupError, match="Could not read your word list file."),
        ):
            service.load()

    def test_memory_error_escapes_unchanged(self, tmp_path):
        """Allocation exhaustion must stay fatal instead of disabling the list."""
        from unittest.mock import patch

        bl = tmp_path / "blacklist.txt"
        bl.write_text("食べる\n", encoding="utf-8")
        service = WordListService(blacklist_path=bl)

        with (
            patch.object(type(bl), "open", side_effect=MemoryError("allocation failed")),
            pytest.raises(MemoryError, match="allocation failed"),
        ):
            service.load()


class TestEncodings:
    """The file decodes through the mining language's ladder, not bare UTF-8."""

    def test_bom_does_not_poison_the_first_entry(self, tmp_path):
        """str.strip() leaves U+FEFF, so entry 1 used to be an unmatchable word."""
        bl = tmp_path / "bl.txt"
        bl.write_text("的\n了\n", encoding="utf-8-sig")

        service = WordListService(blacklist_path=bl)
        service.load()

        assert service.is_blacklisted("的") is True
        assert service.whitelist_entries() == frozenset()

    def test_chinese_ladder_reads_a_gb18030_list(self, tmp_path):
        """Windows Notepad on a mainland machine writes GBK by default."""
        bl = tmp_path / "bl.txt"
        bl.write_bytes("的\n了\n是\n".encode("gb18030"))

        service = WordListService(blacklist_path=bl, **_ladder("zh"))
        service.load()

        assert service.is_blacklisted("的") is True
        assert service.is_blacklisted("是") is True

    def test_chinese_ladder_reads_a_big5_list(self, tmp_path):
        """gb18030 decodes Big5 bytes into PUA mojibake without raising.

        A real list, not three words: ``prefers_big5`` judges the PUA share of
        the whole file, and a handful of characters is below the noise floor it
        documents as unjudgeable.
        """
        words = [
            *("蘋果", "學習", "電腦", "網路", "開會", "時間", "國家", "經濟"),
            *("發展", "關係", "應該", "這樣", "為什麼", "沒關係", "謝謝"),
            *("對不起", "醫生", "圖書館", "飛機", "車站", "臺灣", "銀行"),
        ]
        wl = tmp_path / "wl.txt"
        wl.write_bytes(("\n".join(words) + "\n").encode("big5"))

        service = WordListService(whitelist_path=wl, **_ladder("zh"))
        service.load()

        assert service.whitelist_entries() == set(words)

    def test_japanese_gets_no_ladder_and_a_cp932_list_still_fails(self, tmp_path):
        """ja is carved out of every first-success ladder: utf-8 (+BOM) or nothing."""
        bl = tmp_path / "bl.txt"
        bl.write_bytes("食べる\n飲む\n".encode("cp932"))

        assert _ladder("ja")["encodings"] is None
        service = WordListService(blacklist_path=bl, **_ladder("ja"))

        with pytest.raises(SetupError, match="Could not read your word list file."):
            service.load()

    def test_a_japanese_euc_jp_list_raises_instead_of_loading_mojibake(self, tmp_path):
        """EUC-JP kana decode without error as cp932, so a ladder would load junk silently."""
        words = ("あさ", "かさ", "けさ", "さけ", "たけ", "ちかい", "おおきい", "きせつ", "たいせつ", "そだち")
        bl = tmp_path / "bl.txt"
        bl.write_bytes(("\n".join(words) + "\n").encode("euc_jp"))

        service = WordListService(blacklist_path=bl, **_ladder("ja"))

        with pytest.raises(SetupError, match="Could not read your word list file."):
            service.load()

    def test_plain_utf8_is_unchanged_by_a_ladder(self, tmp_path):
        bl = tmp_path / "bl.txt"
        bl.write_text("食べる\n# comment\n\n飲む\n", encoding="utf-8")

        with_ladder = WordListService(blacklist_path=bl, **_ladder("ja"))
        with_ladder.load()
        without = WordListService(blacklist_path=bl)
        without.load()

        assert with_ladder._blacklist == without._blacklist == {"食べる", "飲む"}

    def test_a_mis_picked_huge_file_is_refused_before_it_is_read(self, tmp_path, monkeypatch):
        """The picker's "All Files (*)" filter lets a video through; stat it, don't decode it."""
        from anki_miner.services import word_list_service as wls

        bl = tmp_path / "bl.txt"
        bl.write_text("的\n了\n", encoding="utf-8")
        monkeypatch.setattr(wls, "_MAX_IMPORT_BYTES", 2)
        monkeypatch.setattr(Path, "open", lambda *a, **k: pytest.fail("the file was read"))

        with pytest.raises(SetupError, match="Could not read your word list file."):
            WordListService(blacklist_path=bl).load()

    def test_the_cap_is_the_one_the_known_words_importer_uses(self):
        """One "a user mis-picked a huge file" bound for both import pickers."""
        from anki_miner.services import known_words_import, word_list_service

        assert word_list_service._MAX_IMPORT_BYTES is known_words_import._MAX_IMPORT_BYTES

    def test_an_undecodable_list_still_raises_setup_error(self, tmp_path):
        """An exhausted ladder is a read failure like any other."""
        bl = tmp_path / "bl.txt"
        bl.write_bytes(b"\xff\xfe\x00\x01 not text")

        service = WordListService(blacklist_path=bl, encodings=("utf-8",))

        with pytest.raises(SetupError, match="Could not read your word list file."):
            service.load()


class TestConstructionSites:
    """Both sites hand the service the profile's ladder (ZH-047)."""

    @staticmethod
    def _constructions():
        for path in (REPO_ROOT / "anki_miner").rglob("*.py"):
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
                if isinstance(node, ast.Call) and getattr(node.func, "id", "") == "WordListService":
                    yield path.relative_to(REPO_ROOT).as_posix(), node

    def test_the_sites_are_the_known_two(self):
        assert {path for path, _node in self._constructions()} == EXPECTED_SITES

    def test_every_construction_passes_the_ladder(self):
        missing = [
            f"{path}:{node.lineno}"
            for path, node in self._constructions()
            if not any(keyword.arg == "encodings" for keyword in node.keywords)
        ]
        assert missing == []


class TestWhitelistEntries:
    """Tests for whitelist_entries (the run-end coverage report's source)."""

    def test_exposes_every_loaded_entry(self, tmp_path):
        """Should return each entry, comments and blanks skipped."""
        wl = tmp_path / "whitelist.txt"
        wl.write_text("新しい\n# comment\n古い\n\n", encoding="utf-8")
        service = WordListService(whitelist_path=wl)
        service.load()

        assert service.whitelist_entries() == {"新しい", "古い"}

    def test_is_empty_without_a_whitelist(self):
        """Should be empty when no whitelist file was configured."""
        service = WordListService()
        service.load()

        assert service.whitelist_entries() == frozenset()
