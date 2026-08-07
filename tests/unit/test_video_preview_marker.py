"""The crash sentinel that survives a native abort.

The failure it exists for kills the process with no traceback, so everything
here is about the file being on disk at the right moments — nothing else can
report that the GL surface was mid-construction when the process died.
"""

from __future__ import annotations

import json

import pytest

from anki_miner.gui.utils import runtime_state, video_preview


@pytest.fixture(autouse=True)
def _clean_state():
    video_preview._reset_for_tests()
    yield
    video_preview._reset_for_tests()


@pytest.fixture
def anki_home(_isolate_anki_home):
    """The per-test home dir (tests/_home_isolation retargets everything to it)."""
    return _isolate_anki_home


@pytest.fixture
def marker(anki_home):
    """The marker path under the per-test home (never the real ~/.anki_miner)."""
    return runtime_state.video_preview_marker_path()


class TestArm:
    def test_writes_json_under_the_isolated_home(self, marker, anki_home):
        video_preview.arm_crash_marker(platform_name="xcb")
        assert marker.is_file()
        assert anki_home in marker.parents
        payload = json.loads(marker.read_text(encoding="utf-8"))
        assert payload["platform_name"] == "xcb"
        assert isinstance(payload["pid"], int)

    def test_arms_once_per_process(self, marker):
        """One write per process, not per widget: a curator and a subtitle
        viewer in the same session must not each pay an fsync."""
        video_preview.arm_crash_marker(platform_name="first")
        marker.write_text('{"platform_name": "edited"}', encoding="utf-8")
        video_preview.arm_crash_marker(platform_name="second")
        assert json.loads(marker.read_text(encoding="utf-8"))["platform_name"] == "edited"

    def test_creates_the_runtime_state_directory(self, marker):
        assert not marker.parent.exists()
        video_preview.arm_crash_marker()
        assert marker.is_file()

    def test_unwritable_home_does_not_raise(self, marker, monkeypatch):
        """A sentinel we cannot write is a lost diagnostic, never a reason to
        block the widget the user asked for."""

        def boom(*_args, **_kwargs):
            raise OSError("read-only")

        monkeypatch.setattr(video_preview.Path, "mkdir", boom)
        video_preview.arm_crash_marker()  # must not raise


class TestPathPlacement:
    def test_not_inside_the_recovery_discard_roots(self, marker):
        """recovery_controller's Discard deletes everything is_within the
        downloads/ and queues/ roots. A marker in either would be swept away at
        exactly the launch it needed to survive."""
        assert not runtime_state.is_within(marker, runtime_state.download_resume_root())
        assert not runtime_state.is_within(marker, runtime_state.queue_state_root())
        assert runtime_state.is_within(marker, runtime_state.runtime_state_root())


class TestClear:
    def test_clear_removes_it(self, marker):
        video_preview.arm_crash_marker()
        video_preview.clear_crash_marker()
        assert not marker.exists()

    def test_clear_is_idempotent_when_absent(self, marker):
        video_preview.clear_crash_marker()
        video_preview.clear_crash_marker()
        assert not marker.exists()


class TestConsume:
    def test_returns_payload_and_deletes(self, marker):
        video_preview.arm_crash_marker(platform_name="wayland")
        payload = video_preview.consume_crash_marker()
        assert payload is not None
        assert payload["platform_name"] == "wayland"
        assert not marker.exists()

    def test_returns_none_when_absent(self, marker):
        assert video_preview.consume_crash_marker() is None

    def test_consumed_only_once(self, marker):
        video_preview.arm_crash_marker()
        assert video_preview.consume_crash_marker() is not None
        assert video_preview.consume_crash_marker() is None

    def test_corrupt_json_still_reports_and_deletes(self, marker):
        """The file's EXISTENCE is the signal; its JSON is only the detail line.
        A half-written marker (the process died mid-fsync) must still count as a
        crash and must never wedge boot."""
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text('{"pid": 12', encoding="utf-8")
        payload = video_preview.consume_crash_marker()
        assert payload is not None
        assert '{"pid": 12' in payload["detail"]
        assert not marker.exists()

    def test_non_dict_json_is_normalised(self, marker):
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text("[1, 2, 3]", encoding="utf-8")
        payload = video_preview.consume_crash_marker()
        assert payload is not None
        assert "detail" in payload
        assert not marker.exists()
