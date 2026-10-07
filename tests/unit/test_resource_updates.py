"""Finding updatable resources, checking publishers, re-validating, and the weekly stamp."""

from __future__ import annotations

import json
import os
import time
from dataclasses import replace
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import requests

from anki_miner.config import ChainEntry, FreqEntry, PitchSourceEntry
from anki_miner.services import resource_updates as ru
from anki_miner.services._sqlite_index import write_ownership_marker
from tests.fixtures.dictionary.build_yomitan_fixture import build_yomitan_zip

INDEX = "https://example.test/index.json"
ZIP = "https://example.test/dict.zip"


def _seed(
    root: Path,
    slot_id: str,
    family: str = "dictionary",
    *,
    language: str = "ja",
    title: str = "Jitendex.org [2026-09-01]",
    revision: str = "2026.09.01.0",
    index_url: str = INDEX,
    download_url: str = ZIP,
    updatable: object = True,
    meta: dict | None = None,
) -> Path:
    slot = root / slot_id
    slot.mkdir(parents=True)
    write_ownership_marker(slot, slot_id, family)  # type: ignore[arg-type]
    (slot / "meta.json").write_text(json.dumps({"language": language, **(meta or {})}), encoding="utf-8")
    extra: dict = {"indexUrl": index_url, "downloadUrl": download_url}
    if updatable is not None:
        extra["isUpdatable"] = updatable
    build_yomitan_zip(slot / "source.zip", title=title, revision=revision, index_extra=extra)
    return slot


@pytest.fixture
def config(test_config, tmp_path):
    return replace(
        test_config,
        language="ja",
        dicts_root=tmp_path / "dicts",
        freqs_root=tmp_path / "freqs",
        pitch_root=tmp_path / "pitch",
        dictionary_chain=(
            ChainEntry(kind="indexed", dict_id="j", enabled=True),
            ChainEntry(kind="jisho", dict_id=None, enabled=False),
        ),
        frequency_chain=(FreqEntry(source_id="jiten", enabled=True),),
        pitch_chain=(PitchSourceEntry(source_id="p", enabled=False),),
    )


def _resource(**kwargs) -> ru.UpdatableResource:
    base = {
        "kind": "dict",
        "slot_id": "j",
        "title": "Jitendex.org [2026-09-01]",
        "revision": "2026.09.01.0",
        "index_url": INDEX,
        "download_url": ZIP,
    }
    base.update(kwargs)
    return ru.UpdatableResource(**base)


def _update(**kwargs) -> ru.ResourceUpdate:
    return ru.ResourceUpdate(_resource(**kwargs), "New", "2026.10.03.0", ZIP)


@pytest.mark.parametrize(
    ("current", "latest", "newer"),
    [
        ("2026.10.03.0", "2026.10.04.0", True),
        ("2026.10.04.0", "2026.10.03.0", False),
        ("2026.10.03.0", "2026.10.03.0", False),
        ("4.7", "4.10", True),  # dotted integers compare as numbers
        ("JMdict.2026-10-06", "JMdict.2026-10-07", True),  # anything else as text
        ("kanjidic2.2026-093", "kanjidic2.2026-280", True),
        ("Jiten 26-09-21", "Jiten 26-10-02", True),
        ("1.0", "1.0.1", True),  # unequal part counts fall back to text
        ("", "2026-10-07", True),
        ("٩", "١٠", False),  # non-ASCII digits are text, as in Yomitan's ASCII \d
    ],
)
def test_is_newer_revision_follows_yomitan(current, latest, newer):
    assert ru.is_newer_revision(current, latest) is newer


def test_finds_each_updatable_slot_in_the_active_chains(config):
    _seed(config.dicts_root, "j")
    _seed(config.freqs_root, "jiten", "frequency", title="Jiten", revision="Jiten 26-09-21")
    _seed(config.pitch_root, "p", "pitch", title="Pitch", revision="1")
    found = ru.updatable_resources(config)
    assert [(r.kind, r.slot_id, r.revision, r.lemmatised) for r in found] == [
        ("dict", "j", "2026.09.01.0", False),
        ("freq", "jiten", "Jiten 26-09-21", False),
        ("pitch", "p", "1", False),
    ]


def test_a_lemmatised_frequency_list_stays_lemmatised(config):
    _seed(config.freqs_root, "jiten", "frequency", title="Jiten", meta={"lemmatised": "1"})
    (resource,) = ru.updatable_resources(config)
    assert resource.lemmatised is True
    assert ru.ResourceUpdate(resource, "Jiten", "2", ZIP).to_spec().lemmatise is True


@pytest.mark.parametrize(
    "seed_kwargs",
    [
        {"updatable": None},
        {"updatable": False},
        {"index_url": "http://example.test/index.json"},
        {"download_url": "ftp://example.test/d.zip"},
        {"language": "zh"},  # another mining language: never restamped
    ],
)
def test_skips_slots_that_cannot_update_here(config, seed_kwargs):
    _seed(config.dicts_root, "j", **seed_kwargs)
    assert ru.updatable_resources(config) == []


def test_skips_unowned_and_zipless_slots(config):
    (config.dicts_root / "j").mkdir(parents=True)  # no ownership marker
    slot = _seed(config.freqs_root, "jiten", "frequency")
    (slot / "source.zip").unlink()
    assert ru.updatable_resources(config) == []


def test_still_valid_when_nothing_changed(config):
    updates = (_update(), _update(kind="freq", slot_id="jiten"))
    assert ru.updates_still_valid(updates, checked=config, live=config) == updates


