from __future__ import annotations

import json
import logging
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Iterator
from unittest.mock import MagicMock
from zipfile import ZipFile

import pytest
import requests

from anki_miner.config import AnkiMinerConfig, AudioSourceEntry, ChainEntry
from anki_miner.diagnostics import bundle
from anki_miner.diagnostics.bundle import collect_log_members, default_bundle_name, write_diagnostics_bundle
from anki_miner.diagnostics.environment import EnvironmentSnapshot
from anki_miner.services.custom_audio_fetcher import CustomAudioFetcher


class SpySinkHandler(RotatingFileHandler):
    def __init__(self, path: Path) -> None:
        self.lock_events: list[str] = []
        super().__init__(path, maxBytes=1024, backupCount=5, encoding="utf-8")
        self._anki_miner_sink = True

    def acquire(self) -> None:
        self.lock_events.append("acquire")
        super().acquire()

    def release(self) -> None:
        self.lock_events.append("release")
        super().release()


@contextmanager
def _installed_sink(path: Path) -> Iterator[SpySinkHandler]:
    root = logging.getLogger()
    handler = SpySinkHandler(path)
    root.addHandler(handler)
    try:
        yield handler
    finally:
        root.removeHandler(handler)
        handler.close()


def _snapshot(tmp_path: Path) -> EnvironmentSnapshot:
    return EnvironmentSnapshot(
        app_version="2.9.0",
        python="3.11.9",
        qt="<unavailable: ImportError>",
        platform="TestOS-1",
        frozen=False,
        meipass=None,
        executable=str(tmp_path / "python"),
        home=str(tmp_path),
        log_path=str(tmp_path / "anki_miner.log"),
        log_ring="1024 bytes x 5 backups",
        ffmpeg="ffmpeg",
        ffprobe="ffprobe",
        ytdlp="yt-dlp",
        alass="alass",
        dictionary_chain=("indexed:test-dictionary enabled",),
        frequency_chain=(),
        pitch_chain=(),
        audio_chain=("jpod101 enabled",),
        ankiconnect_url="http://127.0.0.1:8765",
        deck="Test Deck",
        note_type="Test Note",
    )


