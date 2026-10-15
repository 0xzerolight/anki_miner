"""F-10: a word the curator pinned to a dictionary takes that dictionary's entry first."""

from __future__ import annotations

from anki_miner.models import TokenizedWord
from anki_miner.services.definition_service import DefinitionService, preferred_dictionaries


class _Provider:
    """An offline dictionary stub: batch (``lookup_many``) by default, per-word with ``batch=False``."""

    def __init__(self, name, entries, *, batch=True, available=True, raises=False):
        self.name = name
        self._entries = entries
        self._available = available
        self._raises = raises
        if batch:
            self.lookup_many = self._lookup_many

    def is_available(self):
        return self._available

    def load(self):
        return True

    def lookup(self, word):
        if self._raises:
            raise RuntimeError("broken index")
        return self._entries.get(word)

    def _lookup_many(self, pairs, **_kwargs):
        if self._raises:
            raise RuntimeError("broken index")
        return {word: self._entries.get(word) for word, _reading in pairs}


def _word(front: str, pick: str = "") -> TokenizedWord:
    return TokenizedWord(
        surface=front,
        lemma=front,
        reading="",
        sentence=front,
        start_time=0.0,
        end_time=1.0,
        duration=1.0,
        mined_form_override=front,
        dictionary_pick=pick,
    )


def test_a_new_word_has_no_pick():
    assert _word("猫").dictionary_pick == ""


def test_preferred_dictionaries_maps_each_picked_front_once():
    words = [_word("猫", "Jitendex"), _word("犬"), _word("猫", "JMdict")]
    assert preferred_dictionaries(words) == {"猫": "Jitendex"}


def test_the_picked_dictionary_answers_first(test_config):
    first = _Provider("JMdict", {"猫": "<jm cat>", "犬": "<jm dog>"})
    second = _Provider("Jitendex", {"猫": "<jt cat>"})
    service = DefinitionService(test_config, [first, second])
    out = service.get_definitions_batch([("猫", None), ("犬", None)], preferred_dictionaries={"猫": "Jitendex"})
    assert out == ["<jt cat>", "<jm dog>"]


def test_without_picks_the_first_hit_wins_as_before(test_config):
    service = DefinitionService(
        test_config, [_Provider("JMdict", {"猫": "<jm>"}), _Provider("Jitendex", {"猫": "<jt>"})]
    )
    assert service.get_definitions_batch([("猫", None)]) == ["<jm>"]
    assert service.get_definitions_batch([("猫", None)], preferred_dictionaries={}) == ["<jm>"]


def test_a_pick_the_dictionary_cannot_answer_falls_back_to_the_chain(test_config):
    service = DefinitionService(test_config, [_Provider("JMdict", {"猫": "<jm>"}), _Provider("Jitendex", {})])
    assert service.get_definitions_batch([("猫", None)], preferred_dictionaries={"猫": "Jitendex"}) == ["<jm>"]


def test_a_pick_naming_an_absent_or_unloaded_dictionary_is_ignored(test_config):
    unloaded = _Provider("Jitendex", {"猫": "<jt>"}, available=False)
    service = DefinitionService(test_config, [_Provider("JMdict", {"猫": "<jm>"}), unloaded])
    assert service.get_definitions_batch([("猫", None)], preferred_dictionaries={"猫": "Jitendex"}) == ["<jm>"]
    assert service.get_definitions_batch([("猫", None)], preferred_dictionaries={"猫": "Gone"}) == ["<jm>"]


def test_a_broken_picked_dictionary_degrades_to_the_chain(test_config):
    broken = _Provider("Jitendex", {"猫": "<jt>"}, raises=True)
    service = DefinitionService(test_config, [_Provider("JMdict", {"猫": "<jm>"}), broken])
    assert service.get_definitions_batch([("猫", None)], preferred_dictionaries={"猫": "Jitendex"}) == ["<jm>"]


def test_a_per_word_provider_serves_a_pick_too(test_config):
    legacy = _Provider("Legacy", {"猫": "<legacy>"}, batch=False)
    service = DefinitionService(test_config, [_Provider("JMdict", {"猫": "<jm>"}), legacy])
    assert service.get_definitions_batch([("猫", None)], preferred_dictionaries={"猫": "Legacy"}) == ["<legacy>"]
