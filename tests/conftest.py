"""Pytest configuration and shared fixtures."""

import pytest

from anki_miner.config import AnkiMinerConfig
from anki_miner.models import MediaData, TokenizedWord
from anki_miner.presenters import NullPresenter, NullProgressCallback


@pytest.fixture
def temp_dir(tmp_path):
    """Provide a temporary directory for test files."""
    return tmp_path


@pytest.fixture
def test_config(temp_dir):
    """Provide a test configuration with temporary paths."""
    return AnkiMinerConfig(
        anki_deck_name="test_deck",
        anki_note_type="test_note_type",
        anki_word_field="word",
        anki_fields={
            "word": "word",
            "sentence": "sentence",
            "definition": "definition",
            "picture": "picture",
            "audio": "audio",
            "expression_furigana": "expression_furigana",
            "sentence_furigana": "sentence_furigana",
            "pitch_accent": "PitchAccent",
            "frequency_rank": "FrequencyRank",
        },
        media_temp_folder=temp_dir / "temp_media",
        jmdict_path=temp_dir / "JMdict_e",
        subtitle_offset=0.0,
        max_parallel_workers=2,  # Reduced for tests
    )


@pytest.fixture
def null_presenter():
    """Provide a null presenter for testing (no output)."""
    return NullPresenter()


@pytest.fixture
def null_progress():
    """Provide a null progress callback for testing."""
    return NullProgressCallback()


@pytest.fixture
def make_tokenized_word():
    """Factory fixture for creating TokenizedWord instances with sensible defaults."""

    def _make(
        surface="食べる",
        lemma="食べる",
        reading="タベル",
        sentence="日本語を食べる。",
        start_time=1.0,
        end_time=3.0,
        duration=2.0,
        video_file=None,
        expression_furigana="",
        sentence_furigana="",
        frequency_rank=None,
    ):
        return TokenizedWord(
            surface=surface,
            lemma=lemma,
            reading=reading,
            sentence=sentence,
            start_time=start_time,
            end_time=end_time,
            duration=duration,
            video_file=video_file,
            expression_furigana=expression_furigana,
            sentence_furigana=sentence_furigana,
            frequency_rank=frequency_rank,
        )

    return _make


@pytest.fixture
def make_media_data(tmp_path):
    """Factory fixture for creating MediaData instances with optional real files."""

    def _make(
        screenshot=True,
        audio=True,
        create_files=False,
        prefix="word_1000",
    ):
        ss_path = tmp_path / f"{prefix}.jpg" if screenshot else None
        au_path = tmp_path / f"{prefix}.mp3" if audio else None
        ss_name = f"{prefix}.jpg" if screenshot else None
        au_name = f"{prefix}.mp3" if audio else None

        if create_files:
            if ss_path:
                ss_path.write_bytes(b"\xff\xd8fake-jpeg")
            if au_path:
                au_path.write_bytes(b"\xff\xfbfake-mp3")

        return MediaData(
            screenshot_path=ss_path,
            audio_path=au_path,
            screenshot_filename=ss_name,
            audio_filename=au_name,
        )

    return _make


class RecordingProgress:
    """A real ProgressCallback implementation that records all calls for assertion."""

    def __init__(self):
        self.starts = []
        self.progresses = []
        self.completes = 0
        self.errors = []

    def on_start(self, total: int, description: str) -> None:
        self.starts.append((total, description))

    def on_progress(self, current: int, item_description: str) -> None:
        self.progresses.append((current, item_description))

    def on_complete(self) -> None:
        self.completes += 1

    def on_error(self, item_description: str, error_message: str) -> None:
        self.errors.append((item_description, error_message))


@pytest.fixture
def recording_progress():
    """Provide a progress callback that records all calls for assertion."""
    return RecordingProgress()


@pytest.fixture
def sample_subtitle_content():
    """Provide sample subtitle content for testing."""
    return """[Script Info]
Title: Test Subtitle

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
Dialogue: 0,0:00:01.00,0:00:03.00,Default,,0,0,0,,これは日本語のテストです。
Dialogue: 0,0:00:04.00,0:00:06.00,Default,,0,0,0,,私は学生です。
Dialogue: 0,0:00:07.00,0:00:09.00,Default,,0,0,0,,今日は良い天気ですね。
"""


@pytest.fixture
def sample_subtitle_file(temp_dir, sample_subtitle_content):
    """Create a sample subtitle file for testing."""
    subtitle_file = temp_dir / "test.ass"
    subtitle_file.write_text(sample_subtitle_content, encoding="utf-8")
    return subtitle_file