def _disable_early_crash_member(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(bundle, "_early_crash_path", lambda: tmp_path / "absent-early-crash.log")


def _archive_members(path: Path) -> dict[str, bytes]:
    with ZipFile(path) as archive:
        return {name: archive.read(name) for name in archive.namelist()}


def _failed_fetch(tmp_path: Path, *, kind: str, source_url: str) -> None:
    fetcher = CustomAudioFetcher(
        url_template=source_url,
        kind=kind,
        cache_dir=tmp_path / f"cache-{kind}",
        file_prefix=f"test-{kind}",
        delay=0,
    )
    fetcher._session = MagicMock()
    fetcher._session.get.side_effect = requests.ConnectionError(source_url)
    try:
        assert fetcher.fetch("語", "ご") is None
    finally:
        fetcher.close()


def test_collect_log_members_includes_existing_rotations_and_locks_each_read(tmp_path: Path, monkeypatch) -> None:
    active = tmp_path / "custom.log"
    active.write_bytes(b"active")
    Path(f"{active}.1").write_bytes(b"one")
    Path(f"{active}.3").write_bytes(b"three")
    _disable_early_crash_member(monkeypatch, tmp_path)

    with _installed_sink(active) as handler:
        members, missing = collect_log_members()
        events = list(handler.lock_events)

    assert members == [
        ("anki_miner.log", b"active"),
        ("anki_miner.log.1", b"one"),
        ("anki_miner.log.3", b"three"),
    ]
    assert missing == []
    # The event COUNT is interpreter-specific: StreamHandler.flush() re-enters
    # acquire/release on <=3.12 and uses `with self.lock` on 3.13, so pinning the
    # exact sequence passes only on 3.13. What the read has to prove is that the
    # lock wraps every member and is left released, not how deep it nests.
    assert events[0] == "acquire"
    assert events[-1] == "release"
    assert events.count("acquire") == events.count("release")
    assert events.count("acquire") >= len(members)


def test_collect_log_members_adds_distinct_early_crash_fallback(tmp_path: Path, monkeypatch) -> None:
    active = tmp_path / "anki_miner.log"
    active.write_bytes(b"active")
    early = tmp_path / "AnkiMiner-early-crash.log"
    early.write_bytes(b"early")
    monkeypatch.setattr(bundle, "_early_crash_path", lambda: early)

    with _installed_sink(active):
        members, missing = collect_log_members()

    assert ("early-crash.log", b"early") in members
    assert missing == []


def test_bundle_contains_logs_health_and_full_settings(tmp_path: Path, monkeypatch) -> None:
    active = tmp_path / "custom.log"
    active.write_bytes(b"active log bytes")
    Path(f"{active}.1").write_bytes(b"rotated log bytes")
    _disable_early_crash_member(monkeypatch, tmp_path)
    config = AnkiMinerConfig(
        asr_device="cpu",
        blacklist_path=tmp_path / "lists" / "blacklist.txt",
        dictionary_chain=(ChainEntry(kind="indexed", dict_id="private-dictionary", enabled=True),),
        log_path=active,
    )
    target = tmp_path / "diagnostics.zip"

    with _installed_sink(active):
        result = write_diagnostics_bundle(
            target,
            config=config,
            snapshot=_snapshot(tmp_path),
            health_lines=["tools.ffmpeg: state=ok detail=ffmpeg 7.1 checked_at=2026-08-04T12:30:45"],
        )

    with ZipFile(target) as archive:
        names = archive.namelist()
        settings = json.loads(archive.read("settings.json"))
        health = archive.read("health.txt").decode("utf-8")
        total_bytes = sum(info.file_size for info in archive.infolist())

    assert names == list(result.members)
    assert "anki_miner.log" in names
    assert "anki_miner.log.1" in names
    assert "anki_miner.log.2" not in names
    assert settings["asr_device"] == "cpu"
    assert settings["blacklist_path"] == str(tmp_path / "lists" / "blacklist.txt")
    assert settings["dictionary_chain"] == [{"kind": "indexed", "dict_id": "private-dictionary", "enabled": True}]
    assert "tools.ffmpeg: state=ok" in health
    assert result.total_bytes == total_bytes


def test_bundle_redacts_custom_audio_url_queries_and_fragments(tmp_path: Path, monkeypatch) -> None:
    _disable_early_crash_member(monkeypatch, tmp_path)
    custom_url = "https://audio.example:8443/api/audio?token=PRIVATE_QUERY&term={term}#PRIVATE_FRAGMENT"
    custom_json_url = "https://json.example/list/{language}?key=PRIVATE_JSON#PRIVATE_JSON_FRAGMENT"
    non_custom_url = "https://builtin.example/audio?keep=this#unchanged"
    config = AnkiMinerConfig(
        expression_audio_chain=(
            AudioSourceEntry(kind="custom", url=custom_url, enabled=False),
            AudioSourceEntry(kind="custom_json", url=custom_json_url, enabled=True),
            AudioSourceEntry(kind="jpod101", url=non_custom_url, enabled=True),
        )
    )
    target = tmp_path / "diagnostics.zip"

    write_diagnostics_bundle(
        target,
        config=config,
        snapshot=_snapshot(tmp_path),
        health_lines=[],
    )

    with ZipFile(target) as archive:
        settings_bytes = archive.read("settings.json")
        chain = json.loads(settings_bytes)["expression_audio_chain"]

    assert b"PRIVATE_QUERY" not in settings_bytes
    assert b"PRIVATE_FRAGMENT" not in settings_bytes
    assert b"PRIVATE_JSON" not in settings_bytes
    assert chain[0] == {
        "enabled": False,
        "kind": "custom",
        "pack_id": None,
        "url": "https://audio.example:8443/api/audio?REDACTED",
    }
    assert chain[1]["kind"] == "custom_json"
    assert chain[1]["enabled"] is True
    assert chain[1]["url"] == "https://json.example/list/{language}?REDACTED"
    assert chain[2]["url"] == non_custom_url


@pytest.mark.parametrize(
    ("kind", "source_url", "expected_url", "username", "password"),
    [
        (
            "custom",
            "https://customuser:custompass@customuser.custompass.example/"
            "customuser/custompass;u=customuser;p=custompass?token=PRIVATE_QUERY",
            "<redacted-url>",
            b"customuser",
            b"custompass",
        ),
        (
            "custom_json",
            "https://jsonuser:jsonpass@jsonuser.jsonpass.example/"
            "jsonuser/jsonpass;u=jsonuser;p=jsonpass?token=PRIVATE_QUERY",
            "<redacted-url>",
            b"jsonuser",
            b"jsonpass",
        ),
    ],
)
def test_bundle_fail_closes_custom_audio_urls_with_userinfo(
    tmp_path: Path,
    monkeypatch,
    kind: str,
    source_url: str,
    expected_url: str,
    username: bytes,
    password: bytes,
) -> None:
    _disable_early_crash_member(monkeypatch, tmp_path)
    config = AnkiMinerConfig(expression_audio_chain=(AudioSourceEntry(kind=kind, url=source_url),))
    target = tmp_path / "diagnostics.zip"

    write_diagnostics_bundle(
        target,
        config=config,
        snapshot=_snapshot(tmp_path),
        health_lines=[],
    )

    with ZipFile(target) as archive:
        members = {name: archive.read(name) for name in archive.namelist()}
        saved_url = json.loads(members["settings.json"])["expression_audio_chain"][0]["url"]

    credential_hits = [name for name, content in members.items() if username in content or password in content]
    assert credential_hits == []
    assert saved_url == expected_url


def test_bundle_redaction_keeps_exporting_for_malformed_custom_audio_url(tmp_path: Path, monkeypatch) -> None:
    _disable_early_crash_member(monkeypatch, tmp_path)
    malformed_url = "http://PRIVATE_USER:PRIVATE_PASSWORD@[bad?token=PRIVATE_QUERY"
    config = AnkiMinerConfig(expression_audio_chain=(AudioSourceEntry(kind="custom", url=malformed_url),))
    target = tmp_path / "diagnostics.zip"

    write_diagnostics_bundle(
        target,
        config=config,
        snapshot=_snapshot(tmp_path),
        health_lines=[],
    )

    with ZipFile(target) as archive:
        settings_bytes = archive.read("settings.json")
        saved_url = json.loads(settings_bytes)["expression_audio_chain"][0]["url"]

    assert malformed_url.encode() not in settings_bytes
    assert b"PRIVATE_USER" not in settings_bytes
    assert b"PRIVATE_PASSWORD" not in settings_bytes
    assert b"PRIVATE_QUERY" not in settings_bytes
    assert saved_url == "<redacted-url>"


@pytest.mark.parametrize(
    ("kind", "secret_url", "clean_url", "clean_logged_url", "secrets"),
    [
        (
            "custom",
            "https://PERCENT_USER%3APERCENT_PASS%40audio.example/PERCENT_USER/PERCENT_PASS;u=PERCENT_USER",
            "https://audio.example:8443/direct/{term}?token=PRIVATE_QUERY#PRIVATE_FRAGMENT",
            "https://audio.example:8443/direct/語",
            (b"PERCENT_USER", b"PERCENT_PASS"),
        ),
        (
            "custom_json",
            "https://IPV6_USER:IPV6_PASS@[2001:db8::7]/IPV6_USER/IPV6_PASS;p=IPV6_PASS",
            "https://json.example:8443/list/{term}?token=PRIVATE_QUERY#PRIVATE_FRAGMENT",
            "https://json.example:8443/list/語",
            (b"IPV6_USER", b"IPV6_PASS"),
        ),
    ],
)
def test_failed_custom_audio_fetch_logs_and_bundle_fail_closed_for_userinfo(
    tmp_path: Path,
    monkeypatch,
    caplog,
    kind: str,
    secret_url: str,
    clean_url: str,
    clean_logged_url: str,
    secrets: tuple[bytes, bytes],
) -> None:
    active = tmp_path / "anki_miner.log"
    _disable_early_crash_member(monkeypatch, tmp_path)

    with _installed_sink(active), caplog.at_level(logging.DEBUG):
        _failed_fetch(tmp_path, kind=kind, source_url=secret_url)
        _failed_fetch(tmp_path, kind=kind, source_url=clean_url)
        target = tmp_path / f"{kind}-diagnostics.zip"
        write_diagnostics_bundle(
            target,
            config=AnkiMinerConfig(
                expression_audio_chain=(
                    AudioSourceEntry(kind=kind, url=secret_url),
                    AudioSourceEntry(kind=kind, url=clean_url),
                )
            ),
            snapshot=_snapshot(tmp_path),
            health_lines=[],
        )

    log_bytes = active.read_bytes()
    members = _archive_members(target)
    assert all(secret not in log_bytes for secret in secrets)
    assert [name for name, content in members.items() if any(secret in content for secret in secrets)] == []
    assert clean_logged_url.encode() in log_bytes
    assert clean_logged_url.encode() in members["anki_miner.log"]


def test_nested_custom_json_download_failure_is_redacted_from_log_and_bundle(
    tmp_path: Path, monkeypatch, caplog
) -> None:
    active = tmp_path / "anki_miner.log"
    endpoint = "https://json.example/list/{term}?token=PRIVATE_QUERY"
    nested_url = "https://NESTED_USER:NESTED_PASS@[2001:db8::8]/NESTED_USER/NESTED_PASS;p=NESTED_PASS"
    response = MagicMock(status_code=200, url="https://json.example/list/語")
    response.json.return_value = {
        "type": "audioSourceList",
        "audioSources": [{"url": nested_url}],
    }
    fetcher = CustomAudioFetcher(
        url_template=endpoint,
        kind="custom_json",
        cache_dir=tmp_path / "cache-nested",
        file_prefix="test-nested",
        delay=0,
    )
    fetcher._session = MagicMock()
    fetcher._session.get.side_effect = [response, requests.ConnectionError(nested_url)]
    _disable_early_crash_member(monkeypatch, tmp_path)

    try:
        with _installed_sink(active), caplog.at_level(logging.DEBUG):
            assert fetcher.fetch("語", "ご") is None
            target = tmp_path / "nested-diagnostics.zip"
            write_diagnostics_bundle(
                target,
                config=AnkiMinerConfig(expression_audio_chain=(AudioSourceEntry(kind="custom_json", url=endpoint),)),
                snapshot=_snapshot(tmp_path),
                health_lines=[],
            )
    finally:
        fetcher.close()

    log_bytes = active.read_bytes()
    members = _archive_members(target)
    secrets = (b"NESTED_USER", b"NESTED_PASS")
    assert all(secret not in log_bytes for secret in secrets)
    assert [name for name, content in members.items() if any(secret in content for secret in secrets)] == []


def test_bundle_redacts_pre_fix_audio_failure_lines_from_rotated_logs(tmp_path: Path, monkeypatch) -> None:
    active = tmp_path / "anki_miner.log"
    active.write_bytes(b"current clean record\n")
    rotated = Path(f"{active}.5")
    rotated.write_bytes(
        b"DEBUG audio download failed for "
        b"https://legacy-user.legacy-pass.example/legacy-user/legacy-pass;u=legacy-user;p=legacy-pass: "
        b"ConnectionError: offline\n"
    )
    _disable_early_crash_member(monkeypatch, tmp_path)

    with _installed_sink(active):
        target = tmp_path / "pre-fix-diagnostics.zip"
        write_diagnostics_bundle(
            target,
            config=AnkiMinerConfig(log_path=active),
            snapshot=_snapshot(tmp_path),
            health_lines=[],
        )

    members = _archive_members(target)
    assert [name for name, content in members.items() if b"legacy-user" in content] == []
    assert [name for name, content in members.items() if b"legacy-pass" in content] == []
    assert b"audio download failed for <redacted-url>" in members["anki_miner.log.5"]


def test_permission_error_is_reported_in_result_and_readme(tmp_path: Path, monkeypatch) -> None:
    active = tmp_path / "custom.log"
    active.write_bytes(b"active")
    unreadable = Path(f"{active}.1")
    unreadable.write_bytes(b"private")
    _disable_early_crash_member(monkeypatch, tmp_path)
    real_read_bytes = Path.read_bytes

    def read_bytes(path: Path) -> bytes:
        if path == unreadable:
            raise PermissionError("private path")
        return real_read_bytes(path)

    monkeypatch.setattr(Path, "read_bytes", read_bytes)
    target = tmp_path / "diagnostics.zip"

    with _installed_sink(active):
        result = write_diagnostics_bundle(
            target,
            config=AnkiMinerConfig(log_path=active),
            snapshot=_snapshot(tmp_path),
            health_lines=[],
        )

    with ZipFile(target) as archive:
        readme = archive.read("README.txt").decode("utf-8")
        health = archive.read("health.txt").decode("utf-8")

    assert "anki_miner.log.1" in result.missing
    assert "missing: anki_miner.log.1" in readme
    assert "System Health has not been checked this session." in health


def test_write_failure_leaves_no_target_or_staging_file(tmp_path: Path, monkeypatch) -> None:
    _disable_early_crash_member(monkeypatch, tmp_path)
    target = tmp_path / "diagnostics.zip"

    class FailingZipFile:
        def __init__(self, *_args, **_kwargs) -> None:
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args) -> bool:
            return False

        def writestr(self, *_args, **_kwargs) -> None:
            raise OSError("zip write failed")

    monkeypatch.setattr(bundle.zipfile, "ZipFile", FailingZipFile)

    with pytest.raises(OSError, match="zip write failed"):
        write_diagnostics_bundle(
            target,
            config=AnkiMinerConfig(),
            snapshot=_snapshot(tmp_path),
            health_lines=[],
        )

    assert not target.exists()
    assert list(tmp_path.glob(".diagnostics-*.zip")) == []


