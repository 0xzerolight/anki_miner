"""Tests for anki_media_store streaming / lazy-encode path (OVH-051)."""

import base64
import logging
from dataclasses import replace
from pathlib import Path
from unittest.mock import MagicMock, patch

from anki_miner.models import CardPayload, MediaData
from anki_miner.services import anki_media_store
from anki_miner.services.anki_media_store import (
    AnkiMediaStore,
    _build_store_media_action,
    _content_addressed_name,
    _extract_dict_media_srcs,
    _stream_encode_chunks,
)
from anki_miner.services.anki_note_builder import build_note
from anki_miner.services.dictionary.yomitan_renderer import structured_content_to_html

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _mock_response(result=None, error=None):
    resp = MagicMock()
    resp.json.return_value = {"result": result, "error": error}
    return resp


def _make_files(tmp_path: Path, count: int, size: int = 16) -> list[tuple[str, Path]]:
    """Return (filename, path) pairs for *count* temp files of *size* bytes."""
    pairs = []
    for i in range(count):
        p = tmp_path / f"file_{i}.jpg"
        p.write_bytes(b"x" * size)
        pairs.append((f"file_{i}.jpg", p))
    return pairs


# ---------------------------------------------------------------------------
# TestStreamEncodeChunks (OVH-051) — unit tests for the streaming helper
# ---------------------------------------------------------------------------


class TestStreamEncodeChunks:
    """_stream_encode_chunks encodes lazily and respects count + byte budgets."""

    def test_empty_input_yields_no_chunks(self, tmp_path):
        chunks = list(_stream_encode_chunks([]))
        assert chunks == []

    def test_single_file_yields_one_chunk_with_one_action(self, tmp_path):
        fname, path = _make_files(tmp_path, 1)[0]
        chunks = list(_stream_encode_chunks([(fname, path)]))
        assert len(chunks) == 1
        assert len(chunks[0]) == 1
        orig, stored, action = chunks[0][0]
        assert orig == fname
        # Card media is content-hashed: the sent name carries the sha1 suffix.
        assert stored == _content_addressed_name(fname, path.read_bytes())
        assert stored != fname
        assert action["action"] == "storeMediaFile"
        assert action["params"]["filename"] == stored
        assert "data" in action["params"]

    def test_output_base64_matches_build_store_media_action(self, tmp_path):
        """Encoded data from streaming must be byte-for-byte identical to pre-built."""
        fname, path = _make_files(tmp_path, 1)[0]
        path.write_bytes(b"hello world")

        expected = _build_store_media_action(fname, path)
        assert expected is not None

        chunks = list(_stream_encode_chunks([(fname, path)]))
        assert len(chunks) == 1
        _, _, action = chunks[0][0]
        assert action["params"]["data"] == expected["params"]["data"]

    def test_count_budget_splits_into_multiple_chunks(self, tmp_path):
        """Files exceeding _MEDIA_BATCH_CHUNK per chunk must split."""
        pairs = _make_files(tmp_path, 3)
        with patch("anki_miner.services.anki_media_store._MEDIA_BATCH_CHUNK", 2):
            chunks = list(_stream_encode_chunks(pairs))
        # 3 files, budget=2 → 2 chunks (2 + 1)
        assert len(chunks) == 2
        assert len(chunks[0]) == 2
        assert len(chunks[1]) == 1

    def test_byte_budget_splits_large_files(self, tmp_path):
        """Files whose cumulative base64 size exceeds the byte budget split."""
        # Each file is 300 bytes → ~400 base64 bytes; budget=100 → each file alone.
        pairs = _make_files(tmp_path, 3, size=300)
        with patch("anki_miner.services.anki_media_store._MEDIA_BATCH_MAX_BYTES", 100):
            chunks = list(_stream_encode_chunks(pairs))
        assert len(chunks) == 3
        for chunk in chunks:
            assert len(chunk) == 1

    def test_unreadable_file_is_skipped_with_warning(self, tmp_path, caplog):
        """A file that cannot be read (OSError) is logged and skipped."""
        fname, path = _make_files(tmp_path, 1)[0]
        path.unlink()  # make it unreadable

        with caplog.at_level(logging.WARNING, logger="anki_miner.services.anki_media_store"):
            chunks = list(_stream_encode_chunks([(fname, path)]))

        assert chunks == []
        assert any("Failed" in r.message or "stat" in r.message for r in caplog.records)

    def test_stat_failure_skips_file_with_warning(self, tmp_path, caplog):
        """A file whose stat() raises (e.g. symlink target gone) is skipped."""
        fname = "ghost.jpg"
        path = tmp_path / fname  # never created → stat raises

        with caplog.at_level(logging.WARNING, logger="anki_miner.services.anki_media_store"):
            chunks = list(_stream_encode_chunks([(fname, path)]))

        assert chunks == []
        assert caplog.records  # at least one warning logged


