"""Tests for data model classes."""

from dataclasses import FrozenInstanceError

import pytest

from anki_miner.models.media import MediaData
from anki_miner.models.processing import ProcessingResult, ValidationIssue, ValidationResult
from anki_miner.models.word import (
    LineLemmas,
    TokenizedWord,
    WordData,
    resolve_pronoun_fold_reading,
    select_mined_form,
)


class TestTokenizedWord:
    """Tests for TokenizedWord dataclass."""

    def test_basic_creation(self):
        word = TokenizedWord(
            surface="食べる",
            lemma="食べる",
            reading="タベル",
            sentence="日本語を食べる。",
            start_time=1.0,
            end_time=3.0,
            duration=2.0,
        )
        assert word.surface == "食べる"
        assert word.lemma == "食べる"
        assert word.video_file is None

    def test_with_video_file(self, tmp_path):
        video = tmp_path / "ep01.mkv"
        word = TokenizedWord(
            surface="走る",
            lemma="走る",
            reading="ハシル",
            sentence="走る。",
            start_time=0.0,
            end_time=1.0,
            duration=1.0,
            video_file=video,
        )
        assert word.video_file == video

    def test_resolved_reading_defaults_empty(self):
        # Pitch-realignment field for the じる/ずる override: empty means "no
        # override; use lemma_reading". Set only when the resolver diverges the
        # card front's reading from the lemma's own reading.
        word = TokenizedWord(
            surface="感じ",
            lemma="感ずる",
            reading="カンジ",
            sentence="感じた。",
            start_time=0.0,
            end_time=1.0,
            duration=1.0,
        )
        assert word.resolved_reading == ""

    def test_resolved_reading_settable(self):
        word = TokenizedWord(
            surface="感じ",
            lemma="感ずる",
            orth_base="感じる",
            reading="カンジ",
            sentence="感じた。",
            start_time=0.0,
            end_time=1.0,
            duration=1.0,
            resolved_reading="かんじる",
        )
        assert word.resolved_reading == "かんじる"

    def test_str_shows_lemma_and_reading(self):
        word = TokenizedWord(
            surface="食べた",
            lemma="食べる",
            reading="タベル",
            sentence="",
            start_time=0,
            end_time=0,
            duration=0,
        )
        assert "食べる" in str(word)
        assert "タベル" in str(word)

    def test_repr(self):
        word = TokenizedWord(
            surface="走った",
            lemma="走る",
            reading="ハシル",
            sentence="",
            start_time=0,
            end_time=0,
            duration=0,
        )
        r = repr(word)
        assert "走る" in r
        assert "走った" in r

    def test_furigana_fields_default_empty(self):
        word = TokenizedWord(
            surface="食べる",
            lemma="食べる",
            reading="タベル",
            sentence="",
            start_time=0,
            end_time=0,
            duration=0,
        )
        assert word.expression_furigana == ""
        assert word.sentence_furigana == ""

    def test_furigana_fields_set_correctly(self):
        word = TokenizedWord(
            surface="食べる",
            lemma="食べる",
            reading="タベル",
            sentence="日本語を食べる。",
            start_time=0,
            end_time=0,
            duration=0,
            expression_furigana=" 食べる[たべる]",
            sentence_furigana=" 日本語[にほんご]を 食べる[たべる]。",
        )
        assert word.expression_furigana == " 食べる[たべる]"
        assert word.sentence_furigana == " 日本語[にほんご]を 食べる[たべる]。"

    @pytest.mark.parametrize(
        "pos,surface,lemma,expected",
        [
            # Verb conjugation → lemma wins (Issue #19).
            ("動詞", "破れ", "破れる", "破れる"),
            ("動詞", "食べた", "食べる", "食べる"),
            # Adjective inflection → lemma wins.
            ("形容詞", "高い", "高い", "高い"),
            ("形容詞", "高かった", "高い", "高い"),
            # Noun → surface wins (Issue #5: unidic 豪腕 → 剛腕 quirk).
            ("名詞", "豪腕", "剛腕", "豪腕"),
            ("名詞", "刑務所", "刑務所", "刑務所"),
            # Other / missing pos → falls back to surface defensively.
            ("形状詞", "綺麗", "綺麗", "綺麗"),
            (None, "テスト", "テスト", "テスト"),
        ],
    )
    def test_mined_form_pos_aware(self, pos, surface, lemma, expected):
        """mined_form returns lemma for conjugating POS, surface otherwise."""
        word = TokenizedWord(
            surface=surface,
            lemma=lemma,
            reading="",
            sentence="",
            start_time=0,
            end_time=0,
            duration=0,
            pos=pos,
        )
        assert word.mined_form == expected

    @pytest.mark.parametrize(
        "pos,surface,lemma,orth_base,expected",
        [
            # Kanji-variant verb: unidic lemma normalizes 乞う→請う; the card
            # must keep the source spelling via orth_base.
            ("動詞", "乞わ", "請う", "乞う", "乞う"),
            ("動詞", "喰らえ", "食らう", "喰らう", "喰らう"),
            # Kanji-variant adjective.
            ("形容詞", "淋しかっ", "寂しい", "淋しい", "淋しい"),
            # Empty orth_base (synthetic/OOV token) → lemma fallback.
            ("動詞", "破れ", "破れる", "", "破れる"),
            # Noun: surface wins regardless of a divergent orth_base.
            ("名詞", "豪腕", "剛腕", "剛腕", "豪腕"),
        ],
    )
    def test_mined_form_prefers_orth_base_for_conjugating_pos(self, pos, surface, lemma, orth_base, expected):
        """Verbs/adjectives mine as orth_base (source orthography), falling back
        to lemma when empty; nouns ignore orth_base entirely."""
        word = TokenizedWord(
            surface=surface,
            lemma=lemma,
            reading="",
            sentence="",
            start_time=0,
            end_time=0,
            duration=0,
            pos=pos,
            orth_base=orth_base,
        )
        assert word.mined_form == expected

    def test_select_mined_form_matches_property(self):
        """The module-level helper is the single selection rule the parser and
        the property share."""
        assert select_mined_form("動詞", "乞う", "請う", "乞わ") == "乞う"
        assert select_mined_form("動詞", "", "請う", "乞わ") == "請う"
        assert select_mined_form("形容詞", "淋しい", "寂しい", "淋しかっ") == "淋しい"
        assert select_mined_form("名詞", "剛腕", "剛腕", "豪腕") == "豪腕"
        assert select_mined_form(None, "テスト", "テスト", "テスト") == "テスト"


