"""TEST-ONLY space-delimited ``LanguageProfile``: the profile-boundary proof.

Nothing here ships. ``eu`` is not in ``config.config._LANGUAGE_CODES``, not in
``languages.AVAILABLE_LANGUAGES``, has no package under ``anki_miner/languages/``
and never enters the registry outside a test that registers it by hand. It
exists to answer one question with running code instead of prose: *what would a
fourth language, whose script and morphology share nothing with Japanese, have
to touch outside its own profile?*

Every field below is CONSTRUCTED, never ``dataclasses.replace``d off the ja
profile. Copying ja and overriding three fields proves nothing — it inherits ja
answers for the twenty fields it does not name. Building all of them is the
proof: if a field could only be filled with a Japanese value, the boundary
leaks, and the construction would not compile past it.

**What the stub demonstrates.** On the mining path, every Japanese assumption is
one of three things:

* **injectable** — the tokenizer (``tagger_provider._TAGGERS``), the card-front
  policy (``MinedFormPolicy``), the ingestion script gate
  (``ScriptSupport.contains_target_script``, threaded to
  ``TokenInclusionRule.script_gate``), the sentence character policy
  (``SentenceRules`` into ``split_sentences``), the dictionary key folding
  (``DictKeyFolding``), the subtitle import encodings, the audio stem prefixes.
* **isinstance-gated** — ``morphology``'s ja-only merge passes gate on
  ``SyntheticToken``, which ``LanguageToken`` deliberately is not, so a duck
  token never reaches them.
* **optional** — ``reading``, ``sentence_annotator`` and ``unavailable_reason``
  are ``None``-able; ``render_hooks``/``extra_card_fields``/``catalog`` are
  empty-able. A language with no reading layer, no furigana analogue, no
  bundled resources and no availability probe is a valid profile.

**The deliberate central gates.** Two, and they are policy, not leakage — a
language must be *admitted*, not merely constructible:

1. ``anki_miner.config.config._LANGUAGE_CODES`` — ``AnkiMinerConfig.__post_init__``
   folds any unlisted code to ``"ja"``, so an unadmitted code cannot survive the
   ``dataclasses.replace`` at the end of ``switch_language``.
2. ``anki_miner.languages.AVAILABLE_LANGUAGES`` — the registry's ``_discover``
   loop only registers codes listed there that also have a package on disk.

Plus three contract-governance sets in ``test_language_contract.py``, which are
closed on purpose (a typo'd capability is a silently-off feature): ``PROBE``,
``CAPABILITY_VOCABULARY`` and ``EXTRA_HOOK_FIELDS``. They gate the *test matrix*,
not the runtime.

**One residual ja-ism, pinned rather than papered over**: ``extract_lemma``
strips a hyphen tail whose text contains an ASCII letter (unidic's
``スクランブル-scramble`` disambiguator). For a Latin-script language that also
truncates a genuine hyphenated compound. It is not on the injectable list above;
``test_eu_boundary_stub.py`` pins the behaviour so the gap is recorded, not
assumed away.
"""

from __future__ import annotations

import string
import unicodedata
from typing import TYPE_CHECKING, Any

from anki_miner.config.config import AudioSourceEntry
from anki_miner.languages.profile import (
    AudioDefaults,
    CaptionLangs,
    CardFieldSpec,
    ContentTextStyle,
    LanguageProfile,
    PosDefaults,
    ScriptFilterOption,
    SentenceRules,
)
from anki_miner.languages.switching import blank_scoped_defaults
from anki_miner.languages.token import LanguageToken

if TYPE_CHECKING:  # annotation-only, exactly like ko/render.py
    from anki_miner.config.config import AnkiMinerConfig

__all__ = [
    "EU_CODE",
    "EuDictKeys",
    "EuLookupStrategy",
    "EuMinedForm",
    "EuScriptSupport",
    "EuStubHook",
    "WhitespaceTagger",
    "build_profile",
    "create_parser",
]

#: Not a real ISO code for anything this project mines; deliberately short and
#: obviously synthetic so a grep for it finds only the boundary proof.
EU_CODE = "eu"

