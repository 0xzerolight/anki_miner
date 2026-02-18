"""Tests for ExportService."""

import csv

import pytest

from anki_miner.models.word import WordData
from anki_miner.services.export_service import ExportService


@pytest.fixture
def export_service(test_config):
    """Provide an ExportService instance."""
    return ExportService(test_config)


@pytest.fixture
def make_word_data(make_tokenized_word):
    """Factory fixture for creating WordData instances with sensible defaults."""

    def _make(
        surface="食べる",
        lemma="食べる",
        reading="タベル",
        sentence="日本語を食べる。",
        start_time=1.0,
        end_time=3.0,
        definition="to eat",
        expression_furigana="食[た]べる",
        sentence_furigana="日本語[にほんご]を食[た]べる。",
        pitch_accent="[0]",
        frequency_rank=500,
        screenshot_filename=None,
        audio_filename=None,
        screenshot_path=None,
        audio_path=None,
    ):
        word = make_tokenized_word(
            surface=surface,
            lemma=lemma,
            reading=reading,
            sentence=sentence,
            start_time=start_time,
            end_time=end_time,
            duration=end_time - start_time,
            expression_furigana=expression_furigana,
            sentence_furigana=sentence_furigana,
        )
        return WordData(
            word=word,
            definition=definition,
            pitch_accent=pitch_accent,
            frequency_rank=frequency_rank,
            screenshot_filename=screenshot_filename,
            audio_filename=audio_filename,
            screenshot_path=screenshot_path,
            audio_path=audio_path,
        )

    return _make


@pytest.fixture
def sample_words(make_word_data):
    """Provide a list of sample WordData for testing."""
    return [
        make_word_data(
            surface="食べる",
            lemma="食べる",
            reading="タベル",
            sentence="ご飯を食べる。",
            start_time=1.0,
            end_time=3.0,
            definition="to eat",
            pitch_accent="[0]",
            frequency_rank=500,
        ),
        make_word_data(
            surface="飲んだ",
            lemma="飲む",
            reading="ノム",
            sentence="水を飲んだ。",
            start_time=5.0,
            end_time=7.0,
            definition="to drink",
            pitch_accent="[1]",
            frequency_rank=800,
        ),
        make_word_data(
            surface="走って",
            lemma="走る",
            reading="ハシル",
            sentence="公園を走って。",
            start_time=10.0,
            end_time=12.0,
            definition="to run",
            pitch_accent=None,
            frequency_rank=None,
        ),
    ]


# ── CSV Export ──────────────────────────────────────────────────


class TestExportCsv:
    def test_creates_csv_with_header(self, export_service, sample_words, tmp_path):
        out = tmp_path / "words.csv"
        export_service.export_csv(sample_words, out)

        with open(out, encoding="utf-8") as f:
            reader = csv.reader(f)
            header = next(reader)

        assert "Lemma" in header
        assert "Surface" in header
        assert "Definition" in header
        assert "Pitch Accent" in header
        assert "Frequency Rank" in header
        assert "Start Time" in header
        assert "End Time" in header

    def test_includes_all_fields(self, export_service, sample_words, tmp_path):
        out = tmp_path / "words.csv"
        export_service.export_csv(sample_words, out)

        with open(out, encoding="utf-8") as f:
            reader = csv.reader(f)
            header = next(reader)
            first_row = next(reader)

        assert first_row[header.index("Lemma")] == "食べる"
        assert first_row[header.index("Surface")] == "食べる"
        assert first_row[header.index("Reading")] == "タベル"
        assert first_row[header.index("Definition")] == "to eat"
        assert first_row[header.index("Pitch Accent")] == "[0]"
        assert first_row[header.index("Frequency Rank")] == "500"
        assert first_row[header.index("Start Time")] == "1.00"
        assert first_row[header.index("End Time")] == "3.00"

    def test_returns_row_count(self, export_service, sample_words, tmp_path):
        out = tmp_path / "words.csv"
        count = export_service.export_csv(sample_words, out)
        assert count == 3

    def test_handles_empty_list(self, export_service, tmp_path):
        out = tmp_path / "empty.csv"
        count = export_service.export_csv([], out)
        assert count == 0

        with open(out, encoding="utf-8") as f:
            reader = csv.reader(f)
            header = next(reader)
            rows = list(reader)

        assert len(header) > 0  # Header still written
        assert len(rows) == 0

    def test_handles_none_fields_gracefully(self, export_service, make_word_data, tmp_path):
        word = make_word_data(
            definition=None,
            pitch_accent=None,
            frequency_rank=None,
        )
        out = tmp_path / "nones.csv"
        export_service.export_csv([word], out)

        with open(out, encoding="utf-8") as f:
            reader = csv.reader(f)
            header = next(reader)
            row = next(reader)

        assert row[header.index("Definition")] == ""
        assert row[header.index("Pitch Accent")] == ""
        assert row[header.index("Frequency Rank")] == ""

    def test_media_refs_excluded_by_default(self, export_service, sample_words, tmp_path):
        out = tmp_path / "words.csv"
        export_service.export_csv(sample_words, out)

        with open(out, encoding="utf-8") as f:
            reader = csv.reader(f)
            header = next(reader)

        assert "Screenshot" not in header
        assert "Audio" not in header

    def test_media_refs_included_when_flag_set(self, export_service, make_word_data, tmp_path):
        word = make_word_data(
            screenshot_filename="word_001.jpg",
            audio_filename="word_001.mp3",
        )
        out = tmp_path / "words_media.csv"
        export_service.export_csv([word], out, include_media_refs=True)

        with open(out, encoding="utf-8") as f:
            reader = csv.reader(f)
            header = next(reader)
            row = next(reader)

        assert "Screenshot" in header
        assert "Audio" in header
        assert row[header.index("Screenshot")] == "word_001.jpg"
        assert row[header.index("Audio")] == "word_001.mp3"