# ---------------------------------------------------------------------------
# TestStoreBatchLazyEncoding (OVH-051) — call-ordering spy
# ---------------------------------------------------------------------------


class TestStoreBatchLazyEncoding:
    """store_batch encodes lazily: chunk-2 files are NOT encoded before chunk-1 is POSTed."""

    def _make_items(self, make_tokenized_word, pairs: list[tuple[str, Path]]) -> list[CardPayload]:
        items = []
        for i, (fname, path) in enumerate(pairs):
            word = make_tokenized_word(lemma=f"word_{i}")
            media = MediaData(screenshot_path=path, screenshot_filename=fname)
            items.append(CardPayload(word=word, media=media, definition=f"def_{i}"))
        return items

    def test_unmapped_media_makes_no_collection_media_request(self, test_config, make_tokenized_word, tmp_path):
        screenshot = tmp_path / "word.jpg"
        screenshot.write_bytes(b"image")
        audio = tmp_path / "word.mp3"
        audio.write_bytes(b"audio")
        fields = dict(test_config.anki_fields)
        fields.update(picture="", audio="")
        config = replace(test_config, anki_fields=fields)
        item = CardPayload(
            word=make_tokenized_word(),
            media=MediaData(
                screenshot_path=screenshot,
                screenshot_filename=screenshot.name,
                audio_path=audio,
                audio_filename=audio.name,
            ),
            definition="def",
        )

        with patch("anki_miner.services._ankiconnect.requests.post") as mock_post:
            store = AnkiMediaStore(config)
            stored = store.store_batch([item])

        assert stored == set()
        assert store.last_store_failures == 0
        mock_post.assert_not_called()

    def test_encoding_is_lazy_across_chunks(self, test_config, make_tokenized_word, tmp_path):
        """With N > chunk_size files, chunk-2 files must NOT be encoded before
        chunk-1's POST fires.  We spy on _build_store_media_action to record
        the order in which filenames are encoded, then interleave it with the
        order in which POST calls happen."""
        # 3 files with budget=1 so each file gets its own chunk.
        pairs = _make_files(tmp_path, 3)
        items = self._make_items(make_tokenized_word, pairs)
        # POST bodies carry the content-hashed (sent) name, not the orig name.
        hashed = {fname: _content_addressed_name(fname, path.read_bytes()) for fname, path in pairs}

        encode_order: list[str] = []
        post_order: list[str] = []  # which filenames were in each POST call

        orig_build = anki_media_store._build_store_media_action

        def spying_build(filename, src_path, content_hash=False):
            encode_order.append(filename)
            return orig_build(filename, src_path, content_hash=content_hash)

        success_resp = _mock_response(result=[None])

        def spying_post(*args, **kwargs):
            json_body = kwargs.get("json", {})
            if json_body.get("action") == "multi":
                for action in json_body["params"]["actions"]:
                    post_order.append(action["params"]["filename"])
            return success_resp

        with (
            patch("anki_miner.services.anki_media_store._MEDIA_BATCH_CHUNK", 1),
            patch("anki_miner.services.anki_media_store._build_store_media_action", side_effect=spying_build),
            patch("anki_miner.services._ankiconnect.requests.post", side_effect=spying_post),
        ):
            store = AnkiMediaStore(test_config)
            store.store_batch(items)

        # file_0 encoded, file_0 POSTed, file_1 encoded, file_1 POSTed, file_2 encoded, file_2 POSTed
        assert encode_order == ["file_0.jpg", "file_1.jpg", "file_2.jpg"]
        assert post_order == [hashed["file_0.jpg"], hashed["file_1.jpg"], hashed["file_2.jpg"]]

        # Key ordering check: file_1 must be encoded AFTER file_0 is POSTed.
        # We verify this by checking that file_1's encode happens AFTER file_0's POST
        # using a combined event log.
        combined: list[tuple[str, str]] = []  # ("encode"|"post", filename)

        orig_build2 = anki_media_store._build_store_media_action

        def spying_build2(filename, src_path, content_hash=False):
            combined.append(("encode", filename))
            return orig_build2(filename, src_path, content_hash=content_hash)

        def spying_post2(*args, **kwargs):
            json_body = kwargs.get("json", {})
            if json_body.get("action") == "multi":
                for action in json_body["params"]["actions"]:
                    combined.append(("post", action["params"]["filename"]))
            return success_resp

        with (
            patch("anki_miner.services.anki_media_store._MEDIA_BATCH_CHUNK", 1),
            patch("anki_miner.services.anki_media_store._build_store_media_action", side_effect=spying_build2),
            patch("anki_miner.services._ankiconnect.requests.post", side_effect=spying_post2),
        ):
            store2 = AnkiMediaStore(test_config)
            # Fresh payloads: the first store_batch mutated `items`' filenames to
            # the hashed names, and re-hashing an already-hashed name would double
            # the suffix.
            store2.store_batch(self._make_items(make_tokenized_word, pairs))

        # Expected interleaving: encode-0, post-0, encode-1, post-1, encode-2, post-2
        assert combined == [
            ("encode", "file_0.jpg"),
            ("post", hashed["file_0.jpg"]),
            ("encode", "file_1.jpg"),
            ("post", hashed["file_1.jpg"]),
            ("encode", "file_2.jpg"),
            ("post", hashed["file_2.jpg"]),
        ]

    def test_output_equivalence_small_fixture(self, test_config, make_tokenized_word, tmp_path):
        """The stored set and POST bodies from the streaming path must be
        byte-for-byte identical to what the pre-encoding path would have built."""
        pairs = _make_files(tmp_path, 3, size=8)
        items = self._make_items(make_tokenized_word, pairs)

        # Capture POST payloads from the streaming path
        captured_payloads: list[dict] = []
        success_resp = _mock_response(result=[None, None, None])

        def capture_post(*args, **kwargs):
            captured_payloads.append(kwargs.get("json", {}))
            return success_resp

        with patch("anki_miner.services._ankiconnect.requests.post", side_effect=capture_post):
            store = AnkiMediaStore(test_config)
            stored = store.store_batch(items)

        hashed = {fname: _content_addressed_name(fname, path.read_bytes()) for fname, path in pairs}
        assert stored == set(hashed.values())
        # The content-hashed name is propagated onto each payload's MediaData.
        for item, (fname, _) in zip(items, pairs, strict=True):
            assert item.media.screenshot_filename == hashed[fname]

        # Verify each action in the POST contains the correct base64 data
        import base64 as _b64

        all_actions = []
        for payload in captured_payloads:
            if payload.get("action") == "multi":
                all_actions.extend(payload["params"]["actions"])

        for fname, path in pairs:
            expected_b64 = _b64.b64encode(path.read_bytes()).decode("utf-8")
            matching = [a for a in all_actions if a["params"]["filename"] == hashed[fname]]
            assert len(matching) == 1, f"Expected exactly one action for {fname}"
            assert matching[0]["params"]["data"] == expected_b64

    def test_duplicate_filenames_encoded_once_with_streaming(self, test_config, make_tokenized_word, tmp_path):
        """A filename shared by N payloads must still be encoded exactly once."""
        cover_path = tmp_path / "cover.jpg"
        cover_path.write_bytes(b"cover-data")

        items = []
        for i in range(3):
            au_path = tmp_path / f"clip_{i}.mp3"
            au_path.write_bytes(b"audio-data")
            media = MediaData(
                screenshot_path=cover_path,
                screenshot_filename="cover.jpg",
                audio_path=au_path,
                audio_filename=f"clip_{i}.mp3",
            )
            items.append(
                CardPayload(
                    word=make_tokenized_word(lemma=f"word_{i}"),
                    media=media,
                    definition=f"def_{i}",
                )
            )

        resp = _mock_response(result=[None, None, None, None])
        build_calls: list[str] = []

        orig_build = anki_media_store._build_store_media_action

        def tracking_build(filename, src_path, content_hash=False):
            build_calls.append(filename)
            return orig_build(filename, src_path, content_hash=content_hash)

        with (
            patch("anki_miner.services.anki_media_store._build_store_media_action", side_effect=tracking_build),
            patch("anki_miner.services._ankiconnect.requests.post", return_value=resp),
        ):
            store = AnkiMediaStore(test_config)
            stored = store.store_batch(items)

        # cover.jpg must be encoded exactly once despite appearing in 3 payloads
        assert build_calls.count("cover.jpg") == 1
        assert len(build_calls) == 4  # cover + 3 audio clips
        cover_hashed = _content_addressed_name("cover.jpg", b"cover-data")
        assert cover_hashed in stored
        # The single content-hashed cover name is propagated onto all 3 payloads.
        for item in items:
            assert item.media.screenshot_filename == cover_hashed