#: Leading/trailing characters a lemma never carries. ``str.strip`` with this
#: set turns "Dr." into "dr" and drops a bare "," to "" — which
#: ``TokenInclusionRule.content_gate_ok`` then rejects for having no lemma, so
#: punctuation leaves the mining stream without a punctuation rule anywhere.
_PUNCT = string.punctuation


def fold_lemma(token: str) -> str:
    """Lower-cased *token* with leading/trailing punctuation removed."""
    return token.strip(_PUNCT).lower()


class WhitespaceTagger:
    """Tokenizer for a space-delimited language: ``str.split`` and nothing else.

    Emits ``LanguageToken``s whose ``surface`` is the VERBATIM whitespace-
    separated slice, punctuation included, so ``morphology.iter_token_spans``
    (a cursor + ``str.find`` over the line) can still locate every token. The
    lemma carries the folding instead.

    ``pos1`` is a single invented class, ``"WORD"``: it is what the profile's
    ``allowed_pos`` admits, and it collides with none of the Japanese POS names
    ``content_gate_ok`` rejects outright (助詞/助動詞/記号/補助記号/感動詞/フィラー).
    ``kana`` is ``""`` per the ``LanguageToken`` contract.
    """

    def __call__(self, text: str) -> list[LanguageToken]:
        return [
            LanguageToken(surface=token, pos1="WORD", pos2="", lemma=fold_lemma(token), kana="")
            for token in text.split()
        ]


def create_parser(config: Any, **kwargs: Any) -> Any:
    """Build the stub ``SubtitleParser`` — the shared service, three seams filled.

    Mirrors ``languages/ko/parser.py`` line for line, which is the point: a new
    language's parser factory is ~15 lines over ``SubtitleParserService``, and
    the three ``setdefault`` seams (script gate, mined-form policy, reading
    support) are the whole of what a non-ja language has to say about parsing.
    ``setdefault`` leaves an explicitly injected test double in charge.
    """
    from anki_miner.languages.registry import get_profile
    from anki_miner.services.subtitle_parser import SubtitleParserService

    profile = get_profile(config.language)
    kwargs.setdefault("script_gate", profile.script.contains_target_script)
    kwargs.setdefault("mined_form_policy", profile.mined_form)
    kwargs.setdefault("reading_support", profile.reading)
    return SubtitleParserService(config, **kwargs)


class EuScriptSupport:
    """No script toggles; the ingestion gate is "contains an ASCII letter".

    ``filter_options() == ()`` is the zh precedent: a language whose script
    offers nothing to exclude contributes no rows to Settings -> Filtering, and
    the panel renders an empty section rather than ja's two checkboxes.
    """

    def filter_options(self) -> tuple[ScriptFilterOption, ...]:
        return ()

    def matches(self, option_id: str, form: str) -> bool:
        return False

    def contains_target_script(self, text: str) -> bool:
        return any(char.isascii() and char.isalpha() for char in text)


class EuDictKeys:
    """NFC + casefold term keys, NFC reading keys, Rule-A-only homograph scope.

    Casefolding is safe only because it is SYMMETRIC — the importer folds keys
    with this function before writing them and every lookup folds the query the
    same way (the zh pinyin-reading rationale, ``zh/support.py``). Fold one side
    only and the row is present and never found.

    Rule A' (the tokenizer-lemma tier) and Rule B (the kana-only filter) are
    absent for the same reason they are absent from ko and zh: there is no kana
    script, and the stub's lemma is its own casefolded surface, so A' could
    never fire. ``lemma`` is accepted to keep the one cross-language signature
    (``services/dictionary/storage.py:247``).
    """

    def fold_term(self, s: str) -> str:
        return unicodedata.normalize("NFC", s).casefold()

    def fold_reading(self, s: str | None) -> str | None:
        return None if s is None else unicodedata.normalize("NFC", s)

    def homograph_keep_mask(self, word: str, rows: list[tuple[str, str]], lemma: str | None = None) -> list[bool]:
        """Rule-A-only keep mask aligned to *rows* ((term, content) pairs)."""
        term_exact = [term == word for term, _ in rows]
        if not any(term_exact):
            return [True] * len(rows)
        exact_contents = {content for (_, content), keep in zip(rows, term_exact, strict=True) if keep}
        return [keep or content in exact_contents for (_, content), keep in zip(rows, term_exact, strict=True)]


