"""Unit tests for the import-guardrail helpers (plan 4.7/4.8).

Covers the hand-rolled bank structural checks
(:mod:`anki_miner.services.dictionary.schema_validation`) and the nested-index
diagnostic (:mod:`anki_miner.services.dictionary.zip_safety`).
"""

import pytest

from anki_miner.exceptions import SetupError
from anki_miner.services.dictionary.schema_validation import (
    ensure_bank_array,
    is_valid_meta_bank_entry,
    is_valid_term_bank_entry,
)
from anki_miner.services.dictionary.zip_safety import (
    find_redundant_index_dir,
    raise_if_index_nested,
)


class TestEnsureBankArray:
    def test_list_passes_through(self) -> None:
        bank = [["猫", "たべる", "", "", 0, ["x"]]]
        assert ensure_bank_array(bank, "term_bank_1.json") is bank

    def test_empty_list_is_valid(self) -> None:
        assert ensure_bank_array([], "term_bank_1.json") == []

    @pytest.mark.parametrize("bad", [{"a": 1}, "not-a-list", 5, None])
    def test_non_array_raises_naming_the_file(self, bad: object) -> None:
        with pytest.raises(SetupError, match="term_bank_1.json"):
            ensure_bank_array(bad, "term_bank_1.json")


class TestTermBankEntryValidation:
    def test_full_entry_valid(self) -> None:
        assert is_valid_term_bank_entry(["食べる", "たべる", "v1", "v1", 0, ["to eat"], 1, ""])

    def test_minimum_arity_valid(self) -> None:
        assert is_valid_term_bank_entry(["食べる", "", "", "", 0, ["to eat"]])

    @pytest.mark.parametrize(
        "entry",
        [
            ["食べる", "たべる", "v1", "v1", 0],  # arity 5 < 6
            "not-a-list",
            {"term": "食べる"},
            42,
            [None, "", "", "", 0, ["x"]],  # None term
            ["", "", "", "", 0, ["x"]],  # blank term
            ["   ", "", "", "", 0, ["x"]],  # whitespace-only term
        ],
    )
    def test_malformed_entry_invalid(self, entry: object) -> None:
        assert not is_valid_term_bank_entry(entry)


class TestMetaBankEntryValidation:
    def test_triple_valid(self) -> None:
        assert is_valid_meta_bank_entry(["猫", "freq", 5])

    @pytest.mark.parametrize(
        "entry",
        [
            ["猫", "freq"],  # arity 2 < 3
            "nope",
            {"a": 1},
            [None, "freq", 5],
            ["", "freq", 5],
        ],
    )
    def test_malformed_entry_invalid(self, entry: object) -> None:
        assert not is_valid_meta_bank_entry(entry)


class TestFindRedundantIndexDir:
    def test_nested_single_dir(self) -> None:
        names = ["mydict/index.json", "mydict/term_bank_1.json"]
        assert find_redundant_index_dir(names) == "mydict/"

    def test_nested_multi_level(self) -> None:
        assert find_redundant_index_dir(["a/b/index.json"]) == "a/b/"

    def test_backslash_separators_normalized(self) -> None:
        assert find_redundant_index_dir(["mydict\\index.json"]) == "mydict/"

    def test_root_index_returns_none(self) -> None:
        assert find_redundant_index_dir(["index.json", "term_bank_1.json"]) is None

    def test_no_index_returns_none(self) -> None:
        assert find_redundant_index_dir(["term_bank_1.json", "styles.css"]) is None


class TestRaiseIfIndexNested:
    def test_nested_raises_rezip_diagnostic(self) -> None:
        with pytest.raises(SetupError, match="re-zip the folder CONTENTS"):
            raise_if_index_nested(["mydict/index.json"], missing_msg="fallback")

    def test_names_the_redundant_directory(self) -> None:
        with pytest.raises(SetupError, match='nested under "deep/dir/"'):
            raise_if_index_nested(["deep/dir/index.json"], missing_msg="fallback")

    def test_absent_index_raises_fallback(self) -> None:
        with pytest.raises(SetupError, match="fallback"):
            raise_if_index_nested(["term_bank_1.json"], missing_msg="fallback")
