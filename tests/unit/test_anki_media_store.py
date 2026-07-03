"""Tests for anki_media_store streaming / lazy-encode path (OVH-051)."""

import logging
from pathlib import Path
from unittest.mock import MagicMock, patch

from anki_miner.models import CardPayload, MediaData
from anki_miner.services import anki_media_store
from anki_miner.services.anki_media_store import (
    AnkiMediaStore,
    _build_store_media_action,
    _content_addressed_name,
    _stream_encode_chunks,
)

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