def test_default_bundle_name_uses_passed_datetime() -> None:
    assert default_bundle_name(datetime(2026, 8, 4, 12, 34, 56)) == "anki-miner-diagnostics-20260804-123456.zip"


def _seed_home(tmp_path: Path, monkeypatch) -> Path:
    """Point the bundle at an isolated home holding one of every state artifact."""
    home = tmp_path / "home"
    (home / "profiles").mkdir(parents=True)
    (home / "runtime_state" / "queues").mkdir(parents=True)
    (home / "runtime_state" / "downloads").mkdir(parents=True)
    (home / "gui_config.json").write_text('{"theme": "dark"}', encoding="utf-8")
    (home / "gui_config.json.bak").write_text('{"theme": "light"}', encoding="utf-8")
    (home / "gui_config.from-schema-9.json").write_text('{"schema_version": 9}', encoding="utf-8")
    (home / "ui_state.ini").write_text("[window]\ngeometry=abc\n", encoding="utf-8")
    (home / "profiles" / "work.json").write_text('{"profile_name": "Work"}', encoding="utf-8")
    (home / "runtime_state" / "queues" / "video.json").write_text('{"items": []}', encoding="utf-8")
    (home / "runtime_state" / "downloads" / "x.json").write_text('{"url": "x"}', encoding="utf-8")
    (home / "runtime_state" / "downloads" / "x.part").write_bytes(b"partial")
    slot = home / "dicts" / "jmdict"
    slot.mkdir(parents=True)
    (slot / "meta.json").write_text(
        json.dumps({"schema_version": "6", "entry_count": "1234", "language": "ja"}),
        encoding="utf-8",
    )
    (slot / "index.sqlite").write_bytes(b"x" * 64)
    _seed_known_words(home / "known_words.db")
    monkeypatch.setattr(bundle.paths, "ANKI_MINER_HOME", home)
    return home


