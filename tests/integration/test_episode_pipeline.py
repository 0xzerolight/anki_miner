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

    @staticmethod
    def _make_ankiconnect_responder(config, *, post_preflight_side_effect):
        """Return a side_effect callable that dispatches AnkiConnect responses by action.

        Pre-flight actions (modelNames, modelFieldNames, createDeck) are served
        from the config so verify_card_target() succeeds.  All subsequent calls
        (findNotes, notesInfo, addNotes, …) are consumed in order from
        *post_preflight_side_effect*, which should be a list of MagicMock
        response objects identical to what the test would have put in
        ``side_effect`` before the pre-flight was added.
        """
        fields = list(config.anki_fields.values())
        _remaining = list(post_preflight_side_effect)

        def _responder(*args, **kwargs):
            payload = args[1] if len(args) > 1 else kwargs.get("json", {})
            action = payload.get("action", "")
            r = MagicMock()
            if action == "modelNames":
                r.json.return_value = {"result": [config.anki_note_type], "error": None}
                return r
            if action == "modelFieldNames":
                r.json.return_value = {"result": fields, "error": None}
                return r
            if action == "createDeck":
                r.json.return_value = {"result": 1, "error": None}
                return r
            return _remaining.pop(0)

        return _responder

    def test_full_pipeline(self, config, tmp_path):
        """Full pipeline: parse → filter → extract → define → create cards."""
        video = tmp_path / "ep01.mkv"
        sub = tmp_path / "ep01.ass"

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
            # Must be set explicitly: an auto-created MagicMock attribute is
            # truthy and would leak into mined_form via extract_orth_base.
            t.feature.orthBase = lemma
            return t

        def tagger_func(text):
            if "食べる" in text:
                return [make_mock_token("食べる", "食べる")]
            elif "走る" in text:
                return [make_mock_token("走る", "走る")]
            return []

        mock_tagger = MagicMock(side_effect=tagger_func)

        # Stub media extraction: write a real screenshot file per word so
        # MediaData.has_screenshot is True and cards reach the addNotes call.
        media_dir = tmp_path / "media"
        media_dir.mkdir(parents=True, exist_ok=True)

        def _fake_extract(video_file, words, *args, **kwargs):
            from anki_miner.models import MediaData

            results = []
            for w in words:
                ss = media_dir / f"{w.lemma}.jpg"
                ss.write_bytes(b"\xff\xd8\xff\xe0fake-jpeg")
                results.append(
                    (
                        w,
                        MediaData(
                            screenshot_path=ss, audio_path=None, screenshot_filename=ss.name, audio_filename=None
                        ),
                    )
                )
            return results

        # Capture addNotes payloads so we can assert what reached AnkiConnect.
        added_notes: list[dict] = []

        # Dispatch AnkiConnect responses by action name (order-independent):
        # pre-flight + empty vocab + media upload (multi/storeMediaFile) + addNotes.
        fields = list(config.anki_fields.values())

        def _anki_responder(*args, **kwargs):
            payload = args[1] if len(args) > 1 else kwargs.get("json", {})
            action = payload.get("action", "")
            params = payload.get("params", {})
            r = MagicMock()
            if action == "modelNames":
                r.json.return_value = {"result": [config.anki_note_type], "error": None}
            elif action == "modelFieldNames":
                r.json.return_value = {"result": fields, "error": None}
            elif action == "createDeck":
                r.json.return_value = {"result": 1, "error": None}
            elif action == "findNotes":
                r.json.return_value = {"result": [], "error": None}
            elif action == "storeMediaFile":
                r.json.return_value = {"result": "stored.jpg", "error": None}
            elif action == "multi":
                # Media upload envelope: one result per sub-action.
                sub = params.get("actions", [])
                r.json.return_value = {"result": ["stored.jpg"] * len(sub), "error": None}
            elif action == "canAddNotesWithErrorDetail":
                # Pre-add duplicate probe: report every note as addable.
                probe_notes = params.get("notes", [])
                r.json.return_value = {
                    "result": [{"canAdd": True, "error": None} for _ in probe_notes],
                    "error": None,
                }
            elif action == "addNotes":
                notes = params.get("notes", [])
                added_notes.extend(notes)
                r.json.return_value = {"result": list(range(1, len(notes) + 1)), "error": None}
            else:
                r.json.return_value = {"result": None, "error": None}
            return r

        with (
            patch("anki_miner.services.subtitle_parser.pysubs2.load", return_value=mock_subs),
            patch("anki_miner.services.subtitle_parser.get_shared_tagger", return_value=mock_tagger),
            patch(
                "anki_miner.services.media_extractor.MediaExtractorService.extract_media_batch",
                side_effect=_fake_extract,
            ),
            patch(
                "anki_miner.services.definition_service.DefinitionService.get_definitions_batch",
                side_effect=lambda words, *a, **kw: ["a definition"] * len(words),
            ),
            patch(
                "anki_miner.services.definition_service.DefinitionService.has_offline_definitions",
                side_effect=lambda lemmas: dict.fromkeys(lemmas, True),
            ),
            patch(
                "anki_miner.services.definition_service.DefinitionService.get_glossaries_batch",
                side_effect=lambda words, *a, **kw: [None] * len(words),
            ),
            patch("anki_miner.services.anki_service.requests.post", side_effect=_anki_responder),
        ):

            # Build services with real instances
            subtitle_parser = SubtitleParserService(config)
            word_filter = WordFilterService(config)
            media_extractor = MediaExtractorService(config)
            definition_service = DefinitionService(config, providers=[])
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

        # Two unknown verbs (食べる, 走る) are parsed, both new, both carded.
        assert result.total_words_found == 2
        assert result.new_words_found == 2
        assert result.cards_created == 2
        assert result.elapsed_time > 0
        # addNotes actually fired with both mined words.
        assert {n["fields"]["word"] for n in added_notes} == {"食べる", "走る"}

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
        mock_token.feature.orthBase = "食べる"

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
            patch("anki_miner.services.subtitle_parser.get_shared_tagger", return_value=mock_tagger),
            patch("anki_miner.services.media_extractor.subprocess.run") as mock_subprocess,
            patch("anki_miner.services.media_extractor.subprocess.Popen") as mock_popen,
            patch("anki_miner.services.media_extractor.ensure_directory"),
            patch(
                "anki_miner.services.anki_service.requests.post",
                side_effect=self._make_ankiconnect_responder(
                    config, post_preflight_side_effect=[find_resp, notes_resp]
                ),
            ),
        ):

            subtitle_parser = SubtitleParserService(config)
            word_filter = WordFilterService(config)
            media_extractor = MediaExtractorService(config)
            definition_service = DefinitionService(config, providers=[])
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
        mock_popen.assert_not_called()


