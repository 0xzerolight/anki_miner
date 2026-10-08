"""The not-mined report: every word a run saw and made no card for, with why."""

from __future__ import annotations

from functools import partial

from anki_miner.models import NotMinedReason
from anki_miner.presenters import NullPresenter
from anki_miner.services.word_filter import WordFilterService
from anki_miner.services.word_pool import fixed_selection
from tests.conftest import build_processor
from tests.unit._processor_fixtures import make_media, make_word

_word = partial(make_word, pos="動詞")


def _run(config, services, tmp_path, words, *, created=(), not_created=None, **kwargs):
    """One process_episode over ``words``; Anki confirms ``created`` and refuses ``not_created``."""
    parser = services["subtitle_parser"]
    parser.parse_subtitle_file.return_value = list(words)
    parser.parse_subtitle_file_with_index.return_value = (list(words), [])
    parser.count_lemmas.return_value = {}
    anki = services["anki_service"]

    def _create(card_data, progress_callback=None):
        anki.last_created_mined_forms = list(created)
        anki.last_created_lemmas = list(created)
        anki.last_not_created = dict(not_created or {})
        return list(range(len(created)))

    anki.create_cards_batch.side_effect = _create
    processor = build_processor(
        config=config, presenter=NullPresenter(), **{**services, "word_filter": WordFilterService(config)}
    )
    return processor, processor.process_episode(tmp_path / "v.mkv", tmp_path / "s.ass", **kwargs)


def _media_for(words):
    return [(w, make_media(w.mined_form)) for w in words]