class EuMinedForm:
    """Card front is the folded lemma: "Words" and "words" are one card.

    The ja ``select_mined_form`` table keys on Japanese POS names, so without a
    policy of its own every stub token would fall through it to the surface —
    punctuation, capitalisation and all.
    """

    def mined_form(
        self,
        pos: str | None,
        orth_base: str,
        lemma: str,
        surface: str,
        pronunciation: str | None = None,
    ) -> str:
        return lemma or orth_base or fold_lemma(surface)


class EuLookupStrategy:
    """The tokenizer lemma once, ``conditions=0`` (pure spelling variant).

    Same shape as ``KoLookupStrategy``: the parser puts the lemma in
    ``TokenizedWord.orth_base``, so orth_base IS the fallback. ``ctype`` is
    unused — duck tokens carry no ``feature.cType``.
    """

    def candidates(self, word: str, orth_base: str, ctype: str | None) -> list[tuple[str, int]]:
        return [(orth_base, 0)] if orth_base and orth_base != word else []


class EuStubHook:
    """One extra card field, so the profile-declared-key path is exercised.

    ``"stub_extra"`` is in neither ``anki_note_builder.OPTIONAL_FIELD_KEYS`` nor
    ``_RAW_HTML_FIELD_KEYS`` — both frozen. It reaches a note only because
    ``AnkiService`` threads the profile's ``extra_card_fields`` into
    ``build_note`` as ``extra_optional_keys``.
    """

    def field_names(self) -> tuple[str, ...]:
        return ("stub_extra",)

    def render(self, word: Any, *, config: AnkiMinerConfig) -> dict[str, str]:
        del config  # nothing gates the stub field
        return {"stub_extra": str(getattr(word, "mined_form", "") or "").upper()}


#: ASCII terminators, space-aware. ``space_aware`` is what keeps "3.14" whole
#: for a terminator set containing "." — it is NOT an abbreviation model, and
#: "Dr." does split (pinned in test_eu_boundary_stub.py).
EU_SENTENCE_RULES = SentenceRules(
    terminators=frozenset(".!?"),
    ellipses=frozenset("…"),
    openers=frozenset("([{"),
    closers=frozenset(")]}"),
    space_aware=True,
)

EU_AUDIO = AudioDefaults(
    gtts_lang="en",
    # Namespaced like ko's: the stem doubles as the Anki media filename, so two
    # languages mining the same spelling must not share one audio file.
    cache_stem_prefix="googletts_eu",
    sentence_cache_stem_prefix="sentencetts_eu",
    custom_fetcher_language="en",
    papago_speaker=None,
    default_chain=(AudioSourceEntry(kind="googletts"),),
    candidates=None,
)

EU_CONTENT_STYLE = ContentTextStyle(
    font_role="eu",
    families=("DejaVu Sans", "Noto Sans", "Arial"),
    wrap=lambda text: text,  # space-delimited: Qt already has break opportunities
)

#: One spec per ``EuStubHook`` field. ``capability`` names a flag in the
#: profile's OWN ``capabilities`` and deliberately not one in the shipped
#: ``CAPABILITY_VOCABULARY`` — admitting it there is part of what a real
#: language's contract-governance edit would be.
EU_EXTRA_CARD_FIELDS: tuple[CardFieldSpec, ...] = (
    CardFieldSpec(key="stub_extra", capability="stub_field", placeholder="StubExtra"),
)

#: Core fields plus the hook's own key. Every ja-specific field is unmapped
#: ("" = feature off, the existing empty-name skip), exactly as ko does it.
EU_CARD_FIELDS: dict[str, str] = {
    "word": "Expression",
    "sentence": "Sentence",
    "definition": "MainDefinition",
    "glossary": "",
    "picture": "Picture",
    "audio": "SentenceAudio",
    "expression_furigana": "",
    "expression_reading": "",
    "sentence_furigana": "",
    "sentence_reading": "",
    "pitch_position": "",
    "pitch_category": "",
    "pitch_graph": "",
    "pitch_text": "",
    "frequency": "",
    "frequency_sort": "",
    "source": "",
    "expression_audio": "",
    "stub_extra": "",
}

