"""Tests for data model classes."""

from anki_miner.models.media import MediaData
from anki_miner.models.processing import ProcessingResult, ValidationIssue, ValidationResult
from anki_miner.models.word import TokenizedWord, WordData


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

    def test_has_any_media_true(self, tmp_path):
        ss = tmp_path / "ss.jpg"
        ss.write_bytes(b"fake")
        md = MediaData(screenshot_path=ss, screenshot_filename="ss.jpg")
        assert md.has_any_media is True

    def test_has_any_media_false(self):
        md = MediaData()
        assert md.has_any_media is False

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

    def test_has_new_words_true(self):
        result = ProcessingResult(total_words_found=10, new_words_found=3, cards_created=3)
        assert result.has_new_words is True

    def test_has_new_words_false(self):
        result = ProcessingResult(total_words_found=10, new_words_found=0, cards_created=0)
        assert result.has_new_words is False

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

    def test_has_errors(self):
        result = ValidationResult(
            ankiconnect_ok=False,
            ffmpeg_ok=True,
            deck_exists=True,
            note_type_exists=True,
            issues=[ValidationIssue("AnkiConnect", "ERROR", "Connection failed")],
        )
        assert result.has_errors is True

    def test_has_warnings(self):
        result = ValidationResult(
            ankiconnect_ok=True,
            ffmpeg_ok=True,
            deck_exists=True,
            note_type_exists=True,
            issues=[ValidationIssue("Temp Folder", "WARNING", "Could not create")],
        )
        assert result.has_warnings is True

    def test_no_errors_no_warnings(self):
        result = ValidationResult(
            ankiconnect_ok=True,
            ffmpeg_ok=True,
            deck_exists=True,
            note_type_exists=True,
        )
        assert result.has_errors is False
        assert result.has_warnings is False

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
