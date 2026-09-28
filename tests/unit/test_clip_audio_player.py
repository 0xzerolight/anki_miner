"""Tests for the audio-only clip player the Word Curator uses on Anki-deck runs."""

from __future__ import annotations

from unittest.mock import MagicMock

from anki_miner.gui.widgets import clip_audio_player
from anki_miner.gui.widgets.clip_audio_player import ClipAudioPlayer


def test_core_is_built_audio_only_on_first_play(tmp_path):
    core = MagicMock(name="mpv")
    calls: list[dict] = []
    player = ClipAudioPlayer(factory=lambda **kw: calls.append(kw) or core)

    player.stop()  # nothing built yet: a no-op
    assert calls == []

    player.play(tmp_path / "a.mp3")
    player.play(tmp_path / "b.mp3")

    assert calls == [{"video": False}]  # one core, reused
    assert [c.args for c in core.loadfile.call_args_list] == [(str(tmp_path / "a.mp3"),), (str(tmp_path / "b.mp3"),)]
    assert core.pause is False


def test_stop_pauses_the_core(tmp_path):
    core = MagicMock(name="mpv")
    player = ClipAudioPlayer(factory=lambda **kw: core)
    player.play(tmp_path / "a.mp3")

    player.stop()

    assert core.pause is True


def test_release_terminates_once(tmp_path, monkeypatch):
    core = MagicMock(name="mpv")
    terminated: list[object] = []
    monkeypatch.setattr(clip_audio_player, "terminate_mpv_player", lambda p: terminated.append(p) or True)
    player = ClipAudioPlayer(factory=lambda **kw: core)
    player.release()  # never played: nothing to terminate
    player.play(tmp_path / "a.mp3")

    player.release()
    player.release()

    assert terminated == [core]