# ---------------------------------------------------------------------------
# TestContentHashNames (7.5) — collision hardening + returned-name adoption
# ---------------------------------------------------------------------------


class TestContentAddressedName:
    """The content-addressing helper itself."""

    def test_same_bytes_same_name(self):
        assert _content_addressed_name("w_5000.mp3", b"abc") == _content_addressed_name("w_5000.mp3", b"abc")

    def test_different_bytes_different_name(self):
        a = _content_addressed_name("w_5000.mp3", b"AAAA")
        b = _content_addressed_name("w_5000.mp3", b"BBBB")
        assert a != b

    def test_preserves_stem_and_extension(self):
        name = _content_addressed_name("w_5000.mp3", b"abc")
        assert name.startswith("w_5000_")
        assert name.endswith(".mp3")
        # sha1 hex truncated to 12 chars between stem and extension.
        digest = name[len("w_5000_") : -len(".mp3")]
        assert len(digest) == 12


class TestContentHashStoreBatch:
    """store_batch content-hashes card media and adopts AnkiConnect's returned name."""

    def _item(self, make_tokenized_word, path: Path, filename: str) -> CardPayload:
        media = MediaData(audio_path=path, audio_filename=filename)
        return CardPayload(word=make_tokenized_word(lemma="w"), media=media, definition="d")

    def test_same_name_different_bytes_get_distinct_stored_names(self, test_config, make_tokenized_word, tmp_path):
        """Two episodes both emit ``w_5000.mp3`` at the same offset but with
        different audio bytes; content-hashing must give them distinct Anki names
        so the second no longer overwrites the first's clip (7.5)."""
        ep_a = tmp_path / "a"
        ep_a.mkdir()
        ep_b = tmp_path / "b"
        ep_b.mkdir()
        (ep_a / "w_5000.mp3").write_bytes(b"AAAA")
        (ep_b / "w_5000.mp3").write_bytes(b"BBBB")

        resp = _mock_response(result=[None])
        with patch("anki_miner.services._ankiconnect.requests.post", return_value=resp):
            store = AnkiMediaStore(test_config)
            item_a = self._item(make_tokenized_word, ep_a / "w_5000.mp3", "w_5000.mp3")
            stored_a = store.store_batch([item_a])
            item_b = self._item(make_tokenized_word, ep_b / "w_5000.mp3", "w_5000.mp3")
            stored_b = store.store_batch([item_b])

        assert item_a.media.audio_filename != item_b.media.audio_filename
        assert stored_a != stored_b
        assert item_a.media.audio_filename in stored_a
        assert item_b.media.audio_filename in stored_b

    def test_returned_name_adopted_when_anki_renames(self, test_config, make_tokenized_word, tmp_path):
        """storeMediaFile returns the name it stored under; when it differs from
        the sent (hashed) name we adopt it onto the payload and the returned set."""
        path = tmp_path / "w_100.mp3"
        path.write_bytes(b"data")
        item = self._item(make_tokenized_word, path, "w_100.mp3")

        resp = _mock_response(result=[{"result": "renamed_by_anki.mp3", "error": None}])
        with patch("anki_miner.services._ankiconnect.requests.post", return_value=resp):
            stored = AnkiMediaStore(test_config).store_batch([item])

        assert item.media.audio_filename == "renamed_by_anki.mp3"
        assert stored == {"renamed_by_anki.mp3"}

    def test_error_subresult_excludes_and_counts_failure(self, test_config, make_tokenized_word, tmp_path):
        """A per-file error sub-result excludes the file and leaves the payload's
        pre-hash name untouched (media dropped by build_note), counted as failure."""
        path = tmp_path / "w_100.mp3"
        path.write_bytes(b"data")
        item = self._item(make_tokenized_word, path, "w_100.mp3")

        resp = _mock_response(result=[{"result": None, "error": "cannot store"}])
        with patch("anki_miner.services._ankiconnect.requests.post", return_value=resp):
            store = AnkiMediaStore(test_config)
            stored = store.store_batch([item])

        assert stored == set()
        assert item.media.audio_filename == "w_100.mp3"  # unchanged (not renamed)
        assert store.last_store_failures == 1


