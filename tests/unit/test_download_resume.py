"""Durable partial-download state and the validator rules that guard it (D16-C).

The stakes here are not cosmetic. A resumed download that splices bytes from two
different builds of a dictionary produces a file that passes a length check and
fails everything after it, so every test in ``TestRejection`` is an assertion
that the app threw 580 MB away rather than risk that.
"""

from __future__ import annotations

import hashlib
import json

import pytest

from anki_miner.services import download_resume
from anki_miner.services.download_resume import (
    MANIFEST_VERSION,
    ResumeManifest,
    ResumeState,
    is_identity_encoding,
    parse_content_range,
    safe_key,
    strong_validator,
)

_ETAG = '"abc123"'
_MODIFIED = "Wed, 21 Oct 2026 07:28:00 GMT"
URL = "https://example.com/jmdict.zip"


def _seed(root, key="res", *, body=b"first-half", total=40, etag=_ETAG, last_modified=None):
    """Write a valid partial: ``body`` on disk plus the manifest describing it."""
    state = ResumeState(root, key)
    state.ensure_root()
    state.part_path.write_bytes(body)
    manifest = ResumeManifest(
        url=URL,
        total=total,
        length=len(body),
        sha256=hashlib.sha256(body).hexdigest(),
        etag=etag,
        last_modified=last_modified,
    )
    state.manifest_path.write_text(json.dumps(manifest.to_json()), encoding="utf-8")
    return state


class TestKeys:
    @pytest.mark.parametrize("key", ["resource-dict-jmdict", "ggml-0011aabb", "a", "a.b_c-d"])
    def test_stable_literals_pass(self, key):
        assert safe_key(key) == key

    @pytest.mark.parametrize("key", ["", "..", ".", "a/b", "a\\b", "/abs", "-leading", "x" * 200, "café"])
    def test_a_key_that_could_name_another_file_is_refused(self, key):
        with pytest.raises(ValueError, match="unsafe download resume key"):
            safe_key(key)


class TestValidators:
    def test_a_weak_etag_is_no_validator_at_all(self):
        assert strong_validator('W/"abc"', None) == (None, None)
        assert strong_validator('w/"abc"', None) == (None, None)

    def test_a_strong_etag_survives(self):
        assert strong_validator(_ETAG, None) == (_ETAG, None)

    def test_last_modified_stands_in_when_there_is_no_etag(self):
        assert strong_validator(None, _MODIFIED) == (None, _MODIFIED)
        assert strong_validator('W/"x"', _MODIFIED) == (None, _MODIFIED)

    def test_blank_headers_prove_nothing(self):
        assert strong_validator("  ", "  ") == (None, None)

    @pytest.mark.parametrize("encoding", [None, "", "identity", " IDENTITY "])
    def test_stored_bytes_are_resumable(self, encoding):
        assert is_identity_encoding(encoding) is True

    @pytest.mark.parametrize("encoding", ["gzip", "br", "deflate"])
    def test_encoded_bytes_are_not(self, encoding):
        assert is_identity_encoding(encoding) is False

    @pytest.mark.parametrize(
        "header",
        [None, "", "bytes */600", "items 0-9/600", "bytes 10-5/600", "bytes 0-600/600", "bytes a-b/c"],
    )
    def test_an_unparseable_content_range_is_no_range(self, header):
        assert parse_content_range(header) is None

    def test_a_well_formed_range_yields_its_three_numbers(self):
        assert parse_content_range("bytes 10-39/40") == (10, 39, 40)


