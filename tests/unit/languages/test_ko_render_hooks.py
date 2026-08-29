"""ko render hooks: hanja extraction from the mined form.

Only the hanja hook ships. The planned NIKL vocabulary-grade hook is void: its
data source (the KOGL Type 4 learner-grade list) permits no derivative, so
there is no grade map to read and no ``vocab_grade`` field.

``render`` takes the config keyword-only, matching the landed CardRenderHook
protocol and EpisodeProcessor's ``hook.render(word, config=self.config)`` call.
A positional spelling would TypeError into that loop's except and the field
would silently never reach a card.
"""

from __future__ import annotations

import inspect

from anki_miner.config import AnkiMinerConfig
from anki_miner.languages.ko.render import KO_RENDER_HOOKS, KoHanjaHook
from anki_miner.languages.registry import get_profile
from anki_miner.models.word import TokenizedWord
from anki_miner.services.anki_note_builder import OPTIONAL_FIELD_KEYS

CONFIG = AnkiMinerConfig()


def _word(mined: str) -> TokenizedWord:
    """Phase 5 hands hooks a TokenizedWord; ko mines nouns as their surface."""
    return TokenizedWord(
        surface=mined,
        lemma=mined,
        reading="",
        sentence=mined,
        start_time=0.0,
        end_time=1.0,
        duration=1.0,
    )


def test_hanja_hook_extracts_the_han_run_from_the_mined_form() -> None:
    hook = KoHanjaHook()
    assert hook.field_names() == ("hanja",)
    assert hook.render(_word("漢字語"), config=CONFIG) == {"hanja": "漢字語"}
    assert hook.render(_word("학생"), config=CONFIG) == {"hanja": ""}
    assert hook.render(_word("literature"), config=CONFIG) == {"hanja": ""}


def test_hanja_hook_keeps_only_the_han_characters_of_a_mixed_form() -> None:
    """Mixed script is the case that actually appears in subtitles and prose."""
    assert KoHanjaHook().render(_word("韓國사람"), config=CONFIG) == {"hanja": "韓國"}


def test_hanja_hook_uses_the_shared_ko_script_ranges() -> None:
    """樂 = U+F914 lives in the compatibility block ko sources use for dual
    readings; the ingestion gate accepts it, so the hook must too."""
    assert KoHanjaHook().render(_word("樂"), config=CONFIG) == {"hanja": "樂"}


def test_hanja_hook_survives_a_word_without_a_mined_form() -> None:
    # The hook loop swallows exceptions, so a raising hook emits nothing at all
    # and the field silently disappears. Probe with a default instead.
    assert KoHanjaHook().render(object(), config=CONFIG) == {"hanja": ""}


def test_hanja_hook_takes_the_config_keyword_only() -> None:
    """A positional-config hook TypeErrors into the caller's bare except."""
    for hook in KO_RENDER_HOOKS:
        parameter = inspect.signature(hook.render).parameters["config"]
        assert parameter.kind is inspect.Parameter.KEYWORD_ONLY, type(hook).__name__


def test_profile_declares_the_hook_and_its_field_default() -> None:
    profile = get_profile("ko")
    names = tuple(n for hook in profile.render_hooks for n in hook.field_names())
    assert names == ("hanja",)
    # Mapped-field-is-the-switch convention: default blank, so ko cards are
    # byte-identical until the user maps the field (spec 9.3).
    assert profile.card_field_defaults["hanja"] == ""
    assert profile.scoped_defaults["anki_fields"]["hanja"] == ""


def test_hanja_is_writable_by_the_note_builder() -> None:
    """Mapping the key in KO_CARD_FIELDS is necessary but not sufficient: the
    extra_fields write gate drops any key outside OPTIONAL_FIELD_KEYS."""
    assert "hanja" in OPTIONAL_FIELD_KEYS


def test_no_vocabulary_grade_field_ships() -> None:
    """The NIKL grade list is KOGL Type 4 - no derivative may ship, so the
    grade hook and its field are void, not merely unimplemented."""
    profile = get_profile("ko")
    assert "vocab_grade" not in profile.card_field_defaults
    assert "vocab_grade" not in OPTIONAL_FIELD_KEYS