class TestStoreFailureCounting:
    """A collected-but-unreadable file is counted as a store failure so the user
    is warned about the empty media field (not silently undercounted)."""

    def test_unreadable_collected_file_counts_as_failure(self, test_config, make_tokenized_word, tmp_path):
        # File exists on disk (enters paths_by_filename) but cannot be encoded —
        # _build_store_media_action returns None, so it never enters a chunk.
        path = tmp_path / "clip.mp3"
        path.write_bytes(b"data")
        media = MediaData(audio_path=path, audio_filename="clip.mp3")
        item = CardPayload(word=make_tokenized_word(lemma="w"), media=media, definition="d")

        with patch.object(anki_media_store, "_build_store_media_action", return_value=None):
            store = AnkiMediaStore(test_config)
            stored = store.store_batch([item])

        assert stored == set()
        assert store.last_store_failures == 1

    def test_oversized_media_payload_fails_cleanly(
        self, test_config, make_tokenized_word, tmp_path, monkeypatch, caplog
    ):
        path = tmp_path / "clip.mp3"
        path.write_bytes(b"oversized")
        item = CardPayload(
            word=make_tokenized_word(),
            media=MediaData(audio_path=path, audio_filename=path.name),
            definition="d",
        )
        monkeypatch.setattr(anki_media_store, "_MAX_MEDIA_FILE_BYTES", 4, raising=False)

        with (
            caplog.at_level(logging.WARNING, logger="anki_miner.services.anki_media_store"),
            patch("anki_miner.services._ankiconnect.requests.post") as mock_post,
        ):
            store = AnkiMediaStore(test_config)
            stored = store.store_batch([item])

        assert stored == set()
        assert store.last_store_failures == 1
        assert "cap 4" in caplog.text
        mock_post.assert_not_called()


