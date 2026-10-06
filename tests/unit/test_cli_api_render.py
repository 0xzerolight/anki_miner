"""--api render: phase 5's notes and media written to the run folder instead of Anki."""

from __future__ import annotations

import hashlib
from dataclasses import replace

from anki_miner.cli.api import render
from anki_miner.models import CardPayload, MediaData
from tests.unit.test_cli_api_lines import _word


def _payload(tmp_path, front: str) -> CardPayload:
    picture = tmp_path / f"{front}_1_0.jpg"
    picture.write_bytes(b"jpeg " + front.encode())
    return CardPayload(
        word=_word(front, 0),
        media=MediaData(screenshot_path=picture, screenshot_filename=picture.name),
        definition="a promise",
    )


def test_render_writes_the_notes_fields_and_files_and_nothing_to_anki(tmp_path, test_config, monkeypatch) -> None:
    config = replace(
        test_config,
        anki_fields={**test_config.anki_fields, "word": "Word", "picture": "Picture", "definition": "Meaning"},
    )
    out = tmp_path / "render-1"
    service = render.RenderService(config, out)

    def no_anki(*args, **kwargs):
        raise AssertionError("render called AnkiConnect")

    # Every AnkiService and AnkiMediaStore request goes through this transport.
    monkeypatch.setattr("anki_miner.services._ankiconnect._post", no_anki)
    assert service.create_cards_batch([_payload(tmp_path, "約束")]) == []
    rendered = service.rendered["約束"]
    digest = hashlib.sha1(b"jpeg " + "約束".encode()).hexdigest()[:12]
    assert rendered.files == [f"約束_1_0_{digest}.jpg"]
    assert (out / rendered.files[0]).read_bytes() == b"jpeg " + "約束".encode()
    assert rendered.fields["Word"] == "約束" and rendered.fields["Picture"] == f'<img src="{rendered.files[0]}">'
    assert service.last_created_mined_forms == [] and service.last_media_store_failures == 0


def test_a_vanished_file_is_a_store_failure_not_a_field(tmp_path, test_config) -> None:
    config = replace(test_config, anki_fields={**test_config.anki_fields, "picture": "Picture"})
    payload = _payload(tmp_path, "約束")
    payload.media.screenshot_path.unlink()
    service = render.RenderService(config, tmp_path / "render-1")
    service.create_cards_batch([payload])
    assert service.rendered["約束"].files == [] and service.last_media_store_failures == 1
