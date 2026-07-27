"""The one Restore/Discard question, and what each answer actually does (D16-C).

The inventory reports only what is on disk. Restore keeps it — the resume leg
still has to prove the artifact is unchanged when it next asks the server, so
"Restore" is never a promise that the bytes will be used. Discard deletes, and
only ever beneath the two runtime-state roots.
"""

from __future__ import annotations

import hashlib
import json

import pytest
from PyQt6.QtWidgets import QMessageBox

from anki_miner.gui.controllers import recovery_controller
from anki_miner.gui.controllers.recovery_controller import (
    MIN_OFFERED_BYTES,
    RecoveryController,
    RecoveryInventory,
    describe,
    format_bytes,
    take_inventory,
)
from anki_miner.gui.utils import queue_state_store, runtime_state
from anki_miner.gui.utils.config_manager import GUIConfigManager
from anki_miner.gui.utils.queue_state_store import QueueItemSnapshot, QueueSnapshot
from anki_miner.services.download_resume import ResumeManifest, ResumeState

URL = "https://example.com/jmdict.zip"


@pytest.fixture()
def _home(tmp_path, monkeypatch):
    from anki_miner.config import paths

    home = tmp_path / "home"
    monkeypatch.setattr(GUIConfigManager, "CONFIG_FILE", home / "gui_config.json")
    monkeypatch.setattr(paths, "ANKI_MINER_HOME", home)
    return home


def _seed_partial(key="resource-dict-jmdict", *, saved=4 * 1024 * 1024, total=8 * 1024 * 1024, url=URL):
    state = ResumeState(runtime_state.download_resume_root(), key)
    state.ensure_root()
    body = b"x" * saved
    state.part_path.write_bytes(body)
    manifest = ResumeManifest(
        url=url,
        total=total,
        length=saved,
        sha256=hashlib.sha256(body).hexdigest(),
        etag='"v1"',
        last_modified=None,
    )
    state.manifest_path.write_text(json.dumps(manifest.to_json()), encoding="utf-8")
    return state


def _seed_queue(key="queue.youtube", count=3):
    items = tuple(
        QueueItemSnapshot(
            item_id=f"row-{i}",
            source=queue_state_store.url_source(f"https://youtu.be/{i}", title=f"Ep {i}"),
            title=f"Ep {i}",
        )
        for i in range(count)
    )
    queue_state_store.save(QueueSnapshot(key=key, items=items))


class TestInventory:
    def test_a_fresh_home_offers_nothing(self, _home):
        inventory = take_inventory()
        assert not inventory
        assert inventory.downloads == ()
        assert inventory.queues == ()

    def test_a_kept_partial_is_reported_with_its_durable_size(self, _home):
        _seed_partial()
        inventory = take_inventory()
        assert bool(inventory)
        (download,) = inventory.downloads
        assert download.saved_bytes == 4 * 1024 * 1024
        assert download.total_bytes == 8 * 1024 * 1024
        assert download.url == URL

    def test_bytes_past_the_last_checkpoint_are_not_counted(self, _home):
        """The manifest is the authority; unfsynced tail bytes are not durable."""
        state = _seed_partial()
        with state.part_path.open("ab") as handle:
            handle.write(b"y" * 4096)
        (download,) = take_inventory().downloads
        assert download.saved_bytes == 4 * 1024 * 1024

    def test_a_partial_too_small_to_be_worth_asking_about_is_skipped(self, _home):
        _seed_partial(saved=MIN_OFFERED_BYTES - 1, total=MIN_OFFERED_BYTES * 4)
        assert take_inventory().downloads == ()

    def test_a_partial_with_no_readable_manifest_is_not_offered(self, _home):
        state = _seed_partial()
        state.manifest_path.write_text("{not json}")
        assert take_inventory().downloads == ()

    def test_a_manifest_with_no_body_is_not_offered(self, _home):
        state = _seed_partial()
        state.part_path.unlink()
        assert take_inventory().downloads == ()

    def test_stored_queues_are_reported_with_their_row_counts(self, _home):
        _seed_queue(count=200)
        inventory = take_inventory()
        (snapshot,) = inventory.queues
        assert len(snapshot.items) == 200
        assert inventory.queued_items == 200

    def test_every_queue_is_reported_not_just_the_first(self, _home):
        _seed_queue("queue.youtube", count=2)
        _seed_queue("queue.audiobook", count=5)
        assert take_inventory().queued_items == 7

    def test_taking_stock_mutates_nothing(self, _home):
        state = _seed_partial()
        _seed_queue()
        before = sorted(p.name for p in runtime_state.runtime_state_root().rglob("*"))
        take_inventory()
        assert sorted(p.name for p in runtime_state.runtime_state_root().rglob("*")) == before
        assert state.part_path.exists()


