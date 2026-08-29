"""GUI import entry points stamp the active mining language.

Two halves of one invariant live here:

* every **add** import path forwards the config's language to its importer (the
  repair paths deliberately do not — they replay the slot's own stamp), and
* the dictionary importer folds its key columns with the profile of the
  language it is stamping, matching what ``IndexedDictProvider`` folds with at
  query time.

Every forwarding assertion uses the omit-when-ja shape: a Japanese call passes
no ``language=`` at all, so the call stays byte-identical to the pre-transition
one (several pre-existing tests stub these collaborators with exact-signature
doubles that a new keyword would break).
"""

from __future__ import annotations

import dataclasses
import hashlib
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import pytest

pytest.importorskip("PyQt6.QtWidgets")

from anki_miner.config import AnkiMinerConfig  # noqa: E402
from anki_miner.gui.workers.import_worker import ImportWorker  # noqa: E402

_FREQ = "anki_miner.gui.workers.import_worker.import_frequency_source"
_PITCH = "anki_miner.gui.workers.import_worker.import_pitch_source"
_PACK = "anki_miner.gui.workers.import_worker.import_audio_pack"
_ANDROID = "anki_miner.gui.workers.import_worker.import_android_audio_db"
_YOMITAN = "anki_miner.gui.workers.import_worker.import_yomitan_zip"


def _fake_result():
    return type(
        "R",
        (),
        {
            "source_id": "s",
            "pack_id": "s",
            "dict_id": "s",
            "entry_count": 1,
            "source_name": "s",
            "format": "csv",
            "skipped_malformed": 0,
            "converted_to_ranks": False,
            "is_categorical": False,
            "media_warnings": (),
        },
    )()


def _capturing(seen: dict):
    def _fake_import(input_path, dest_root, **kwargs):
        seen.update(kwargs)
        return _fake_result()

    return _fake_import


def _drive(worker: ImportWorker) -> None:
    worker._runner(lambda *a, **k: None, lambda: False)


# ---------------------------------------------------------------------------
# The five add constructors forward the language; the four repair ones cannot.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("target", "factory"),
    [
        (_FREQ, lambda tmp: ImportWorker.for_source(tmp / "a.csv", tmp, language="zh")),
        (_PITCH, lambda tmp: ImportWorker.for_pitch_source(tmp / "a.csv", tmp, language="zh")),
        (_PACK, lambda tmp: ImportWorker.for_pack(tmp / "pack", tmp, language="zh")),
        (_ANDROID, lambda tmp: ImportWorker.for_android_audio_db(tmp / "a.db", tmp, language="zh")),
        (_YOMITAN, lambda tmp: ImportWorker.for_yomitan(tmp / "a.zip", tmp, language="zh")),
    ],
)
def test_add_constructors_forward_language(qapp, tmp_path, monkeypatch, target, factory):
    seen: dict = {}
    monkeypatch.setattr(target, _capturing(seen))
    _drive(factory(tmp_path))
    assert seen["language"] == "zh"


@pytest.mark.parametrize(
    ("target", "factory"),
    [
        (_FREQ, lambda tmp: ImportWorker.for_source(tmp / "a.csv", tmp)),
        (_PITCH, lambda tmp: ImportWorker.for_pitch_source(tmp / "a.csv", tmp)),
        (_PACK, lambda tmp: ImportWorker.for_pack(tmp / "pack", tmp)),
        (_ANDROID, lambda tmp: ImportWorker.for_android_audio_db(tmp / "a.db", tmp)),
        (_YOMITAN, lambda tmp: ImportWorker.for_yomitan(tmp / "a.zip", tmp)),
    ],
)
def test_add_constructors_omit_language_for_ja(qapp, tmp_path, monkeypatch, target, factory):
    """ja is the importer default, so the ja call carries no keyword at all."""
    seen: dict = {}
    monkeypatch.setattr(target, _capturing(seen))
    _drive(factory(tmp_path))
    assert "language" not in seen


def test_repair_never_stamps_the_active_language():
    import inspect

    for name in ("for_source_repair", "for_pitch_source_repair", "for_yomitan_repair", "for_pack_repair"):
        params = inspect.signature(getattr(ImportWorker, name)).parameters
        assert "language" not in params, name


# ---------------------------------------------------------------------------
# Every GUI add call site passes the config's language.
# ---------------------------------------------------------------------------


def _config(language: str) -> AnkiMinerConfig:
    return dataclasses.replace(AnkiMinerConfig(), language=language)