def _seed_known_words(path: Path) -> None:
    conn = sqlite3.connect(path)
    try:
        conn.execute("CREATE TABLE known_words (lemma TEXT PRIMARY KEY, source TEXT DEFAULT 'anki')")
        conn.executemany(
            "INSERT INTO known_words (lemma, source) VALUES (?, ?)",
            [("\u732b", "anki"), ("\u72ac", "anki"), ("\u9ce5", "user")],
        )
        conn.commit()
    finally:
        conn.close()


def _seeded_config(home: Path) -> AnkiMinerConfig:
    return AnkiMinerConfig(
        dicts_root=home / "dicts",
        freqs_root=home / "freqs",
        pitch_root=home / "pitch",
        audio_packs_root=home / "audio_packs",
        known_words_db_path=home / "known_words.db",
        stats_db_path=home / "stats.db",
        log_path=home / "anki_miner.log",
    )


def test_bundle_ships_on_disk_config_and_runtime_state(tmp_path: Path, monkeypatch) -> None:
    home = _seed_home(tmp_path, monkeypatch)
    _disable_early_crash_member(monkeypatch, tmp_path)
    target = tmp_path / "state-diagnostics.zip"

    write_diagnostics_bundle(
        target,
        config=AnkiMinerConfig(log_path=home / "anki_miner.log"),
        snapshot=_snapshot(tmp_path),
        health_lines=[],
    )

    members = _archive_members(target)
    assert members["config/gui_config.json"] == b'{"theme": "dark"}'
    assert members["config/gui_config.json.bak"] == b'{"theme": "light"}'
    assert members["config/gui_config.from-schema-9.json"] == b'{"schema_version": 9}'
    assert b"geometry=abc" in members["config/ui_state.ini"]
    assert members["config/profiles/work.json"] == b'{"profile_name": "Work"}'
    assert members["state/queues/video.json"] == b'{"items": []}'
    assert members["state/downloads/x.json"] == b'{"url": "x"}'
    assert "state/downloads/x.part" not in members


