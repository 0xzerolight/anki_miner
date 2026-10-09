"""ko render hooks: hanja extraction from the mined form, hangul front for all-hanja words.

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

import pytest

from anki_miner.config import AnkiMinerConfig
from anki_miner.languages.ko.render import KO_RENDER_HOOKS, KoHanjaHook
from anki_miner.languages.ko.script import krdict_headword
from anki_miner.languages.profile import CARD_FRONT_KEY
from anki_miner.languages.registry import get_profile
from anki_miner.models.word import TokenizedWord
from anki_miner.services.anki_note_builder import OPTIONAL_FIELD_KEYS

CONFIG = AnkiMinerConfig()

# What IndexedDictProvider.lookup returns for KRDICT's hanja-keyed rows
# (KO-EN.KRDICT.No.Examples.zip, krdict_ko-en_0924, imported with language="ko").
# 學校 verbatim; the other two keep the headword span and trim the gloss.
KRDICT_HAKGYO = (
    '<div class="yomitan-glossary"><ol data-count="1"><li data-dictionary="KRDICT EN" '
    'data-dictionary-id="krdict-en"><i>(Noun, ⭐⭐⭐, KRDICT EN)</i><ul class="gloss-list" data-count="1">'
    '<li class="gloss-item"><div class="gloss-content"><span class="gloss-sc-span" lang="ko">'
    '<span class="gloss-sc-span" lang="ko" style="font-weight: bold">학교</span>'
    '<span class="gloss-sc-span" lang="ko"> 〔學校〕</span></span><div class="gloss-sc-div" lang="en">'
    '<div class="gloss-sc-div" lang="en"><span class="gloss-sc-span" lang="en">school</span></div>'
    '<div class="gloss-sc-div" lang="en">An institution where teachers teach students in accordance with a '
    "certain purpose, curriculum, or policy, etc.</div></div></div></li></ul></li></ol></div>"
)
KRDICT_NAK = (
    '<div class="gloss-content"><span class="gloss-sc-span" lang="ko">'
    '<span class="gloss-sc-span" lang="ko" style="font-weight: bold">낙</span>'
    '<span class="gloss-sc-span" lang="ko"> 〔樂〕</span></span><div class="gloss-sc-div" lang="en">'
    '<div class="gloss-sc-div" lang="en"><span class="gloss-sc-span" lang="en">joy; pleasure; delight</span>'
    "</div></div></div>"
)
#: An affix row: the headword carries KRDICT's hyphen, so it is no word to put on a front.
KRDICT_MUL_AFFIX = (
    '<i>(Affix, ⭐⭐, KRDICT EN)</i><ul class="gloss-list" data-count="1"><li class="gloss-item">'
    '<div class="gloss-content"><span class="gloss-sc-span" lang="ko">'
    '<span class="gloss-sc-span" lang="ko" style="font-weight: bold">-물</span>'
    '<span class="gloss-sc-span" lang="ko"> 〔物〕</span></span>'
)


def _word(mined: str, definition: str = "") -> TokenizedWord:
    """Phase 5 hands hooks a TokenizedWord; ko mines nouns as their surface.

    ``definition_html`` is what EpisodeProcessor._apply_render_hooks stashes
    on the word before it calls the hooks.
    """
    return TokenizedWord(
        surface=mined,
        lemma=mined,
        reading="",
        sentence=mined,
        start_time=0.0,
        end_time=1.0,
        duration=1.0,
        definition_html=definition,
    )


def test_hanja_hook_extracts_the_han_run_from_the_mined_form() -> None:
    hook = KoHanjaHook()
    assert hook.field_names() == ("hanja",)
    assert hook.render(_word("학생"), config=CONFIG) == {"hanja": ""}
    assert hook.render(_word("literature"), config=CONFIG) == {"hanja": ""}


def test_hanja_hook_keeps_only_the_han_characters_of_a_mixed_form() -> None:
    """Mixed script is the case that actually appears in subtitles and prose."""
    assert KoHanjaHook().render(_word("韓國사람"), config=CONFIG) == {"hanja": "韓國"}


def test_a_mixed_form_keeps_its_own_front_whatever_the_dictionary_says() -> None:
    assert KoHanjaHook().render(_word("韓國사람", KRDICT_HAKGYO), config=CONFIG) == {"hanja": "韓國"}


def test_an_all_hanja_word_gets_the_krdict_hangul_headword_as_its_front() -> None:
    assert KoHanjaHook().render(_word("學校", KRDICT_HAKGYO), config=CONFIG) == {
        "hanja": "學校",
        CARD_FRONT_KEY: "학교",
    }


@pytest.mark.parametrize(
    "definition", ["", "<i>school</i>", KRDICT_MUL_AFFIX], ids=["no-definition", "no-headword", "affix-headword"]
)
def test_an_all_hanja_word_without_a_hangul_headword_writes_nothing(definition: str) -> None:
    """The front stays the Hanja, so a Hanja field would only repeat it."""
    assert KoHanjaHook().render(_word("學校", definition), config=CONFIG) == {}
    assert KoHanjaHook().render(_word("漢字語", definition), config=CONFIG) == {}


def test_hanja_hook_uses_the_shared_ko_script_ranges() -> None:
    """樂 = U+F914 lives in the compatibility block ko sources use for dual
    readings; the ingestion gate accepts it, so the hook must too."""
    assert KoHanjaHook().render(_word("\uf914", KRDICT_NAK), config=CONFIG) == {
        "hanja": "\uf914",
        CARD_FRONT_KEY: "낙",
    }


@pytest.mark.parametrize(
    ("definition", "headword"),
    [
        (KRDICT_HAKGYO, "학교"),
        (KRDICT_NAK, "낙"),
        (KRDICT_MUL_AFFIX, ""),
        ('<span lang="ko" style="font-weight: bold">학교 생활</span>', ""),
        ('<span lang="ko" style="font-weight: bold">學校</span>', ""),
        ('<span lang="ko" style="font-weight: bold"></span>', ""),
        ("<i>school</i>", ""),
        ("", ""),
    ],
    ids=["hakgyo", "nak", "affix", "spaced", "hanja", "empty-span", "no-span", "empty"],
)
def test_krdict_headword_is_the_first_bold_korean_span_when_it_is_all_hangul(definition: str, headword: str) -> None:
    assert krdict_headword(definition) == headword


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


@pytest.mark.parametrize(
    ("mined", "definition"),
    [
        ("學校", KRDICT_HAKGYO),
        ("樂", KRDICT_NAK),
        ("學校", ""),
        ("學校", KRDICT_MUL_AFFIX),
        ("韓國사람", KRDICT_HAKGYO),
        ("학생", KRDICT_HAKGYO),
    ],
    ids=["hakgyo", "nak", "no-definition", "affix-headword", "mixed-script", "hangul"],
)
def test_card_front_is_the_front_render_writes(mined: str, definition: str) -> None:
    """Phase 2's known gate keys on card_front, Anki on render's front: one answer (audit L1-004)."""
    hook = KoHanjaHook()
    rendered = hook.render(_word(mined, definition), config=CONFIG)
    assert hook.card_front(mined, lambda: definition) == rendered.get(CARD_FRONT_KEY, "")


def test_card_front_reads_the_definition_only_for_an_all_hanja_word() -> None:
    """Phase 2 asks about every word; only the ones whose front can move cost a lookup."""

    def no_lookup() -> str:
        raise AssertionError("looked up a word whose front cannot move")

    hook = KoHanjaHook()
    assert hook.card_front("학생", no_lookup) == ""
    assert hook.card_front("韓國사람", no_lookup) == ""
    assert hook.card_front("", no_lookup) == ""