def _capture_factory(monkeypatch, owner, name, seen: dict):
    monkeypatch.setattr(owner, name, classmethod(lambda cls, *a, **kw: seen.update(kw)))


@pytest.mark.parametrize("language", ["zh", "ja"])
def test_frequency_flow_passes_the_config_language(qapp, tmp_path, monkeypatch, language):
    from anki_miner.gui.controllers.frequency_import_flow import FrequencyImportFlow

    seen: dict = {}
    _capture_factory(monkeypatch, ImportWorker, "for_source", seen)
    flow = FrequencyImportFlow(None, None, lambda: _config(language), lambda _chain: None, lambda: None)
    flow._make_add_worker(tmp_path / "a.csv", tmp_path)
    assert seen.get("language") == (None if language == "ja" else "zh")


@pytest.mark.parametrize("language", ["zh", "ja"])
def test_pitch_flow_passes_the_config_language(qapp, tmp_path, monkeypatch, language):
    from anki_miner.gui.controllers.pitch_import_flow import PitchImportFlow

    seen: dict = {}
    _capture_factory(monkeypatch, ImportWorker, "for_pitch_source", seen)
    flow = PitchImportFlow(None, None, lambda: _config(language), lambda _chain: None, lambda: None)
    flow._make_add_worker(tmp_path / "a.csv", tmp_path)
    assert seen.get("language") == (None if language == "ja" else "zh")


@pytest.mark.parametrize("language", ["zh", "ja"])
def test_dictionary_add_passes_the_config_language(qapp, tmp_path, monkeypatch, language):
    from unittest.mock import MagicMock

    from anki_miner.gui.controllers.dictionary_import_flow import DictionaryImportFlow

    seen: dict = {}
    _capture_factory(monkeypatch, ImportWorker, "for_yomitan", seen)
    flow = DictionaryImportFlow(
        parent=None,
        panel=MagicMock(),
        get_config=lambda: dataclasses.replace(_config(language), dicts_root=tmp_path),
        persist_chain=MagicMock(),
        notify_config_changed=MagicMock(),
    )
    captured: list = []
    monkeypatch.setattr(flow, "_run_chained_imports", lambda **kw: captured.append(kw))
    flow._add_dict_picked("trace", 0.0, [str(tmp_path / "d.zip")])

    assert captured, "the add path must reach the chained-import runner"
    captured[0]["make_worker"](tmp_path / "d.zip")
    assert seen.get("language") == (None if language == "ja" else "zh")


@pytest.mark.parametrize("language", ["zh", "ja"])
def test_audio_pack_add_passes_the_config_language(qapp, tmp_path, monkeypatch, language):
    from unittest.mock import MagicMock

    from anki_miner.gui.controllers.audio_pack_import_flow import AudioPackImportFlow

    seen: dict = {}
    _capture_factory(monkeypatch, ImportWorker, "for_pack", seen)
    flow = AudioPackImportFlow(
        parent=None,
        panel=MagicMock(),
        get_config=lambda: dataclasses.replace(_config(language), audio_packs_root=tmp_path),
        persist_chain=MagicMock(),
        notify_config_changed=MagicMock(),
    )
    captured: list = []
    monkeypatch.setattr(flow, "_run_chained_imports", lambda **kw: captured.append(kw))
    flow.add_pack(_scan_result=(str(tmp_path), [(tmp_path / "pack", "ajt")]), _trace_id="trace")

    assert captured, "the add path must reach the chained-import runner"
    captured[0]["make_worker"]((tmp_path / "pack", "ajt"))
    assert seen.get("language") == (None if language == "ja" else "zh")


@pytest.mark.parametrize("language", ["zh", "ja"])
def test_android_db_add_passes_the_config_language(qapp, tmp_path, monkeypatch, language):
    from unittest.mock import MagicMock

    from anki_miner.gui.controllers.audio_pack_import_flow import AudioPackImportFlow

    seen: dict = {}
    _capture_factory(monkeypatch, ImportWorker, "for_android_audio_db", seen)
    flow = AudioPackImportFlow(
        parent=None,
        panel=MagicMock(),
        get_config=lambda: dataclasses.replace(_config(language), audio_packs_root=tmp_path),
        persist_chain=MagicMock(),
        notify_config_changed=MagicMock(),
    )
    monkeypatch.setattr(flow, "_run_modal_import", MagicMock())
    monkeypatch.setattr(flow, "_set_import_buttons_enabled", MagicMock())
    flow._add_android_db_picked("trace", 0.0, str(tmp_path / "android.db"))
    assert seen.get("language") == (None if language == "ja" else "zh")