class TestDictMediaSrcUnescaping:
    """A dict-media basename with an HTML-special char (``&``) is stored escaped
    in the rendered ``<img src>`` but must resolve to the raw on-disk file."""

    def test_extract_unescapes_amp(self):
        html_blob = '<img class="anki-miner-dict-media" src="d__a&amp;b.svg">'
        assert _extract_dict_media_srcs(html_blob) == ["d__a&b.svg"]

    def test_escaped_src_resolves_to_on_disk_file(self, test_config, tmp_path, make_tokenized_word):
        from dataclasses import replace

        # On-disk flattened name carries the raw '&'; the renderer HTML-escaped it.
        media_dir = tmp_path / "dicts" / "d" / "media"
        media_dir.mkdir(parents=True)
        (media_dir / "a&b.svg").write_bytes(b"<svg/>")
        config = replace(test_config, dicts_root=tmp_path / "dicts")

        definition = '<img class="anki-miner-dict-media" src="d__a&amp;b.svg">'
        item = CardPayload(word=make_tokenized_word(), media=MediaData(), definition=definition)

        resp = _mock_response(result=["d__a&b.svg"])
        with patch("anki_miner.services._ankiconnect.requests.post", return_value=resp) as mock_post:
            store = AnkiMediaStore(config)
            store.upload_dict_media([item])

        # storeMediaFile shipped under the UNescaped name with the real file bytes.
        payload = mock_post.call_args[1]["json"]
        actions = payload["params"]["actions"]
        assert len(actions) == 1
        assert actions[0]["params"]["filename"] == "d__a&b.svg"
        assert base64.b64decode(actions[0]["params"]["data"]) == b"<svg/>"
        # Cached under the same unescaped key so it never double-misses.
        assert "d__a&b.svg" in store._dict_media_uploaded


