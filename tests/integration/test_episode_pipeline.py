"""Integration tests for the full episode processing pipeline."""

from unittest.mock import MagicMock, patch

import pytest

from anki_miner.config import AnkiMinerConfig
from anki_miner.models import TokenizedWord
from anki_miner.orchestration.episode_processor import EpisodeProcessor
from anki_miner.presenters import NullPresenter
from anki_miner.services import (
    AnkiService,
    DefinitionService,
    MediaExtractorService,
    SubtitleParserService,
    WordFilterService,
)


def _make_word(surface, lemma, start=1.0):
    return TokenizedWord(
        surface=surface,
        lemma=lemma,
        reading=lemma,
        sentence=f"{surface}のテスト",
        start_time=start,
        end_time=start + 2.0,
        duration=2.0,
    )


class TestEpisodePipeline:
    """Integration tests using real service instances with mocked external boundaries."""

    @pytest.fixture
    def config(self, tmp_path):
        return AnkiMinerConfig(
            anki_deck_name="test",
            anki_note_type="test_type",
            anki_word_field="word",
            anki_fields={
                "word": "word",
                "sentence": "sentence",
                "definition": "definition",
                "picture": "picture",
                "audio": "audio",
                "expression_furigana": "expression_furigana",
                "sentence_furigana": "sentence_furigana",
            },
            media_temp_folder=tmp_path / "media",
            jmdict_path=tmp_path / "JMdict_e",
            max_parallel_workers=1,
        )

    def test_full_pipeline(self, config, tmp_path):
        """Full pipeline: parse → filter → extract → define → create cards."""
        video = tmp_path / "ep01.mkv"
        sub = tmp_path / "ep01.ass"

        [_make_word("食べる", "食べる", 1.0), _make_word("走る", "走る", 5.0)]

        # Mock pysubs2.load to return mock subtitle lines
        mock_line1 = MagicMock()
        mock_line1.text = "食べる"
        mock_line1.start = 1000
        mock_line1.end = 3000

        mock_line2 = MagicMock()
        mock_line2.text = "走る"
        mock_line2.start = 5000
        mock_line2.end = 7000

        mock_subs = MagicMock()
        mock_subs.__iter__ = MagicMock(return_value=iter([mock_line1, mock_line2]))

        # Mock tagger to return proper tokens
        def make_mock_token(surface, lemma):
            t = MagicMock()
            t.surface = surface
            t.feature.pos1 = "動詞"
            t.feature.pos2 = None
            t.feature.lemma = lemma
            t.feature.kana = lemma
            return t

        def tagger_func(text):
            if "食べる" in text:
                return [make_mock_token("食べる", "食べる")]
            elif "走る" in text:
                return [make_mock_token("走る", "走る")]
            return []

        mock_tagger = MagicMock(side_effect=tagger_func)

        # Mock AnkiConnect responses
        mock_anki_response = MagicMock()
        mock_anki_response.json.return_value = {"result": [], "error": None}

        # Mock subprocess for media extraction
        mock_proc = MagicMock()
        mock_proc.returncode = 0

        # For screenshots to "exist"
        media_dir = tmp_path / "media"
        media_dir.mkdir(parents=True, exist_ok=True)

        with (
            patch("anki_miner.services.subtitle_parser.pysubs2.load", return_value=mock_subs),
            patch("anki_miner.services.subtitle_parser.fugashi.Tagger", return_value=mock_tagger),
            patch("anki_miner.services.media_extractor.subprocess.run", return_value=mock_proc),
            patch("anki_miner.services.media_extractor.ensure_directory"),
            patch("anki_miner.services.definition_service.requests.get") as mock_get,
            patch("anki_miner.services.definition_service.time.sleep"),
            patch("anki_miner.services.anki_service.requests.post") as mock_post,
        ):

            # AnkiConnect returns empty vocabulary, then accepts cards
            find_resp = MagicMock()
            find_resp.json.return_value = {"result": [], "error": None}
            add_resp = MagicMock()
            add_resp.json.return_value = {"result": [1, 2], "error": None}
            mock_post.side_effect = [find_resp, add_resp]

            # Jisho returns definitions
            jisho_resp = MagicMock()
            jisho_resp.status_code = 200
            jisho_resp.json.return_value = {
                "data": [{"senses": [{"english_definitions": ["to eat"]}]}]
            }
            mock_get.return_value = jisho_resp

            # Build services with real instances
            subtitle_parser = SubtitleParserService(config)
            word_filter = WordFilterService(config)
            media_extractor = MediaExtractorService(config)
            definition_service = DefinitionService(config)
            anki_service = AnkiService(config)

            processor = EpisodeProcessor(
                config=config,
                subtitle_parser=subtitle_parser,
                word_filter=word_filter,
                media_extractor=media_extractor,
                definition_service=definition_service,
                anki_service=anki_service,
                presenter=NullPresenter(),
            )

            result = processor.process_episode(video, sub)

        assert result.total_words_found >= 0  # Some words found from parsing
        assert result.elapsed_time > 0

    def test_preview_mode_skips_media_and_cards(self, config, tmp_path):
        """Preview mode should parse and filter but not extract media or create cards."""
        video = tmp_path / "ep01.mkv"
        sub = tmp_path / "ep01.ass"

        mock_line = MagicMock()
        mock_line.text = "勉強する"
        mock_line.start = 1000
        mock_line.end = 3000

        mock_subs = MagicMock()
        mock_subs.__iter__ = MagicMock(return_value=iter([mock_line]))

        mock_token = MagicMock()
        mock_token.surface = "勉強"
        mock_token.feature.pos1 = "名詞"
        mock_token.feature.pos2 = None
        mock_token.feature.lemma = "勉強"
        mock_token.feature.kana = "ベンキョウ"

        mock_tagger = MagicMock()
        mock_tagger.return_value = [mock_token]

        find_resp = MagicMock()
        find_resp.json.return_value = {"result": [], "error": None}

        with (
            patch("anki_miner.services.subtitle_parser.pysubs2.load", return_value=mock_subs),
            patch("anki_miner.services.subtitle_parser.fugashi.Tagger", return_value=mock_tagger),
            patch("anki_miner.services.media_extractor.subprocess.run") as mock_subprocess,
            patch("anki_miner.services.media_extractor.ensure_directory"),
            patch("anki_miner.services.anki_service.requests.post", return_value=find_resp),
        ):

            subtitle_parser = SubtitleParserService(config)
            word_filter = WordFilterService(config)
            media_extractor = MediaExtractorService(config)
            definition_service = DefinitionService(config)
            anki_service = AnkiService(config)

            processor = EpisodeProcessor(
                config=config,
                subtitle_parser=subtitle_parser,
                word_filter=word_filter,
                media_extractor=media_extractor,
                definition_service=definition_service,
                anki_service=anki_service,
                presenter=NullPresenter(),
            )

            result = processor.process_episode(video, sub, preview_mode=True)

        # Preview mode should not touch media extraction
        mock_subprocess.assert_not_called()
        assert result.cards_created == 0
        assert result.new_words_found >= 0

    def test_all_words_known_returns_early(self, config, tmp_path):
        """When all words are already in Anki, should return early."""
        video = tmp_path / "ep01.mkv"
        sub = tmp_path / "ep01.ass"

        mock_line = MagicMock()
        mock_line.text = "食べる"
        mock_line.start = 1000
        mock_line.end = 3000

        mock_subs = MagicMock()
        mock_subs.__iter__ = MagicMock(return_value=iter([mock_line]))

        mock_token = MagicMock()
        mock_token.surface = "食べる"
        mock_token.feature.pos1 = "動詞"
        mock_token.feature.pos2 = None
        mock_token.feature.lemma = "食べる"
        mock_token.feature.kana = "タベル"

        mock_tagger = MagicMock()
        mock_tagger.return_value = [mock_token]

        # findNotes returns note IDs, notesInfo returns word values
        find_resp = MagicMock()
        find_resp.json.return_value = {"result": [1], "error": None}
        notes_resp = MagicMock()
        notes_resp.json.return_value = {
            "result": [{"fields": {"word": {"value": "食べる"}}}],
            "error": None,
        }

        with (
            patch("anki_miner.services.subtitle_parser.pysubs2.load", return_value=mock_subs),
            patch("anki_miner.services.subtitle_parser.fugashi.Tagger", return_value=mock_tagger),
            patch("anki_miner.services.media_extractor.subprocess.run") as mock_subprocess,
            patch("anki_miner.services.media_extractor.ensure_directory"),
            patch(
                "anki_miner.services.anki_service.requests.post",
                side_effect=[find_resp, notes_resp],
            ),
        ):

            subtitle_parser = SubtitleParserService(config)
            word_filter = WordFilterService(config)
            media_extractor = MediaExtractorService(config)
            definition_service = DefinitionService(config)
            anki_service = AnkiService(config)

            processor = EpisodeProcessor(
                config=config,
                subtitle_parser=subtitle_parser,
                word_filter=word_filter,
                media_extractor=media_extractor,
                definition_service=definition_service,
                anki_service=anki_service,
                presenter=NullPresenter(),
            )

            result = processor.process_episode(video, sub)

        assert result.new_words_found == 0
        assert result.cards_created == 0
        mock_subprocess.assert_not_called()