# ---------------------------------------------------------------------------
# The recommended-resources download worker.
# ---------------------------------------------------------------------------


def test_resource_download_worker_forwards_language(qapp, tmp_path):
    from anki_miner.gui.workers.resource_download_worker import ResourceDownloadWorker

    worker = ResourceDownloadWorker(
        (),
        dicts_root=tmp_path / "d",
        freqs_root=tmp_path / "f",
        pitch_root=tmp_path / "p",
        download_dir=tmp_path / "dl",
        language="ko",
    )
    assert worker._language == "ko"


def test_resource_download_worker_defaults_to_ja(qapp, tmp_path):
    from anki_miner.gui.workers.resource_download_worker import ResourceDownloadWorker

    worker = ResourceDownloadWorker(
        (),
        dicts_root=tmp_path / "d",
        freqs_root=tmp_path / "f",
        pitch_root=tmp_path / "p",
        download_dir=tmp_path / "dl",
    )
    assert worker._language == "ja"


@pytest.mark.parametrize("language", ["zh", "ja"])
def test_resource_download_worker_stamps_its_importers(qapp, tmp_path, monkeypatch, language):
    """The three importer calls carry the worker's language (omitted for ja)."""
    from anki_miner.gui.workers import resource_download_worker as mod
    from anki_miner.services.resource_catalog import ResourceSpec

    seen: list[dict] = []

    def fake_download(url, **kwargs):
        temp = Path(kwargs["dest_dir"]) / f"{Path(url).name}.part"
        temp.parent.mkdir(parents=True, exist_ok=True)
        temp.write_bytes(b"ZIP")
        return temp

    def fake_import(source, root, **kwargs):
        seen.append(kwargs)
        return _fake_result()

    monkeypatch.setattr(mod, "download_to_temp", fake_download)
    monkeypatch.setattr(mod, "import_yomitan_zip", fake_import)
    monkeypatch.setattr(mod, "import_frequency_source", fake_import)
    monkeypatch.setattr(mod, "import_pitch_source", fake_import)
    monkeypatch.setattr(mod, "sweep_superseded_dicts", lambda *a, **kw: ([], []))

    specs = [
        ResourceSpec(id="d", kind="dict", display_name="D", url="http://x/d.zip", license_note=""),
        ResourceSpec(id="f", kind="freq", display_name="F", url="http://x/f.csv", license_note=""),
        ResourceSpec(id="p", kind="pitch", display_name="P", url="http://x/p.txt", license_note=""),
    ]
    worker = mod.ResourceDownloadWorker(
        specs,
        dicts_root=tmp_path / "d",
        freqs_root=tmp_path / "f",
        pitch_root=tmp_path / "p",
        download_dir=tmp_path / "dl",
        language=language,
    )
    worker.run()

    assert len(seen) == 3
    for kwargs in seen:
        assert kwargs.get("language") == (None if language == "ja" else "zh")


@pytest.mark.parametrize("language", ["zh", "ja"])
def test_download_dialog_stamps_the_catalog_language(qapp, tmp_path, monkeypatch, language):
    """The recommended set IS the ja catalog, so the stamp is ja in every session.

    Stamping the active language would both mislabel JMdict and fold it out of
    the ja chain filter, leaving it downloaded but unqueryable. Per-profile
    catalog routing lands with the setup-wizard task (2B.8).
    """
    from unittest.mock import MagicMock

    from anki_miner.gui.widgets.dialogs import resource_download_dialog as mod
    from anki_miner.services.resource_catalog import RECOMMENDED_DEFAULT_SET

    seen: list[dict] = []
    monkeypatch.setattr(mod, "ResourceDownloadWorker", lambda *a, **kw: (seen.append(kw), MagicMock())[1])

    session = mod.ResourceDownloadSession(
        None,
        dataclasses.replace(_config(language), dicts_root=tmp_path, freqs_root=tmp_path, pitch_root=tmp_path),
        specs=RECOMMENDED_DEFAULT_SET,
        activate=lambda _s: None,
    )
    monkeypatch.setattr(mod, "ResourceDownloadWindow", MagicMock())
    assert session.start() is True

    assert seen
    # Omit-when-ja: no keyword at all, so the ja call stays byte-identical.
    assert "language" not in seen[0]