class TestDictionaryMediaConfirmedNames:
    def _item(self, make_tokenized_word, src: str) -> CardPayload:
        tag = f'<img class="anki-miner-dict-media" src="{src}">'
        return CardPayload(
            word=make_tokenized_word(),
            media=MediaData(),
            definition=f"definition {tag}",
            extra_fields={"glossary": f"glossary {tag}"},
        )

    def test_anki_rename_rewrites_definition_and_glossary(self, test_config, tmp_path, make_tokenized_word):
        from dataclasses import replace

        media_dir = tmp_path / "dicts" / "d" / "media"
        media_dir.mkdir(parents=True)
        (media_dir / "pitch[1].svg").write_bytes(b"<svg/>")
        config = replace(test_config, dicts_root=tmp_path / "dicts")
        items = [self._item(make_tokenized_word, "d__pitch[1].svg")]

        with patch(
            "anki_miner.services.anki_media_store.post_multi",
            return_value=["d__pitch1.svg"],
        ):
            store = AnkiMediaStore(config)
            store.upload_dict_media(items)

        assert "d__pitch[1].svg" not in items[0].definition
        assert 'src="d__pitch1.svg"' in items[0].definition
        assert items[0].extra_fields is not None
        assert 'src="d__pitch1.svg"' in items[0].extra_fields["glossary"]

    def test_real_renderer_rename_rewrites_img_and_monochrome_mask_in_final_note(
        self, test_config, tmp_path, make_tokenized_word
    ):
        media_dir = tmp_path / "dicts" / "d" / "media"
        media_dir.mkdir(parents=True)
        (media_dir / "pitch[1].svg").write_bytes(b"<svg/>")
        config = replace(test_config, dicts_root=tmp_path / "dicts")
        logical_name = "d__pitch[1].svg"
        actual_name = "d__pitch1.svg"
        rendered = structured_content_to_html(
            {"tag": "img", "path": "pitch[1].svg", "appearance": "monochrome"},
            dict_id="d",
        )
        assert rendered.count(logical_name) == 2
        items = [CardPayload(word=make_tokenized_word(), media=MediaData(), definition=rendered)]

        with patch("anki_miner.services.anki_media_store.post_multi", return_value=[actual_name]):
            AnkiMediaStore(config).upload_dict_media(items)

        final_note = build_note(items[0], config, set()).note
        definition_field = config.anki_fields["definition"]
        assert final_note["fields"][definition_field].count(actual_name) == 2
        assert logical_name not in final_note["fields"][definition_field]

    def test_collision_safe_store_keeps_existing_bytes_and_uses_returned_name(
        self, test_config, tmp_path, make_tokenized_word
    ):
        media_dir = tmp_path / "dicts" / "d" / "media"
        media_dir.mkdir(parents=True)
        (media_dir / "icon.svg").write_bytes(b"new-dictionary-bytes")
        config = replace(test_config, dicts_root=tmp_path / "dicts")
        items = [self._item(make_tokenized_word, "d__icon.svg")]
        media_files = {"d__icon.svg": b"old-user-bytes"}
        captured_actions: list[dict] = []

        def store_without_overwrite(_url, actions, timeout):
            stored_names = []
            for action in actions:
                captured_actions.append(action)
                params = action["params"]
                sent_name = params["filename"]
                actual_name = sent_name
                if sent_name in media_files and params.get("deleteExisting") is False:
                    actual_name = "d__icon_1.svg"
                media_files[actual_name] = base64.b64decode(params["data"])
                stored_names.append(actual_name)
            return stored_names

        with patch("anki_miner.services.anki_media_store.post_multi", side_effect=store_without_overwrite):
            store = AnkiMediaStore(config)
            store.upload_dict_media(items)

        action = captured_actions[0]
        assert action["params"]["deleteExisting"] is False
        assert media_files["d__icon.svg"] == b"old-user-bytes"
        assert media_files["d__icon_1.svg"] == b"new-dictionary-bytes"
        final_note = build_note(items[0], config, set()).note
        definition_field = config.anki_fields["definition"]
        assert 'src="d__icon_1.svg"' in final_note["fields"][definition_field]


