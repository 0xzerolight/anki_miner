"""--api ocr over a fake mokuro script: the .mokuro beside the volume, page counts, refusals, cancel."""

from __future__ import annotations

import json
import sys
import zipfile
from dataclasses import replace

import pytest

from anki_miner.cli.api import ocr
from anki_miner.services.mokuro_runner import MokuroResult, MokuroRunnerService, MokuroStatus

# mokuro's own receipt lines (services/mokuro_runner.py); a page whose name starts with "bad" fails, as
# --ignore_errors leaves it out of the .mokuro. Its arguments are kept beside it for the flags.
_FAKE_MOKURO = """#!{python}
import json, sys, zipfile
from pathlib import Path

source = Path(sys.argv[1])
(Path(__file__).parent / "argv.json").write_text(json.dumps(sys.argv[1:]))
if "--fail" in source.name:
    print("Error while processing " + str(source))
    print("Processed successfully: 0/1")
    sys.exit(0)
if source.is_dir():
    names = sorted(p.relative_to(source).as_posix() for p in source.rglob("*") if p.is_file())
    out = source.parent / (source.name + ".mokuro")
else:
    names = sorted(n for n in zipfile.ZipFile(source).namelist() if not n.endswith("/"))
    out = source.with_suffix(".mokuro")
pages = [{{"img_path": n, "blocks": []}} for n in names if not Path(n).name.startswith("bad")]
for done in range(1, len(names) + 1):
    print("Processing pages...: 50%|#####| {{}}/{{}} [00:01<00:01]".format(done, len(names)))
out.write_text(json.dumps({{"title": source.parent.name, "volume": source.stem, "pages": pages}}))
print("Processed successfully: 1/1")
"""