def test_download_from_a_zh_session_indexes_a_ja_slot(qapp, tmp_path, monkeypatch):
    """End of the same thread: the slot's meta.language is ja, not the session's.

    The worker the zh session builds is constructed for real and then run on
    this thread, so the assertion is on the importer call the download actually
    makes rather than on the session's keyword.
    """
    from unittest.mock import MagicMock

    from anki_miner.gui.widgets.dialogs import resource_download_dialog as mod
    from anki_miner.gui.workers import resource_download_worker as worker_mod
    from anki_miner.services.resource_catalog import ResourceSpec

    spec = ResourceSpec(id="f", kind="freq", display_name="F", url="http://x/f.csv", license_note="")
    stamped: list[str] = []
    built: list = []

    def fake_download(url, **kwargs):
        temp = Path(kwargs["dest_dir"]) / "f.csv"
        temp.parent.mkdir(parents=True, exist_ok=True)
        temp.write_text("word,1\n", encoding="utf-8")
        return temp

    def fake_import(source, root, **kwargs):
        stamped.append(kwargs.get("language", "ja"))
        return _fake_result()

    monkeypatch.setattr(worker_mod, "download_to_temp", fake_download)
    monkeypatch.setattr(worker_mod, "import_frequency_source", fake_import)
    monkeypatch.setattr(mod, "ResourceDownloadWindow", MagicMock())

    def _capture(*args, **kwargs):
        built.append(worker_mod.ResourceDownloadWorker(*args, **kwargs))
        return MagicMock()

    monkeypatch.setattr(mod, "ResourceDownloadWorker", _capture)

    session = mod.ResourceDownloadSession(
        None,
        dataclasses.replace(_config("zh"), dicts_root=tmp_path, freqs_root=tmp_path, pitch_root=tmp_path),
        specs=(spec,),
        activate=lambda _s: None,
    )
    assert session.start() is True
    assert built, "the session must build a download worker"
    built[0].run()

    assert stamped == ["ja"]


# ---------------------------------------------------------------------------
# The android.db importer's own stamp (no GUI in the way).
# ---------------------------------------------------------------------------