class TestManifestLoad:
    def test_a_seeded_partial_loads(self, tmp_path):
        state = _seed(tmp_path)
        manifest = state.load()
        assert manifest is not None
        assert manifest.length == 10
        assert manifest.total == 40
        assert manifest.if_range == _ETAG

    def test_a_future_schema_version_is_refused_not_migrated(self, tmp_path):
        state = _seed(tmp_path)
        raw = json.loads(state.manifest_path.read_text())
        raw["version"] = MANIFEST_VERSION + 1
        state.manifest_path.write_text(json.dumps(raw))
        assert state.load() is None

    def test_an_oversized_manifest_is_refused_without_being_parsed(self, tmp_path):
        state = _seed(tmp_path)
        state.manifest_path.write_text(" " * (download_resume.MANIFEST_MAX_BYTES + 1))
        assert state.load() is None

    def test_undecodable_json_is_refused(self, tmp_path):
        state = _seed(tmp_path)
        state.manifest_path.write_text("{not json")
        assert state.load() is None

    @pytest.mark.parametrize(
        ("field", "value"),
        [
            ("url", 7),
            ("url", ""),
            ("total", "40"),
            ("total", 0),
            ("total", True),
            ("length", -1),
            ("length", 0),
            ("length", 41),  # past the recorded total
            ("length", True),
            ("sha256", "not-a-digest"),
            ("sha256", 12345),
            ("etag", 5),
            ("last_modified", []),
        ],
    )
    def test_every_field_is_type_and_bound_checked(self, tmp_path, field, value):
        state = _seed(tmp_path)
        raw = json.loads(state.manifest_path.read_text())
        raw[field] = value
        state.manifest_path.write_text(json.dumps(raw))
        assert state.load() is None

    def test_a_manifest_with_no_validator_is_worthless(self, tmp_path):
        state = _seed(tmp_path, etag=None)
        raw = json.loads(state.manifest_path.read_text())
        raw["etag"] = None
        raw["last_modified"] = None
        state.manifest_path.write_text(json.dumps(raw))
        assert state.load() is None

    def test_a_weak_etag_stored_by_a_future_bug_still_fails_closed(self, tmp_path):
        state = _seed(tmp_path)
        raw = json.loads(state.manifest_path.read_text())
        raw["etag"] = 'W/"abc123"'
        state.manifest_path.write_text(json.dumps(raw))
        assert state.load() is None

    def test_a_missing_manifest_is_simply_absent(self, tmp_path):
        assert ResumeState(tmp_path, "nothing").load() is None


class TestRestore:
    def test_a_matching_prefix_restores_with_a_live_hasher(self, tmp_path):
        state = _seed(tmp_path, body=b"0123456789")
        restored = state.restore(URL)
        assert restored is not None
        assert restored.manifest.length == 10
        # The hasher continues where the prefix left off, so appended bytes need
        # no second read of the megabytes already on disk.
        restored.hasher.update(b"rest")
        assert restored.hasher.hexdigest() == hashlib.sha256(b"0123456789rest").hexdigest()

    def test_bytes_written_after_the_last_checkpoint_are_truncated_away(self, tmp_path):
        state = _seed(tmp_path, body=b"0123456789")
        state.part_path.write_bytes(b"0123456789NEVER-FSYNCED")
        restored = state.restore(URL)
        assert restored is not None
        assert state.part_path.stat().st_size == 10

    def test_a_prefix_that_no_longer_hashes_right_is_discarded(self, tmp_path):
        state = _seed(tmp_path, body=b"0123456789")
        state.part_path.write_bytes(b"XXXXXXXXXX")
        assert state.restore(URL) is None
        assert not state.part_path.exists()
        assert not state.manifest_path.exists()

    def test_a_body_shorter_than_the_manifest_claims_is_discarded(self, tmp_path):
        state = _seed(tmp_path, body=b"0123456789")
        state.part_path.write_bytes(b"012")
        assert state.restore(URL) is None
        assert not state.manifest_path.exists()

    def test_a_missing_body_is_discarded(self, tmp_path):
        state = _seed(tmp_path)
        state.part_path.unlink()
        assert state.restore(URL) is None
        assert not state.manifest_path.exists()

    def test_state_recorded_for_another_url_is_never_reused(self, tmp_path):
        state = _seed(tmp_path)
        assert state.restore("https://example.com/other.zip") is None
        assert not state.part_path.exists()


class TestResponseMatching:
    def test_an_unchanged_etag_matches(self, tmp_path):
        manifest = _seed(tmp_path).load()
        assert manifest is not None
        assert manifest.matches_response(etag=_ETAG, last_modified=None) is True

    def test_a_changed_etag_does_not(self, tmp_path):
        manifest = _seed(tmp_path).load()
        assert manifest is not None
        assert manifest.matches_response(etag='"different"', last_modified=None) is False

    def test_a_weak_etag_on_the_response_is_ignored_rather_than_trusted(self, tmp_path):
        manifest = _seed(tmp_path).load()
        assert manifest is not None
        # Weak reduces to "no validator named", which the Content-Range checks
        # still have to carry — it must not be compared as if it were ours.
        assert manifest.matches_response(etag='W/"abc123"', last_modified=None) is True

    def test_an_etag_appearing_where_we_only_had_a_date_is_refused(self, tmp_path):
        manifest = _seed(tmp_path, etag=None, last_modified=_MODIFIED).load()
        assert manifest is not None
        assert manifest.matches_response(etag='"new"', last_modified=_MODIFIED) is False

    def test_a_moved_last_modified_is_refused(self, tmp_path):
        manifest = _seed(tmp_path, etag=None, last_modified=_MODIFIED).load()
        assert manifest is not None
        assert manifest.matches_response(etag=None, last_modified="Thu, 22 Oct 2026 07:28:00 GMT") is False