class TestVowelElongationNounFold:
    """名詞 whose surface is the lemma plus a colloquial vowel-elongation tail
    (手ぇ, 気い) mines the bare lemma so it dedups against the plain-form card."""

    @pytest.mark.parametrize(
        ("lemma", "surface"),
        [
            ("手", "手ぇ"),  # small-え elongation
            ("目", "目ぇ"),
            ("気", "気い"),  # full-vowel elongation
            ("血", "血ぃ"),
            ("手", "手ええ"),  # 2-char tail
            ("手", "手ー"),  # long-vowel mark tail
        ],
    )
    def test_folds_vowel_elongated_noun_to_lemma(self, lemma, surface):
        assert select_mined_form("名詞", surface, lemma, surface) == lemma

    @pytest.mark.parametrize(
        ("pos", "orth_base", "lemma", "surface", "expected"),
        [
            # コーヒー: surface == lemma (gloss stripped) → no fold, keep surface.
            ("名詞", "コーヒー", "コーヒー", "コーヒー", "コーヒー"),
            # Loanword whose tail is NOT a vowel/elongation char.
            ("名詞", "パン", "パン", "パンダ", "パンダ"),
            # Issue #5 homograph: surface does not start with the variant lemma.
            ("名詞", "剛腕", "剛腕", "豪腕", "豪腕"),
            # 3-char tail is out of the 1-2 window.
            ("名詞", "手", "手", "手ぇぇぇ", "手ぇぇぇ"),
            # Only 名詞 folds — 代名詞 keeps surface.
            ("代名詞", "俺", "俺", "俺え", "俺え"),
            # Verb path is unaffected (returns orth_base).
            ("動詞", "見る", "見る", "見え", "見る"),
        ],
    )
    def test_does_not_overfold(self, pos, orth_base, lemma, surface, expected):
        assert select_mined_form(pos, orth_base, lemma, surface) == expected

    def test_mined_form_property_folds_vowel_noun(self):
        word = TokenizedWord(
            surface="手ぇ",
            lemma="手",
            reading="テエ",
            sentence="",
            start_time=0,
            end_time=0,
            duration=0,
            pos="名詞",
            orth_base="手ぇ",
        )
        assert word.mined_form == "手"