def _make_android_db(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    try:
        conn.executescript("""
            CREATE TABLE entries (
                id INTEGER PRIMARY KEY, expression TEXT NOT NULL, reading TEXT,
                source TEXT NOT NULL, speaker TEXT, display TEXT, file TEXT NOT NULL
            );
            CREATE TABLE android (
                id INTEGER PRIMARY KEY, file TEXT NOT NULL, source TEXT NOT NULL, data BLOB NOT NULL
            );
            """)
        conn.execute(
            "INSERT INTO entries VALUES (1, ?, ?, ?, NULL, ?, ?)",
            ("食べる", "たべる", "nhk16", "タベル", "audio/one.mp3"),
        )
        conn.execute("INSERT INTO android VALUES (1, ?, ?, ?)", ("audio/one.mp3", "nhk16", b"ID3-test"))
        conn.commit()
    finally:
        conn.close()
    return path


@pytest.mark.parametrize("language", ["zh", "ja"])
def test_android_audio_db_stamps_the_language(tmp_path, language):
    from anki_miner.services.audio_packs.importer import import_android_audio_db
    from anki_miner.services.audio_packs.storage import read_meta

    source = _make_android_db(tmp_path / "src" / "android.db")
    packs_root = tmp_path / "packs"
    result = import_android_audio_db(source, packs_root, language=language)

    assert read_meta(packs_root / result.pack_id / "index.sqlite")["language"] == language


# ---------------------------------------------------------------------------
# Step 5: the stamped language also picks the import-side key folding.
# ---------------------------------------------------------------------------


class _UpperKeys:
    """Stub folding that uppercases both key spaces — unlike ja on ASCII."""

    def fold_term(self, s: str) -> str:
        return s.upper()

    def fold_reading(self, s: str | None) -> str | None:
        return None if s is None else s.upper()

    def homograph_keep_mask(self, word: str, rows: list[tuple[str, str]], lemma: str | None = None) -> list[bool]:
        return [True] * len(rows)


_ASCII_BANK = [[["abc", "dee", "", "", 0, ["alpha"], 1, ""]]]


@pytest.fixture
def frozen_import_date(monkeypatch):
    """Pin ``import_date`` so two importer runs can be compared byte-for-byte."""
    import anki_miner.services.dictionary.importers.yomitan_importer as yi

    class _FixedDatetime:
        @staticmethod
        def now(tz=None):
            return datetime(2026, 1, 1, tzinfo=tz or UTC)

    monkeypatch.setattr(yi, "datetime", _FixedDatetime)


def test_dict_import_folds_with_the_stamped_language(tmp_path, monkeypatch):
    """A non-ja import writes keys folded by that language's own rule.

    The real zh profile with its folding swapped for ``_UpperKeys``: what is
    under test is that the STAMP picks the folding, and zh's own NFC+casefold
    rule leaves this ASCII bank indistinguishable from ja's.
    """
    from anki_miner.languages import registry
    from anki_miner.services.dictionary import storage
    from anki_miner.services.dictionary.importers.yomitan_importer import import_yomitan_zip
    from tests.fixtures.dictionary.build_yomitan_fixture import build_yomitan_zip

    monkeypatch.setattr(registry, "_CACHE", dict(registry._CACHE))
    monkeypatch.setitem(
        registry._CACHE,
        "zh",
        dataclasses.replace(registry.get_profile("zh"), dict_keys=_UpperKeys()),
    )

    zip_path = build_yomitan_zip(tmp_path / "src" / "d.zip", term_banks=_ASCII_BANK)
    dest = tmp_path / "dicts"
    result = import_yomitan_zip(zip_path, dest, dict_id="zh-dict", language="zh")

    db = dest / result.dict_id / "index.sqlite"
    assert storage.read_meta(db)["language"] == "zh"
    conn = sqlite3.connect(db)
    try:
        assert conn.execute("SELECT term, reading FROM entries").fetchall() == [("ABC", "DEE")]
    finally:
        conn.close()


def test_ja_dict_import_bytes_are_unchanged(tmp_path, monkeypatch, frozen_import_date):
    """The ja folding leaves the produced index byte-identical to keys=None."""
    import anki_miner.services.dictionary.importers.yomitan_importer as yi
    from tests.fixtures.dictionary.build_yomitan_fixture import build_yomitan_zip

    zip_path = build_yomitan_zip(tmp_path / "src" / "d.zip")

    folded_root = tmp_path / "folded"
    import_yomitan_zip = yi.import_yomitan_zip
    seen_keys: list[object] = []
    real_bulk_insert = yi.bulk_insert

    def recording(*args, **kwargs):
        seen_keys.append(kwargs.get("keys"))
        return real_bulk_insert(*args, **kwargs)

    monkeypatch.setattr(yi, "bulk_insert", recording)
    import_yomitan_zip(zip_path, folded_root, dict_id="d")

    # The pre-transition call shape: no folding at all.
    def keyless(*args, **kwargs):
        kwargs.pop("keys", None)
        return real_bulk_insert(*args, **kwargs)

    monkeypatch.setattr(yi, "bulk_insert", keyless)
    plain_root = tmp_path / "plain"
    import_yomitan_zip(zip_path, plain_root, dict_id="d")

    from anki_miner.languages.registry import get_profile

    assert seen_keys == [get_profile("ja").dict_keys]
    folded = (folded_root / "d" / "index.sqlite").read_bytes()
    plain = (plain_root / "d" / "index.sqlite").read_bytes()
    assert hashlib.sha256(folded).hexdigest() == hashlib.sha256(plain).hexdigest()


def test_dict_import_and_query_fold_with_the_same_language(tmp_path, monkeypatch):
    """Import-side and query-side folding are one invariant."""
    from anki_miner.config import ChainEntry
    from anki_miner.languages.registry import get_profile
    from anki_miner.services.dictionary.importers.yomitan_importer import import_yomitan_zip
    from anki_miner.services.dictionary.registry import DictionaryRegistry
    from tests.fixtures.dictionary.build_yomitan_fixture import build_yomitan_zip

    seen: list[object] = []
    import anki_miner.services.dictionary.importers.yomitan_importer as yi

    real = yi.bulk_insert
    monkeypatch.setattr(yi, "bulk_insert", lambda *a, **kw: (seen.append(kw.get("keys")), real(*a, **kw))[1])

    dest = tmp_path / "dicts"
    import_yomitan_zip(build_yomitan_zip(tmp_path / "src" / "d.zip"), dest, dict_id="ja-dict")
    assert seen == [get_profile("ja").dict_keys]

    registry = DictionaryRegistry(dest)
    registry.load()
    config = dataclasses.replace(
        AnkiMinerConfig(),
        dicts_root=dest,
        dictionary_chain=(ChainEntry(kind="indexed", dict_id="ja-dict", enabled=True),),
    )
    chain = registry.build_provider_chain(config)
    assert [p._keys for p in chain] == [seen[0]]
