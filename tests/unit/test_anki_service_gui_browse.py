"""D4: "Show in Anki" opens Anki's card browser on exactly the run's notes."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from anki_miner.exceptions import AnkiConnectionError
from anki_miner.services.anki_service import AnkiService


def test_it_asks_anki_to_browse_those_notes(test_config):
    service = AnkiService(test_config)
    with patch("anki_miner.services.anki_service.post_action", return_value=[1, 2]) as post:
        service.gui_browse_notes([101, 202])

    post.assert_called_once_with(
        test_config.ankiconnect_url,
        "guiBrowse",
        params={"query": "nid:101,202"},
        timeout=15,
    )


def test_no_notes_sends_nothing(test_config):
    service = AnkiService(test_config)
    with patch("anki_miner.services.anki_service.post_action") as post:
        service.gui_browse_notes([])

    post.assert_not_called()


def test_an_unreachable_anki_raises(test_config):
    service = AnkiService(test_config)
    with (
        patch(
            "anki_miner.services.anki_service.post_action",
            side_effect=AnkiConnectionError("Cannot connect to AnkiConnect. Is Anki running?"),
        ),
        pytest.raises(AnkiConnectionError),
    ):
        service.gui_browse_notes([7])