@pytest.fixture
def mokuro(tmp_path, test_config, monkeypatch):
    """A profile whose mokuro is the fake script; returns the script's folder."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    script = bin_dir / "mokuro"
    script.write_text(_FAKE_MOKURO.format(python=sys.executable), encoding="utf-8")
    script.chmod(0o755)
    config = replace(test_config, mokuro_location=script)
    monkeypatch.setattr(ocr.settings, "load_profile_config", lambda profile_id: config)
    return bin_dir


@pytest.fixture
def verdict(capfd, monkeypatch):
    from anki_miner.cli import api, entry

    monkeypatch.setattr(api, "_prepare_process", lambda: None)
    monkeypatch.setattr(entry, "_install_api_log", lambda: None)

    def run(*argv: str) -> dict:
        assert entry.main(["--api", *argv]) == 0
        [line] = capfd.readouterr().out.splitlines()
        return json.loads(line)

    return run


def _folder(tmp_path, name="漫画 第1巻", pages=("001.jpg", "002.png", "bad003.jpg")):
    volume = tmp_path / "漫画" / name
    volume.mkdir(parents=True)
    for page in pages:
        (volume / page).parent.mkdir(parents=True, exist_ok=True)
        (volume / page).write_bytes(b"page")
    return volume


def _ocr_file(tmp_path, *volumes, language="ja"):
    runs = tmp_path / "runs"
    runs.mkdir(exist_ok=True)
    episodes = [{"run_id": f"vol-{i}", "volume": str(v)} for i, v in enumerate(volumes, start=1)]
    path = tmp_path / "ocr.json"
    path.write_text(
        json.dumps({"schema": 1, "run_dir": str(runs), "language": language, "episodes": episodes}),
        encoding="utf-8",
    )
    return path


def _record(tmp_path, run_id="vol-1", n=1):
    return json.loads((tmp_path / "runs" / run_id / f"ocr-{n}.json").read_text(encoding="utf-8"))


@pytest.mark.skipif(sys.platform == "win32", reason="the fake mokuro is a shebang script")
def test_a_page_folder_gets_its_mokuro_beside_it(verdict, mokuro, tmp_path) -> None:
    volume = _folder(tmp_path)
    v = verdict("ocr", str(_ocr_file(tmp_path, volume)))
    assert v["ok"] and v["runs"] == [
        {
            "run_id": "vol-1",
            "ok": True,
            "error": None,
            "message": None,
            "file": "ocr-1.json",
            "failure_is_transient": False,
        }
    ]
    mokuro_file = volume.parent / "漫画 第1巻.mokuro"
    assert _record(tmp_path) == {
        "schema": 1,
        "run_id": "vol-1",
        "mokuro_file": str(mokuro_file),
        "pages": 2,
        "pages_failed": 1,
    }
    assert mokuro_file.is_file()
    folder = tmp_path / "runs" / "vol-1"
    assert sorted(p.name for p in folder.iterdir()) == ["ocr-1.json", "progress.json"]  # no ocr-1/ folder
    progress = json.loads((folder / "progress.json").read_text(encoding="utf-8"))
    assert (progress["stage"], progress["stages"], progress["done"], progress["total"]) == (1, 1, 100, 100)
    argv = json.loads((mokuro / "argv.json").read_text(encoding="utf-8"))
    assert argv[0] == str(volume) and "--no_cache=True" not in argv and "--force_cpu=True" not in argv


@pytest.mark.skipif(sys.platform == "win32", reason="the fake mokuro is a shebang script")
def test_an_archive_and_a_mokuro_already_there(verdict, mokuro, tmp_path, monkeypatch, test_config) -> None:
    archive = tmp_path / "漫画 第2巻.cbz"
    with zipfile.ZipFile(archive, "w") as zf:
        for name in ("p/", "p/001.jpg", "p/002.jpg", "p/bad003.jpg"):
            zf.writestr(name, b"page")
    old = tmp_path / "漫画 第2巻.mokuro"
    old.write_text('{"pages": []}', encoding="utf-8")
    config = replace(test_config, mokuro_location=mokuro / "mokuro", mokuro_use_gpu=False)
    monkeypatch.setattr(ocr.settings, "load_profile_config", lambda profile_id: config)
    assert verdict("ocr", str(_ocr_file(tmp_path, archive)))["ok"]
    record = _record(tmp_path)
    assert (record["mokuro_file"], record["pages"], record["pages_failed"]) == (str(old), 2, 1)
    assert len(json.loads(old.read_text(encoding="utf-8"))["pages"]) == 2  # replaced
    argv = json.loads((mokuro / "argv.json").read_text(encoding="utf-8"))
    assert "--no_cache=True" in argv and "--force_cpu=True" in argv  # the tab's Redo, and its GPU box off


@pytest.mark.skipif(sys.platform == "win32", reason="the fake mokuro is a shebang script")
def test_a_volume_mokuro_fails_on_is_ocr_failed_and_the_next_still_runs(verdict, mokuro, tmp_path) -> None:
    v = verdict("ocr", str(_ocr_file(tmp_path, _folder(tmp_path, "x --fail"), _folder(tmp_path, "ok"))))
    assert v["ok"] is False and v["error"] is None
    assert [(r["error"], r["file"]) for r in v["runs"]] == [("OCR_FAILED", None), (None, "ocr-1.json")]
    assert "did not produce" in v["runs"][0]["message"]


def test_page_images_counts_what_mokuro_reads(tmp_path) -> None:
    volume = _folder(tmp_path, pages=("a/001.jpg", "a/001.png", "002.WEBP", "notes.txt", ".DS_Store", "._003.jpg"))
    assert ocr.page_images(volume) == 2  # 001.jpg and 001.png are one page to mokuro
    assert ocr.page_images(tmp_path / "missing.cbz") == 0


@pytest.mark.parametrize("volume", ["book.txt", "missing", "missing.cbz"])
def test_anything_but_a_volume_is_bad_run_file(verdict, mokuro, tmp_path, volume) -> None:
    (tmp_path / "book.txt").write_text("x", encoding="utf-8")
    v = verdict("ocr", str(_ocr_file(tmp_path, tmp_path / volume)))
    assert v["error"] == "BAD_RUN_FILE" and "folder of page images" in v["message"] and v["runs"] == []


def test_mokuro_not_ready_refuses_the_call(verdict, tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(
        ocr, "manga_ocr_problem", lambda config: "mokuro is not installed. Install it with setup --ocr."
    )
    v = verdict("ocr", str(_ocr_file(tmp_path, _folder(tmp_path))))
    assert (v["error"], v["message"], v["runs"]) == (
        "SETUP_ERROR",
        "mokuro is not installed. Install it with setup --ocr.",
        [],
    )


def test_mokuro_lost_mid_call_is_setup_error(verdict, tmp_path, monkeypatch) -> None:
    from anki_miner.exceptions.mokuro import MokuroNotFoundError

    def gone(self, volume, options, progress_cb=None, cancel_event=None):
        raise MokuroNotFoundError("mokuro is not installed.")

    monkeypatch.setattr(ocr, "manga_ocr_problem", lambda config: None)
    monkeypatch.setattr(MokuroRunnerService, "process_volume", gone)
    [run] = verdict("ocr", str(_ocr_file(tmp_path, _folder(tmp_path))))["runs"]
    assert run["error"] == "SETUP_ERROR"


def test_a_cancel_file_stops_the_run(verdict, tmp_path, monkeypatch) -> None:
    def cancelled(self, volume, options, progress_cb=None, cancel_event=None):
        (tmp_path / "runs" / "vol-1" / "cancel").touch()
        assert cancel_event.wait(5)
        return MokuroResult(MokuroStatus.CANCELLED, None)

    monkeypatch.setattr(ocr, "manga_ocr_problem", lambda config: None)
    monkeypatch.setattr(MokuroRunnerService, "process_volume", cancelled)
    [run] = verdict("ocr", str(_ocr_file(tmp_path, _folder(tmp_path))))["runs"]
    assert (run["error"], run["file"]) == ("CANCELLED", None)
    assert not (tmp_path / "runs" / "vol-1" / "cancel").exists()
    assert not list((tmp_path / "runs" / "vol-1").glob("ocr-*"))