class TestWording:
    def test_the_offer_names_the_artifact_and_the_size(self, _home):
        _seed_partial()
        text = describe(take_inventory())
        assert "jmdict.zip" in text
        assert "4 MB" in text

    def test_the_queue_line_states_a_count_it_actually_has(self, _home):
        _seed_queue(count=200)
        assert "200 items" in describe(take_inventory())

    def test_the_offer_never_shows_a_raw_url(self, _home):
        _seed_partial(url="https://cdn.example.com/some/long/path/jmdict.zip")
        assert "https://" not in describe(take_inventory())

    @pytest.mark.parametrize(
        ("count", "expected"),
        [(2048, "2 KB"), (5 * 1024 * 1024, "5 MB"), (int(2.5 * 1024**3), "2.5 GB"), (10, "1 KB")],
    )
    def test_sizes_read_the_way_the_sentence_needs_them(self, count, expected):
        assert format_bytes(count) == expected


class TestDiscard:
    def test_discard_removes_both_kinds_of_state(self, _home):
        state = _seed_partial()
        _seed_queue()
        recovery_controller.discard_all()
        assert not state.part_path.exists()
        assert not state.manifest_path.exists()
        assert queue_state_store.stored_keys() == ()
        assert not take_inventory()

    def test_discard_never_reaches_above_the_runtime_state_roots(self, _home, tmp_path):
        outsider = tmp_path / "precious.txt"
        outsider.write_text("keep me")
        _seed_partial()
        # A symlink inside the downloads root pointing outside it must not be
        # followed into a deletion of the target.
        link = runtime_state.download_resume_root() / "escape.part"
        link.symlink_to(outsider)
        recovery_controller.discard_all()
        assert outsider.exists()

    def test_discard_on_an_empty_home_is_a_no_op(self, _home):
        recovery_controller.discard_all()
        assert not take_inventory()


def _press(box, role):
    """Click the box's button with ``role``, so ``clickedButton()`` is real."""
    for button in box.buttons():
        if box.buttonRole(button) == role:
            button.click()
            return
    raise AssertionError(f"no button with role {role}")


class TestPrompt:
    def test_an_empty_inventory_asks_nothing(self, _home, qtbot):
        controller = RecoveryController()
        assert controller.offer(RecoveryInventory()) is False
        assert controller.asked is False

    def test_restore_is_reported_and_the_question_is_asked_once(self, _home, qtbot, monkeypatch):
        _seed_queue()
        opened: list[str] = []

        def _click_restore(box):
            opened.append(box.text())
            _press(box, QMessageBox.ButtonRole.AcceptRole)

        monkeypatch.setattr(recovery_controller.QMessageBox, "exec", lambda self: _click_restore(self))
        controller = RecoveryController()
        assert controller.offer(take_inventory()) is True
        assert controller.asked is True
        assert len(opened) == 1
        # A second call must not put the question again.
        assert controller.offer(take_inventory()) is False
        assert len(opened) == 1

    def test_discard_is_reported(self, _home, qtbot, monkeypatch):
        _seed_queue()

        def _click_discard(box):
            _press(box, QMessageBox.ButtonRole.DestructiveRole)

        monkeypatch.setattr(recovery_controller.QMessageBox, "exec", lambda self: _click_discard(self))
        assert RecoveryController().offer(take_inventory()) is False