class TestKatakanaPronounFold:
    """代名詞 whose surface is a curated katakana pronoun (ワタシ, オマエ) mines the
    conventional kanji card front so it dedups against the plain-kanji card.

    The (surface, lemma) pairs are the REAL unidic-lite tokenization — probed on
    the shipping dictionary — so ``lemma`` is the value lemma-trust would wrongly
    card (オマエ→御前). The fold is membership-only, never lemma-derived.
    """

    @pytest.mark.parametrize(
        ("surface", "lemma", "kanji"),
        [
            ("ワタシ", "私", "私"),
            ("ボク", "僕", "僕"),
            ("キサマ", "貴様", "貴様"),
            ("ワレ", "我", "我"),
            ("オマエ", "御前", "お前"),  # lemma 御前 (would card 御前[ごぜん]) — NOT trusted
        ],
    )
    def test_folds_katakana_pronoun_to_kanji(self, surface, lemma, kanji):
        # Pre-fix the card front was the katakana surface itself; the fold must
        # replace it with the kanji spelling (pinned different).
        assert surface != kanji
        assert select_mined_form("代名詞", surface, lemma, surface) == kanji

    @pytest.mark.parametrize(
        ("surface", "lemma"),
        [
            ("アナタ", "貴方"),  # non-map katakana 代名詞 — stays surface
            ("オラ", "己"),
            ("コレ", "此れ"),
            ("ソレ", "其れ"),
            ("ワイ", "わし"),  # declared residual
        ],
    )
    def test_non_map_katakana_pronoun_unaffected(self, surface, lemma):
        assert select_mined_form("代名詞", surface, lemma, surface) == surface

    def test_natural_kanji_pronoun_unaffected(self):
        # A pronoun already written in kanji (surface 私) is not a map key, so it
        # keeps its surface — no double-fold, and its natural reading path stands.
        assert select_mined_form("代名詞", "私", "私", "私") == "私"

    def test_non_pronoun_katakana_surface_unaffected(self):
        # The map is 代名詞-gated: a 名詞 spelled ワタシ (unlikely, but proves the pos
        # guard) does not fold.
        assert select_mined_form("名詞", "ワタシ", "ワタシ", "ワタシ") == "ワタシ"

    def test_mined_form_property_folds_katakana_pronoun(self):
        word = TokenizedWord(
            surface="オマエ",
            lemma="御前",
            reading="オマエ",
            sentence="",
            start_time=0,
            end_time=0,
            duration=0,
            pos="代名詞",
            orth_base="オマエ",
        )
        assert word.mined_form == "お前"

    @pytest.mark.parametrize(
        ("surface", "mined", "reading"),
        [
            ("ワタシ", "私", "わたし"),
            ("ボク", "僕", "ぼく"),
            ("キサマ", "貴様", "きさま"),
            ("ワレ", "我", "われ"),
            ("オマエ", "お前", "おまえ"),
        ],
    )
    def test_resolve_pronoun_fold_reading_paired(self, surface, mined, reading):
        assert resolve_pronoun_fold_reading(surface, mined) == reading

    @pytest.mark.parametrize(
        ("surface", "mined"),
        [
            ("ワタシ", "ワタシ"),  # mined not yet folded → no override
            ("ワタシ", "僕"),  # surface/kanji mismatch (self-gating)
            ("私", "私"),  # natural kanji surface not a key
            ("アナタ", "貴方"),  # non-map pronoun
            ("誰", "誰"),  # unrelated word
        ],
    )
    def test_resolve_pronoun_fold_reading_returns_none(self, surface, mined):
        assert resolve_pronoun_fold_reading(surface, mined) is None