def test_bundle_includes_the_child_log_beside_the_active_log(tmp_path: Path, monkeypatch) -> None:
    _seed_home(tmp_path, monkeypatch)
    active = tmp_path / "anki_miner.log"
    active.write_bytes(b"active")
    (tmp_path / "anki_miner.child.log").write_bytes(b"child stderr")
    _disable_early_crash_member(monkeypatch, tmp_path)

    with _installed_sink(active):
        members, missing = collect_log_members()

    assert ("anki_miner.child.log", b"child stderr") in members
    assert missing == []


def test_readme_declares_the_bundle_format(tmp_path: Path, monkeypatch) -> None:
    _seed_home(tmp_path, monkeypatch)
    _disable_early_crash_member(monkeypatch, tmp_path)
    target = tmp_path / "format-diagnostics.zip"

    write_diagnostics_bundle(
        target,
        config=AnkiMinerConfig(),
        snapshot=_snapshot(tmp_path),
        health_lines=[],
    )

    readme = _archive_members(target)["README.txt"].decode("utf-8")
    assert readme.splitlines()[2] == f"bundle_format: {bundle.BUNDLE_FORMAT}"
    assert bundle.BUNDLE_FORMAT == 2


def test_oversized_state_member_is_truncated_while_logs_stay_whole(tmp_path: Path, monkeypatch) -> None:
    home = _seed_home(tmp_path, monkeypatch)
    big = b"u" * (5 * 1024 * 1024)
    (home / "ui_state.ini").write_bytes(big)
    active = home / "anki_miner.log"
    active.write_bytes(b"L" * (5 * 1024 * 1024))
    _disable_early_crash_member(monkeypatch, tmp_path)
    target = tmp_path / "capped-diagnostics.zip"

    with _installed_sink(active):
        write_diagnostics_bundle(
            target,
            config=AnkiMinerConfig(log_path=active),
            snapshot=_snapshot(tmp_path),
            health_lines=[],
        )

    members = _archive_members(target)
    omitted = len(big) - bundle._MEMBER_MAX_BYTES
    assert len(members["config/ui_state.ini"]) < len(big)
    assert f"<truncated: {omitted} bytes omitted>".encode() in members["config/ui_state.ini"]
    assert len(members["anki_miner.log"]) == 5 * 1024 * 1024