#: The single invented POS class the tagger emits.
EU_ALLOWED_POS: tuple[str, ...] = ("WORD",)
EU_EXCLUDED_SUBTYPES: tuple[str, ...] = ()


def eu_normalize(text: str) -> str:
    """Normalise to NFC (composed accented Latin)."""
    return unicodedata.normalize("NFC", text)


def _scoped_defaults() -> dict[str, object]:
    """First-visit values for EVERY language-scoped field.

    Starts from ``blank_scoped_defaults()`` — never hand-written, so a field
    appended to ``LANGUAGE_SCOPED_FIELDS`` cannot silently miss a value here.
    ``switch_language`` raises on incomplete coverage, and that raise is itself
    under test.
    """
    defaults = blank_scoped_defaults()
    defaults["downloader_subtitle_langs"] = "en"
    defaults["expression_audio_chain"] = EU_AUDIO.default_chain
    defaults["allowed_pos"] = EU_ALLOWED_POS
    defaults["excluded_subtypes"] = EU_EXCLUDED_SUBTYPES
    defaults["anki_fields"] = dict(EU_CARD_FIELDS)
    # The one the blank-by-type loop gets wrong rather than merely empty: "" is
    # not a deck AnkiConnect accepts, and inheriting ja's default would file
    # these cards into the Japanese deck. Same split as ko/zh — the deck name is
    # generic, the ja note type ("Lapis") is not, so the note type ships empty.
    defaults["anki_deck_name"] = "Anki Miner"
    return defaults


def build_profile() -> LanguageProfile:
    """Build the stub profile. Every field named explicitly — that IS the proof.

    MUST NOT call ``registry.get_profile``: the registry holds a non-reentrant
    lock across the builder call (see ``zh.build_profile``).
    """
    return LanguageProfile(
        code=EU_CODE,
        display_name="Stub (eu)",
        create_parser=create_parser,
        mined_form=EuMinedForm(),
        lookup=EuLookupStrategy(),
        # No reading layer and no furigana analogue: both optional fields stay
        # None, and the parser's ja reading derivation runs on a duck token
        # whose feature.kana is "" — the ko arrangement exactly.
        reading=None,
        sentence_annotator=None,
        script=EuScriptSupport(),
        audio_track_codes=frozenset({"eng", "en", "english"}),
        import_encodings=("utf-8-sig", "cp1252", "latin-1"),
        scoped_defaults=_scoped_defaults(),
        sentence_rules=EU_SENTENCE_RULES,
        normalize=eu_normalize,
        dict_keys=EuDictKeys(),
        audio=EU_AUDIO,
        asr_language="en",
        captions=CaptionLangs(
            primary="en",
            codes=("en",),
            orig_codes=("en-orig",),
            audio_pattern="^en(-|$)",
            bare_fallback=True,
        ),
        pos_defaults=PosDefaults(
            allowed_pos=EU_ALLOWED_POS,
            excluded_subtypes=EU_EXCLUDED_SUBTYPES,
            labels={"WORD": "Word"},
        ),
        # Empty on purpose: a language that bundles no downloadable resource is
        # a valid profile, and the four resource families degrade to "nothing
        # offered" rather than to a ja-shaped list.
        catalog=(),
        capabilities=frozenset({"stub_field"}),
        card_field_defaults=EU_CARD_FIELDS,
        render_hooks=(EuStubHook(),),
        content_style=EU_CONTENT_STYLE,
        # None, not a probe: the stub needs no third-party engine, and the only
        # defaulted field on the dataclass is the one a language with nothing
        # optional to report leaves unset.
        unavailable_reason=None,
        extra_card_fields=EU_EXTRA_CARD_FIELDS,
        smoke_sentence="The quick brown fox jumps over the lazy dog.",
        english_name="European stub",
    )