class TestLineLemmas:
    """Tests for LineLemmas dataclass."""

    def test_basic_creation_with_defaults(self):
        line = LineLemmas(
            line_text="日本語を食べる",
            lemmas=frozenset({"日本語", "食べる"}),
            start_time=1.0,
            end_time=3.0,
            duration=2.0,
        )
        assert line.line_text == "日本語を食べる"
        assert line.lemmas == frozenset({"日本語", "食べる"})
        assert line.start_time == 1.0
        assert line.end_time == 3.0
        assert line.duration == 2.0
        # Furigana / reading default to empty string when not provided.
        assert line.sentence_furigana == ""
        assert line.sentence_reading == ""

    def test_furigana_and_reading_set(self):
        line = LineLemmas(
            line_text="食べる",
            lemmas=frozenset({"食べる"}),
            start_time=0.0,
            end_time=1.0,
            duration=1.0,
            sentence_furigana=" 食べる[たべる]",
            sentence_reading="たべる",
        )
        assert line.sentence_furigana == " 食べる[たべる]"
        assert line.sentence_reading == "たべる"

    def test_is_frozen(self):
        """Mutating any field must raise FrozenInstanceError."""
        line = LineLemmas(
            line_text="テスト",
            lemmas=frozenset({"テスト"}),
            start_time=0.0,
            end_time=1.0,
            duration=1.0,
        )
        with pytest.raises(FrozenInstanceError):
            line.line_text = "changed"  # type: ignore[misc]
        with pytest.raises(FrozenInstanceError):
            line.lemmas = frozenset()  # type: ignore[misc]
        with pytest.raises(FrozenInstanceError):
            line.start_time = 99.0  # type: ignore[misc]


class TestWordData:
    """Tests for WordData dataclass."""

    def _make_word(self):
        return TokenizedWord(
            surface="食べる",
            lemma="食べる",
            reading="タベル",
            sentence="",
            start_time=0,
            end_time=0,
            duration=0,
        )

    def test_has_media_with_screenshot(self, tmp_path):
        wd = WordData(
            word=self._make_word(),
            screenshot_path=tmp_path / "ss.jpg",
        )
        assert wd.has_media is True

    def test_has_media_with_audio(self, tmp_path):
        wd = WordData(
            word=self._make_word(),
            audio_path=tmp_path / "au.mp3",
        )
        assert wd.has_media is True

    def test_has_media_false_when_none(self):
        wd = WordData(word=self._make_word())
        assert wd.has_media is False

    def test_has_definition_true(self):
        wd = WordData(word=self._make_word(), definition="to eat")
        assert wd.has_definition is True

    def test_has_definition_false_when_none(self):
        wd = WordData(word=self._make_word(), definition=None)
        assert wd.has_definition is False

    def test_has_definition_false_when_empty(self):
        wd = WordData(word=self._make_word(), definition="")
        assert wd.has_definition is False

    def test_str_with_definition(self):
        wd = WordData(word=self._make_word(), definition="to eat food")
        s = str(wd)
        assert "食べる" in s
        assert "to eat" in s

    def test_str_without_definition(self):
        wd = WordData(word=self._make_word())
        assert "No definition" in str(wd)


class TestMediaData:
    """Tests for MediaData dataclass."""

    def test_has_screenshot_with_real_file(self, tmp_path):
        ss = tmp_path / "screenshot.jpg"
        ss.write_bytes(b"fake")
        md = MediaData(screenshot_path=ss, screenshot_filename="screenshot.jpg")
        assert md.has_screenshot is True

    def test_has_screenshot_false_missing_file(self, tmp_path):
        md = MediaData(
            screenshot_path=tmp_path / "nonexistent.jpg",
            screenshot_filename="nonexistent.jpg",
        )
        assert md.has_screenshot is False

    def test_has_screenshot_false_when_none(self):
        md = MediaData()
        assert md.has_screenshot is False

    def test_has_audio_with_real_file(self, tmp_path):
        au = tmp_path / "audio.mp3"
        au.write_bytes(b"fake")
        md = MediaData(audio_path=au, audio_filename="audio.mp3")
        assert md.has_audio is True

    def test_has_audio_false_missing_file(self, tmp_path):
        md = MediaData(
            audio_path=tmp_path / "nonexistent.mp3",
            audio_filename="nonexistent.mp3",
        )
        assert md.has_audio is False

    def test_str_with_media(self, tmp_path):
        ss = tmp_path / "ss.jpg"
        ss.write_bytes(b"fake")
        au = tmp_path / "au.mp3"
        au.write_bytes(b"fake")
        md = MediaData(
            screenshot_path=ss,
            audio_path=au,
            screenshot_filename="ss.jpg",
            audio_filename="au.mp3",
        )
        s = str(md)
        assert "Screenshot" in s
        assert "Audio" in s

    def test_str_no_media(self):
        md = MediaData()
        assert "No media" in str(md)