class TestCheckpointAtomicity:
    def test_the_body_is_fsynced_before_the_manifest_describes_it(self, tmp_path, monkeypatch):
        order: list[str] = []
        state = ResumeState(tmp_path, "res")
        state.ensure_root()

        real_fsync = download_resume.os.fsync
        monkeypatch.setattr(download_resume.os, "fsync", lambda fd: (order.append("fsync"), real_fsync(fd))[1])
        real_replace = download_resume.os.replace
        monkeypatch.setattr(
            download_resume.os, "replace", lambda a, b: (order.append("manifest"), real_replace(a, b))[1]
        )

        with state.part_path.open("wb") as handle:
            handle.write(b"0123456789")
            state.checkpoint(
                handle,
                url=URL,
                total=40,
                length=10,
                digest=hashlib.sha256(b"0123456789").hexdigest(),
                etag=_ETAG,
                last_modified=None,
            )

        assert order == ["fsync", "manifest"]

    def test_a_checkpoint_replaces_the_manifest_rather_than_editing_it(self, tmp_path):
        state = _seed(tmp_path, body=b"0123456789")
        with state.part_path.open("r+b") as handle:
            handle.seek(0, 2)
            handle.write(b"more")
            state.checkpoint(
                handle,
                url=URL,
                total=40,
                length=14,
                digest=hashlib.sha256(b"0123456789more").hexdigest(),
                etag=_ETAG,
                last_modified=None,
            )
        restored = state.restore(URL)
        assert restored is not None
        assert restored.manifest.length == 14

    def test_a_crash_leaving_the_old_manifest_recovers_to_the_older_prefix(self, tmp_path):
        """The body may lead the manifest; it must never lag it."""
        state = _seed(tmp_path, body=b"0123456789")
        # Simulate: more bytes landed, the process died before the next
        # checkpoint. Recovery keeps the last DESCRIBED prefix and no more.
        with state.part_path.open("ab") as handle:
            handle.write(b"undescribed-tail")
        restored = state.restore(URL)
        assert restored is not None
        assert restored.manifest.length == 10
        assert state.part_path.read_bytes() == b"0123456789"


class TestDiscardAndPromote:
    def test_discard_removes_both_files_and_never_raises(self, tmp_path):
        state = _seed(tmp_path)
        state.discard()
        assert not state.part_path.exists()
        assert not state.manifest_path.exists()
        state.discard()  # idempotent

    def test_promote_moves_the_body_and_forgets_the_manifest(self, tmp_path):
        state = _seed(tmp_path, body=b"complete")
        dest = tmp_path / "out" / "artifact.part"
        assert state.promote(dest) == dest
        assert dest.read_bytes() == b"complete"
        assert not state.part_path.exists()
        assert not state.manifest_path.exists()

    def test_keepable_refuses_a_response_that_proved_nothing(self, tmp_path):
        state = ResumeState(tmp_path, "res")
        assert state.keepable(total=40, etag=_ETAG, last_modified=None) is True
        assert state.keepable(total=40, etag=None, last_modified=_MODIFIED) is True
        assert state.keepable(total=40, etag='W/"weak"', last_modified=None) is False
        assert state.keepable(total=0, etag=_ETAG, last_modified=None) is False


class TestDefaultRoot:
    def test_the_root_follows_the_app_home_at_call_time(self, tmp_path, monkeypatch):
        from anki_miner.config import paths

        monkeypatch.setattr(paths, "ANKI_MINER_HOME", tmp_path / "home")
        assert download_resume.default_resume_root() == tmp_path / "home" / "runtime_state" / "downloads"

    def test_it_is_not_beside_the_settings_file_it_must_never_travel_with(self, tmp_path, monkeypatch):
        from anki_miner.config import paths

        monkeypatch.setattr(paths, "ANKI_MINER_HOME", tmp_path)
        root = download_resume.default_resume_root()
        assert "runtime_state" in root.parts
        assert "profiles" not in root.parts