@pytest.mark.parametrize(
    "change",
    [
        {"language": "zh"},
        {"dicts_root": Path("/elsewhere")},
        {"pitch_root": Path("/elsewhere")},
    ],
)
def test_a_language_or_root_change_during_the_check_drops_everything(config, change):
    assert ru.updates_still_valid((_update(),), checked=config, live=replace(config, **change)) == ()


def test_a_slot_removed_during_the_check_is_dropped(config):
    live = replace(config, dictionary_chain=())
    kept = _update(kind="freq", slot_id="jiten")
    assert ru.updates_still_valid((_update(), kept), checked=config, live=live) == (kept,)


def test_a_newer_revision_is_an_update_with_the_published_url():
    remote = {
        "title": "Jitendex.org [2026-10-03]",
        "revision": "2026.10.03.0",
        "downloadUrl": "https://cdn.example.test/new.zip",
    }
    check = ru.check_for_updates([_resource()], fetch=lambda url: remote)
    (update,) = check.updates
    assert (update.latest_title, update.latest_revision, update.download_url) == (
        "Jitendex.org [2026-10-03]",
        "2026.10.03.0",
        "https://cdn.example.test/new.zip",
    )
    assert check.reached and check.failures == ()


@pytest.mark.parametrize("published", [None, "http://cdn.example.test/new.zip"])
def test_a_missing_or_insecure_published_url_falls_back_to_the_saved_one(published):
    remote = {"title": "T", "revision": "2026.10.03.0"}
    if published:
        remote["downloadUrl"] = published
    (update,) = ru.check_for_updates([_resource()], fetch=lambda url: remote).updates
    assert update.download_url == ZIP


@pytest.mark.parametrize("revision", ["2026.09.01.0", "2026.08.01.0"])
def test_a_same_or_older_revision_is_no_update(revision):
    check = ru.check_for_updates([_resource()], fetch=lambda url: {"title": "T", "revision": revision})
    assert check.updates == () and check.reached


def test_one_publisher_down_never_stops_the_rest():
    def fetch(url):
        if url == INDEX:
            raise requests.ConnectionError("offline")
        return {"title": "B", "revision": "2"}

    check = ru.check_for_updates(
        [_resource(), _resource(slot_id="b", index_url="https://b.test/i.json", revision="1")], fetch=fetch
    )
    assert [u.resource.slot_id for u in check.updates] == ["b"]
    assert [(r.slot_id, msg) for r, msg in check.failures] == [("j", "offline")]
    assert check.reached


def test_nobody_reached_is_not_a_completed_check():
    def fetch(url):
        raise requests.Timeout("slow")

    assert ru.check_for_updates([_resource(), _resource(slot_id="b")], fetch=fetch).reached is False


def test_a_published_index_without_revision_is_a_failure():
    check = ru.check_for_updates([_resource()], fetch=lambda url: {"title": "T"})
    assert check.updates == () and len(check.failures) == 1


def test_nothing_updatable_counts_as_reached():
    assert ru.check_for_updates([]).reached is True


def test_cancel_stops_before_the_next_publisher():
    fetch = MagicMock()
    ru.check_for_updates([_resource()], cancelled=lambda: True, fetch=fetch)
    fetch.assert_not_called()


def test_to_spec_rebuilds_the_slot_in_place():
    spec = ru.ResourceUpdate(_resource(kind="freq", slot_id="jiten"), "Jiten", "Jiten 26-10-02", ZIP).to_spec()
    assert (spec.id, spec.kind, spec.display_name, spec.url) == ("jiten", "freq", "Jiten", ZIP)
    assert spec.pin_slot is True and spec.sweep_superseded is False and spec.lemmatise is False


def _response(chunks, *, error=None):
    response = MagicMock()
    response.iter_content.return_value = chunks
    response.raise_for_status.side_effect = error
    return response


def test_fetch_sends_a_user_agent_and_a_timeout():
    with patch(
        "anki_miner.services.resource_updates.requests.get", return_value=_response([b'{"revision": "2"}'])
    ) as get:
        assert ru.fetch_remote_index(INDEX) == {"revision": "2"}
    assert get.call_args.kwargs["timeout"] == (5, 10)
    assert "anki-miner" in get.call_args.kwargs["headers"]["User-Agent"]


@pytest.mark.parametrize("body", [[b"x" * (1024 * 1024 + 1)], [b"[1, 2]"]])
def test_fetch_refuses_an_oversized_or_non_object_index(body):
    response = _response(body)
    with (
        patch("anki_miner.services.resource_updates.requests.get", return_value=response),
        pytest.raises(ValueError),
    ):
        ru.fetch_remote_index(INDEX)
    response.close.assert_called_once()


def test_fetch_raises_an_http_error_and_closes():
    response = _response([], error=requests.HTTPError("404"))
    with (
        patch("anki_miner.services.resource_updates.requests.get", return_value=response),
        pytest.raises(requests.HTTPError),
    ):
        ru.fetch_remote_index(INDEX)
    response.close.assert_called_once()


def test_the_weekly_stamp(tmp_path):
    stamp = tmp_path / "runtime_state" / "resource_update_check"
    assert ru.update_check_due(stamp) is True
    ru.mark_update_checked(stamp)
    assert ru.update_check_due(stamp) is False
    eight_days_ago = time.time() - 8 * 24 * 3600
    os.utime(stamp, (eight_days_ago, eight_days_ago))
    assert ru.update_check_due(stamp) is True
    tomorrow = time.time() + 24 * 3600
    os.utime(stamp, (tomorrow, tomorrow))
    assert ru.update_check_due(stamp) is True  # a clock that went back never blocks forever