class TestProcessingResult:
    """Tests for ProcessingResult dataclass."""

    def test_success_when_no_errors(self):
        result = ProcessingResult(total_words_found=10, new_words_found=5, cards_created=5)
        assert result.success is True

    def test_not_success_when_errors(self):
        result = ProcessingResult(
            total_words_found=10,
            new_words_found=5,
            cards_created=0,
            errors=["Something failed"],
        )
        assert result.success is False

    def test_default_elapsed_time(self):
        result = ProcessingResult(total_words_found=0, new_words_found=0, cards_created=0)
        assert result.elapsed_time == 0.0

    def test_str_representation(self):
        result = ProcessingResult(
            total_words_found=10,
            new_words_found=5,
            cards_created=3,
            elapsed_time=2.5,
        )
        s = str(result)
        assert "10" in s
        assert "5" in s
        assert "3" in s


class TestValidationIssue:
    """Tests for ValidationIssue dataclass."""

    def test_str_representation(self):
        issue = ValidationIssue(component="ffmpeg", severity="ERROR", message="not found")
        s = str(issue)
        assert "ERROR" in s
        assert "ffmpeg" in s
        assert "not found" in s


class TestValidationResult:
    """Tests for ValidationResult dataclass."""

    def test_all_passed_true(self):
        result = ValidationResult(
            ankiconnect_ok=True,
            ffmpeg_ok=True,
            deck_exists=True,
            note_type_exists=True,
        )
        assert result.all_passed is True

    def test_all_passed_false_ankiconnect(self):
        result = ValidationResult(
            ankiconnect_ok=False,
            ffmpeg_ok=True,
            deck_exists=True,
            note_type_exists=True,
        )
        assert result.all_passed is False

    def test_all_passed_false_ffmpeg(self):
        result = ValidationResult(
            ankiconnect_ok=True,
            ffmpeg_ok=False,
            deck_exists=True,
            note_type_exists=True,
        )
        assert result.all_passed is False

    def test_all_passed_false_ffprobe(self):
        result = ValidationResult(
            ankiconnect_ok=True,
            ffmpeg_ok=True,
            ffprobe_ok=False,
            deck_exists=True,
            note_type_exists=True,
        )
        assert result.all_passed is False

    def test_ffprobe_defaults_true(self):
        result = ValidationResult(
            ankiconnect_ok=True,
            ffmpeg_ok=True,
            deck_exists=True,
            note_type_exists=True,
        )
        assert result.ffprobe_ok is True

    def test_get_errors(self):
        issues = [
            ValidationIssue("A", "ERROR", "msg1"),
            ValidationIssue("B", "WARNING", "msg2"),
            ValidationIssue("C", "ERROR", "msg3"),
        ]
        result = ValidationResult(
            ankiconnect_ok=False,
            ffmpeg_ok=False,
            deck_exists=True,
            note_type_exists=True,
            issues=issues,
        )
        errors = result.get_errors()
        assert len(errors) == 2
        assert all(e.severity == "ERROR" for e in errors)

    def test_get_warnings(self):
        issues = [
            ValidationIssue("A", "ERROR", "msg1"),
            ValidationIssue("B", "WARNING", "msg2"),
        ]
        result = ValidationResult(
            ankiconnect_ok=False,
            ffmpeg_ok=True,
            deck_exists=True,
            note_type_exists=True,
            issues=issues,
        )
        warnings = result.get_warnings()
        assert len(warnings) == 1
        assert warnings[0].component == "B"

    def test_str_passed(self):
        result = ValidationResult(
            ankiconnect_ok=True,
            ffmpeg_ok=True,
            deck_exists=True,
            note_type_exists=True,
        )
        assert "PASSED" in str(result)

    def test_str_failed(self):
        result = ValidationResult(
            ankiconnect_ok=False,
            ffmpeg_ok=True,
            deck_exists=True,
            note_type_exists=True,
        )
        assert "FAILED" in str(result)