def test_export_logs_one_receipt(tmp_path: Path, monkeypatch, caplog) -> None:
    _seed_home(tmp_path, monkeypatch)
    _disable_early_crash_member(monkeypatch, tmp_path)
    target = tmp_path / "receipt-diagnostics.zip"

    with caplog.at_level(logging.INFO, logger="anki_miner.diagnostics.bundle"):
        result = write_diagnostics_bundle(
            target,
            config=AnkiMinerConfig(),
            snapshot=_snapshot(tmp_path),
            health_lines=[],
        )

    receipts = [
        record.getMessage()
        for record in caplog.records
        if record.name == "anki_miner.diagnostics.bundle" and record.getMessage().startswith("Diagnostics exported:")
    ]
    assert len(receipts) == 1
    assert f"path={target}" in receipts[0]
    assert f"members={len(result.members)}" in receipts[0]
    assert f"zip_bytes={target.stat().st_size}" in receipts[0]


def test_bundle_inventories_resources_stores_disk_and_screens(tmp_path: Path, monkeypatch) -> None:
    home = _seed_home(tmp_path, monkeypatch)
    _disable_early_crash_member(monkeypatch, tmp_path)
    target = tmp_path / "inventory-diagnostics.zip"

    write_diagnostics_bundle(
        target,
        config=_seeded_config(home),
        snapshot=_snapshot(tmp_path),
        health_lines=[],
        ui_facts={"screen_count": "2", "theme": "midnight"},
    )

    members = _archive_members(target)
    resources = members["resources.txt"].decode("utf-8")
    stores = members["stores.txt"].decode("utf-8")
    disk = members["disk.txt"].decode("utf-8")
    screens = members["screens.txt"].decode("utf-8")

    assert "dicts/jmdict:" in resources
    assert "schema_version=6" in resources
    assert "entry_count=1234" in resources
    assert "language=ja" in resources
    assert "files=2" in resources
    assert "bytes=" in resources
    assert "known_words.db: rows=3" in stores
    assert "source.anki=2" in stores
    assert "source.user=1" in stores
    assert "free_bytes=" in disk
    assert "filesystem_encoding=" in disk
    assert "screen_count: 2" in screens
    assert "theme: midnight" in screens