# ── TSV Export ──────────────────────────────────────────────────


class TestExportTsv:
    def test_creates_tsv_with_tabs(self, export_service, sample_words, tmp_path):
        out = tmp_path / "words.tsv"
        export_service.export_tsv(sample_words, out)

        content = out.read_text(encoding="utf-8")
        lines = content.strip().split("\n")
        # TSV should use tabs; csv.reader with tab delimiter should parse it
        assert len(lines) == 4  # header + 3 data rows
        # Verify tabs are present in the raw content
        assert "\t" in lines[0]

    def test_returns_row_count(self, export_service, sample_words, tmp_path):
        out = tmp_path / "words.tsv"
        count = export_service.export_tsv(sample_words, out)
        assert count == 3

    def test_includes_media_refs(self, export_service, make_word_data, tmp_path):
        word = make_word_data(
            screenshot_filename="shot.jpg",
            audio_filename="clip.mp3",
        )
        out = tmp_path / "words.tsv"
        export_service.export_tsv([word], out)

        with open(out, encoding="utf-8") as f:
            reader = csv.reader(f, delimiter="\t")
            header = next(reader)
            row = next(reader)

        assert "Screenshot" in header
        assert "Audio" in header
        assert row[header.index("Screenshot")] == "shot.jpg"
        assert row[header.index("Audio")] == "clip.mp3"

    def test_includes_all_core_fields(self, export_service, sample_words, tmp_path):
        out = tmp_path / "words.tsv"
        export_service.export_tsv(sample_words, out)

        with open(out, encoding="utf-8") as f:
            reader = csv.reader(f, delimiter="\t")
            header = next(reader)

        expected = [
            "Lemma",
            "Surface",
            "Reading",
            "Sentence",
            "Definition",
            "Expression Furigana",
            "Sentence Furigana",
            "Pitch Accent",
            "Frequency Rank",
            "Screenshot",
            "Audio",
            "Start Time",
            "End Time",
        ]
        assert header == expected


# ── Vocabulary List Export ──────────────────────────────────────


class TestExportVocabList:
    def test_plain_format(self, export_service, sample_words, tmp_path):
        out = tmp_path / "vocab.txt"
        count = export_service.export_vocab_list(sample_words, out, format="plain")

        lines = out.read_text(encoding="utf-8").strip().split("\n")
        assert count == 3
        assert lines == ["食べる", "飲む", "走る"]

    def test_takoboto_format(self, export_service, sample_words, tmp_path):
        out = tmp_path / "vocab.txt"
        count = export_service.export_vocab_list(sample_words, out, format="takoboto")

        lines = out.read_text(encoding="utf-8").strip().split("\n")
        assert count == 3
        assert lines[0] == "食べる\tタベル"
        assert lines[1] == "飲む\tノム"
        assert lines[2] == "走る\tハシル"

    def test_jpdb_format(self, export_service, sample_words, tmp_path):
        out = tmp_path / "vocab.txt"
        count = export_service.export_vocab_list(sample_words, out, format="jpdb")

        lines = out.read_text(encoding="utf-8").strip().split("\n")
        assert count == 3
        assert lines == ["食べる", "飲む", "走る"]

    def test_deduplicates_words(self, export_service, make_word_data, tmp_path):
        words = [
            make_word_data(lemma="食べる", surface="食べた"),
            make_word_data(lemma="食べる", surface="食べて"),
            make_word_data(lemma="飲む", surface="飲む"),
        ]
        out = tmp_path / "vocab.txt"
        count = export_service.export_vocab_list(words, out, format="plain")

        lines = out.read_text(encoding="utf-8").strip().split("\n")
        assert count == 2
        assert lines == ["食べる", "飲む"]

    def test_handles_empty_list(self, export_service, tmp_path):
        out = tmp_path / "vocab.txt"
        count = export_service.export_vocab_list([], out, format="plain")

        assert count == 0
        assert out.read_text(encoding="utf-8") == ""