class TestStoreFiles:
    """store_files: the path-oriented engine Card Backfill uploads through."""

    def test_returns_the_confirmed_content_hashed_name(self, test_config, tmp_path):
        src = tmp_path / "jpod101_猫_ねこ.mp3"
        src.write_bytes(b"ID3" + b"\x00" * 64)
        store = AnkiMediaStore(test_config)
        with patch.object(store, "_store_media_chunk", lambda chunk: {sent: sent for sent, _a in chunk}):
            result = store.store_files({src.name: src})
        assert list(result) == [src.name]
        # Content-addressed: the stored name is NOT the name we were handed.
        assert result[src.name] != src.name
        assert result[src.name].startswith("jpod101_猫_ねこ_")
        assert result[src.name].endswith(".mp3")

    def test_adopts_a_name_ankiconnect_renamed(self, test_config, tmp_path):
        src = tmp_path / "a.mp3"
        src.write_bytes(b"ID3")
        store = AnkiMediaStore(test_config)
        with patch.object(
            store, "_store_media_chunk", lambda chunk: {sent: "anki-chose-this.mp3" for sent, _a in chunk}
        ):
            assert store.store_files({"a.mp3": src}) == {"a.mp3": "anki-chose-this.mp3"}

    def test_a_rejected_file_is_absent_from_the_result(self, test_config, tmp_path):
        src = tmp_path / "a.mp3"
        src.write_bytes(b"ID3")
        store = AnkiMediaStore(test_config)
        with patch.object(store, "_store_media_chunk", lambda chunk: {}):
            assert store.store_files({"a.mp3": src}) == {}

    def test_an_unreadable_file_is_skipped_not_raised_on(self, test_config, tmp_path):
        missing = tmp_path / "gone.mp3"  # never created
        store = AnkiMediaStore(test_config)
        with patch.object(store, "_store_media_chunk", lambda chunk: {sent: sent for sent, _a in chunk}):
            assert store.store_files({"gone.mp3": missing}) == {}

    def test_empty_input_makes_no_request(self, test_config):
        store = AnkiMediaStore(test_config)
        with patch("anki_miner.services._ankiconnect.requests.post") as post:
            assert store.store_files({}) == {}
        post.assert_not_called()

    def test_store_batch_still_propagates_the_confirmed_name(self, test_config, make_tokenized_word, tmp_path):
        # Regression guard on the extraction: the CardPayload caller keeps its
        # behaviour and now delegates instead of running its own upload loop.
        src = tmp_path / "b.mp3"
        src.write_bytes(b"ID3")
        config = replace(test_config, anki_fields={"expression_audio": "WordAudio"})
        store = AnkiMediaStore(config)
        media = MediaData(expression_audio_path=src, expression_audio_filename="b.mp3")
        payload = CardPayload(word=make_tokenized_word(lemma="w"), media=media, definition="d")
        with patch.object(store, "store_files", lambda paths: {"b.mp3": "b_abc123def456.mp3"}):
            assert store.store_batch([payload]) == {"b_abc123def456.mp3"}
        assert media.expression_audio_filename == "b_abc123def456.mp3"
        assert store.last_store_failures == 0