class TestIPlusOneFilterIntegration:
    """End-to-end checks for use_i_plus_one_filter through the real parser + filter."""

    @pytest.fixture
    def base_config(self, tmp_path):
        """Minimal config with all optional lookups disabled.

        The i+1 path needs nothing more than the parser and the word filter —
        no frequency list, no JMdict, no pitch CSV, no blacklist/whitelist,
        no known-words DB, and no sentence dedup (so we can see the legacy
        first-wins sentence pick when the flag is off).
        """
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
            use_blacklist=False,
            use_whitelist=False,
            use_known_words_db=False,
            deduplicate_sentences=False,
        )

    def _write_srt(self, path, lines):
        """Write a minimal .srt file. lines is [(start_sec, end_sec, text), ...]."""

        def _ts(sec):
            ms = int(round(sec * 1000))
            h, rem = divmod(ms, 3_600_000)
            m, rem = divmod(rem, 60_000)
            s, ms = divmod(rem, 1000)
            return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"

        blocks = []
        for i, (start, end, text) in enumerate(lines, start=1):
            blocks.append(f"{i}\n{_ts(start)} --> {_ts(end)}\n{text}\n")
        path.write_text("\n".join(blocks), encoding="utf-8")

    def _make_anki_post_mock(self, config, known_words):
        """Build a requests.post side_effect that fakes AnkiConnect responses.

        Action-dispatching (not ordered): serves the always-on pre-flight
        (modelNames / modelFieldNames / createDeck), the known-words query
        (findNotes returns a synthetic id per known word, notesInfo the fields),
        and harmless successes for anything else — the capture path cancels at
        curation, before card creation.
        """
        fields = list(config.anki_fields.values())

        def _responder(*args, **kwargs):
            payload = args[1] if len(args) > 1 else kwargs.get("json", {})
            action = payload.get("action", "")
            r = MagicMock()
            if action == "modelNames":
                r.json.return_value = {"result": [config.anki_note_type], "error": None}
            elif action == "modelFieldNames":
                r.json.return_value = {"result": fields, "error": None}
            elif action == "findNotes":
                r.json.return_value = {"result": list(range(1, len(known_words) + 1)), "error": None}
            elif action == "notesInfo":
                r.json.return_value = {
                    "result": [{"fields": {"word": {"value": w}}} for w in known_words],
                    "error": None,
                }
            else:
                r.json.return_value = {"result": 1, "error": None}
            return r

        return _responder

    def _run_capture(self, config, srt_path, known_words):
        """Run process_episode and capture the post-filter mining set.

        A curation callback that returns ``None`` (a user cancel) records the
        words it was offered and stops the run before phase 3 — exactly the
        stop-after-filter semantics the old preview path had, including i+1
        swaps when the flag is on.
        """
        captured: list[list] = []

        def _capture_and_cancel(words):
            captured.append(list(words))
            return None

        with (
            patch(
                "anki_miner.services.anki_service.requests.post",
                side_effect=self._make_anki_post_mock(config, known_words),
            ),
            patch(
                "anki_miner.services.definition_service.DefinitionService.has_offline_definitions",
                side_effect=lambda lemmas: dict.fromkeys(lemmas, True),
            ),
        ):
            subtitle_parser = SubtitleParserService(config)
            word_filter = WordFilterService(config)
            media_extractor = MediaExtractorService(config)
            definition_service = DefinitionService(config, providers=[])
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

            video = srt_path.with_suffix(".mkv")
            processor.process_episode(video, srt_path, curation_callback=_capture_and_cancel)

        # The curation callback is offered the post-filter set exactly once,
        # even when empty... unless the filter left nothing (no callback then),
        # which none of these tests exercise.
        assert len(captured) == 1
        return captured[0]

    def test_i_plus_one_filter_changes_sentence_selection(self, base_config, tmp_path):
        """Flag on swaps 難しい from the i+2 L2 to the i+1 L3 sentence."""
        from dataclasses import replace

        srt = tmp_path / "ep.srt"
        self._write_srt(
            srt,
            [
                (0.0, 1.0, "新しい単語が出た"),
                (1.0, 2.0, "新しい難しい単語"),
                (2.0, 3.0, "難しい言葉だ"),
            ],
        )
        # Mark 単語, 出る, 言葉 as already-known so mineable = {新しい, 難しい}.
        known = {"単語", "出る", "言葉"}

        config_off = replace(base_config, use_i_plus_one_filter=False)
        words_off = self._run_capture(config_off, srt, known)

        config_on = replace(base_config, use_i_plus_one_filter=True)
        words_on = self._run_capture(config_on, srt, known)

        by_lemma_off = {w.lemma: w for w in words_off}
        by_lemma_on = {w.lemma: w for w in words_on}

        # Both runs surface the same two mineable lemmas.
        assert set(by_lemma_off) == {"新しい", "難しい"}
        assert set(by_lemma_on) == {"新しい", "難しい"}

        # 新しい: parser first-wins → L1 in both cases (L1 is also the i+1 line).
        assert by_lemma_off["新しい"].sentence == "新しい単語が出た"
        assert by_lemma_on["新しい"].sentence == "新しい単語が出た"

        # 難しい: parser first-wins → L2 (i+2). i+1 filter swaps to L3.
        assert by_lemma_off["難しい"].sentence == "新しい難しい単語"
        assert by_lemma_on["難しい"].sentence == "難しい言葉だ"

        # And timing rides along with the swap.
        assert by_lemma_on["難しい"].start_time == pytest.approx(2.0)
        assert by_lemma_on["難しい"].end_time == pytest.approx(3.0)

    def test_i_plus_one_filter_drops_word(self, base_config, tmp_path):
        """Words with no i+1 coverage are dropped; surviving words remain."""
        from dataclasses import replace

        srt = tmp_path / "ep.srt"
        self._write_srt(
            srt,
            [
                (0.0, 1.0, "珍しい単語と言葉"),
                (1.0, 2.0, "珍しい本だ"),
                (2.0, 3.0, "言葉が好き"),
            ],
        )
        # 単語 and 好き are known → mineable = {珍しい, 言葉, 本}.
        # L1 ∩ mineable = {珍しい, 言葉} (i+2)
        # L2 ∩ mineable = {珍しい, 本}    (i+2)
        # L3 ∩ mineable = {言葉}          (i+1)
        # → 言葉 kept (L3); 珍しい and 本 dropped (no i+1 coverage).
        known = {"単語", "好き"}

        config_on = replace(base_config, use_i_plus_one_filter=True)
        words_on = self._run_capture(config_on, srt, known)

        by_lemma_on = {w.lemma: w for w in words_on}
        assert "言葉" in by_lemma_on
        assert "珍しい" not in by_lemma_on
        assert "本" not in by_lemma_on
        assert by_lemma_on["言葉"].sentence == "言葉が好き"