class TestNotMinedReport:
    def test_parse_rejects_reach_the_result(self, test_config, mock_services, tmp_path):
        taberu = _word("食べる")
        mock_services["subtitle_parser"].last_parse_rejects = {"ちょっと": NotMinedReason.KANA_ONLY}
        mock_services["anki_service"].get_existing_vocabulary.return_value = set()
        mock_services["media_extractor"].extract_media_batch.return_value = _media_for([taberu])
        mock_services["definition_service"].get_definitions_batch.return_value = ["1. to eat"]

        _proc, result = _run(test_config, mock_services, tmp_path, [taberu], created=["食べる"])

        assert result.not_mined is not None
        assert result.not_mined.forms(NotMinedReason.KANA_ONLY) == {"ちょっと"}
        assert result.not_mined.words == {"ちょっと"}

    def test_a_known_word_the_parse_turned_away_is_reported_as_known(self, test_config, mock_services, tmp_path):
        """Whitelisting a known word would not mine it (the known check beats the
        whitelist), so its parse reason must not tell the user to."""
        taberu = _word("食べる")
        mock_services["subtitle_parser"].last_parse_rejects = {
            "ちょっと": NotMinedReason.KANA_ONLY,
            "はい": NotMinedReason.WORD_TYPE,
        }
        mock_services["anki_service"].get_existing_vocabulary.return_value = {"ちょっと"}
        mock_services["media_extractor"].extract_media_batch.return_value = _media_for([taberu])
        mock_services["definition_service"].get_definitions_batch.return_value = ["1. to eat"]

        _proc, result = _run(test_config, mock_services, tmp_path, [taberu], created=["食べる"])

        assert result.not_mined.forms(NotMinedReason.KNOWN) == {"ちょっと"}
        assert result.not_mined.forms(NotMinedReason.KANA_ONLY) == frozenset()
        assert result.not_mined.forms(NotMinedReason.WORD_TYPE) == {"はい"}

    def test_known_words_are_reported_and_mined_ones_never(self, test_config, mock_services, tmp_path):
        taberu, nomu = _word("食べる"), _word("飲む", start_time=5.0)
        mock_services["anki_service"].get_existing_vocabulary.return_value = {"飲む"}
        mock_services["media_extractor"].extract_media_batch.return_value = _media_for([taberu])
        mock_services["definition_service"].get_definitions_batch.return_value = ["1. to eat"]

        _proc, result = _run(test_config, mock_services, tmp_path, [taberu, nomu], created=["食べる"])

        assert result.not_mined.forms(NotMinedReason.KNOWN) == {"飲む"}
        assert "食べる" not in result.not_mined.words

    def test_media_definition_and_anki_drops_are_named(self, test_config, mock_services, tmp_path):
        words = [_word(w, start_time=float(i * 3)) for i, w in enumerate(("見る", "書く", "走る", "読む", "食べる"))]
        mock_services["anki_service"].get_existing_vocabulary.return_value = set()
        # 見る: no media. 書く: no definition. 走る: Anki duplicate. 読む: unconfirmed. 食べる: created.
        mock_services["media_extractor"].extract_media_batch.return_value = _media_for(words[1:])
        mock_services["definition_service"].get_definitions_batch.return_value = [None, "1. run", "1. read", "1. eat"]

        _proc, result = _run(
            test_config,
            mock_services,
            tmp_path,
            words,
            created=["食べる"],
            not_created={"走る": "duplicate", "読む": "uncertain"},
        )

        report = result.not_mined
        assert report.forms(NotMinedReason.MEDIA_FAILED) == {"見る"}
        assert report.forms(NotMinedReason.NO_DEFINITION) == {"書く"}
        assert report.forms(NotMinedReason.ANKI_DUPLICATE) == {"走る"}
        assert report.forms(NotMinedReason.ANKI_FAILED) == {"読む"}
        assert "食べる" not in report.words

    def test_a_cancelled_run_keeps_the_drops_it_established(self, test_config, mock_services, tmp_path):
        taberu, nomu = _word("食べる"), _word("飲む", start_time=5.0)
        mock_services["anki_service"].get_existing_vocabulary.return_value = {"飲む"}

        _proc, result = _run(test_config, mock_services, tmp_path, [taberu, nomu], curation_callback=lambda words: None)

        assert result.not_mined is not None
        assert result.not_mined.forms(NotMinedReason.KNOWN) == {"飲む"}

    def test_refusals_do_not_leak_into_the_next_run(self, test_config, mock_services, tmp_path):
        """Batch shares one AnkiService; run 2 stops at phase 2 and must not
        inherit run 1's refusal."""
        taberu = _word("食べる")
        anki = mock_services["anki_service"]
        anki.get_existing_vocabulary.return_value = set()
        mock_services["media_extractor"].extract_media_batch.return_value = _media_for([taberu])
        mock_services["definition_service"].get_definitions_batch.return_value = ["1. to eat"]
        processor, first = _run(test_config, mock_services, tmp_path, [taberu], not_created={"食べる": "refused"})
        assert first.not_mined.forms(NotMinedReason.ANKI_FAILED) == {"食べる"}

        anki.get_existing_vocabulary.return_value = {"食べる"}
        second = processor.process_episode(tmp_path / "v.mkv", tmp_path / "s.ass")

        assert second.not_mined.forms(NotMinedReason.ANKI_FAILED) == frozenset()
        assert second.not_mined.forms(NotMinedReason.KNOWN) == {"食べる"}

    def test_the_season_mine_pass_reports_only_what_happened_to_its_chosen_words(
        self, test_config, mock_services, tmp_path
    ):
        """The pre-pass already reported this file's parse and filter drops."""
        taberu, nomu = _word("食べる"), _word("飲む", start_time=5.0)
        mock_services["subtitle_parser"].last_parse_rejects = {"ちょっと": NotMinedReason.KANA_ONLY}
        mock_services["anki_service"].get_existing_vocabulary.return_value = {"飲む"}
        mock_services["media_extractor"].extract_media_batch.return_value = _media_for([taberu])
        mock_services["definition_service"].get_definitions_batch.return_value = [None]

        _proc, result = _run(
            test_config, mock_services, tmp_path, [taberu, nomu], curation_callback=fixed_selection([taberu])
        )

        assert result.not_mined.words == {"食べる"}
        assert result.not_mined.forms(NotMinedReason.NO_DEFINITION) == {"食べる"}