def test_missing_ui_facts_render_a_placeholder(tmp_path: Path, monkeypatch) -> None:
    home = _seed_home(tmp_path, monkeypatch)
    _disable_early_crash_member(monkeypatch, tmp_path)
    target = tmp_path / "no-facts-diagnostics.zip"

    write_diagnostics_bundle(
        target,
        config=_seeded_config(home),
        snapshot=_snapshot(tmp_path),
        health_lines=[],
    )

    assert b"<unavailable" in _archive_members(target)["screens.txt"]


def test_unreadable_store_is_reported_without_raising(tmp_path: Path, monkeypatch) -> None:
    home = _seed_home(tmp_path, monkeypatch)
    _disable_early_crash_member(monkeypatch, tmp_path)
    target = tmp_path / "locked-diagnostics.zip"

    write_diagnostics_bundle(
        target,
        config=_seeded_config(home),
        snapshot=_snapshot(tmp_path),
        health_lines=[],
    )

    stores = _archive_members(target)["stores.txt"].decode("utf-8")
    assert "stats.db: <unavailable:" in stores
    assert "known_words.db: rows=3" in stores


def test_unreadable_resource_root_is_reported_without_raising(tmp_path: Path, monkeypatch) -> None:
    home = _seed_home(tmp_path, monkeypatch)
    _disable_early_crash_member(monkeypatch, tmp_path)
    real_iterdir = Path.iterdir

    def iterdir(path: Path):
        if path == home / "dicts":
            raise PermissionError("locked root")
        return real_iterdir(path)

    monkeypatch.setattr(Path, "iterdir", iterdir)
    target = tmp_path / "locked-root-diagnostics.zip"

    write_diagnostics_bundle(
        target,
        config=_seeded_config(home),
        snapshot=_snapshot(tmp_path),
        health_lines=[],
    )

    resources = _archive_members(target)["resources.txt"].decode("utf-8")
    assert "dicts: <unavailable: PermissionError: locked root>" in resources
