"""A11, A20, D6 item 2 on Video → YouTube."""

from __future__ import annotations

from dataclasses import replace

import pytest

pytest.importorskip("PyQt6.QtWidgets")

from PyQt6.QtWidgets import QLabel


def test_the_instruction_lives_in_the_placeholder(queue_youtube_tab):
    tab = queue_youtube_tab
    assert tab.url_edit.placeholderText() == "Paste YouTube links or playlists, one per line, then click Mine"
    assert tab.empty_label is None


def test_no_heading_repeats_the_tab_name(queue_youtube_tab):
    texts = [label.text() for label in queue_youtube_tab.findChildren(QLabel)]
    assert "YouTube queue" not in texts


def test_align_captions_left_the_screen(queue_youtube_tab):
    assert not hasattr(queue_youtube_tab, "align_captions_checkbox")


def test_the_worker_takes_alignment_from_the_config(youtube_tab):
    from anki_miner.gui.widgets import youtube_tab as module

    tab = youtube_tab
    tab.config = replace(tab.config, youtube_align_captions=True)

    tab._make_worker([], None, None)

    assert module.YouTubeQueueWorker.call_args.kwargs["align_captions"] is True
