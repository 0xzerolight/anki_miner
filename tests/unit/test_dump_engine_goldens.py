from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
import unidic_lite

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = REPOSITORY_ROOT / "scripts" / "dump_engine_goldens.py"
CORPUS_PATH = REPOSITORY_ROOT / "tests" / "fixtures" / "goldens" / "tokenizer-v1.json"


def _load_exporter():
    spec = importlib.util.spec_from_file_location("dump_engine_goldens", SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


exporter = _load_exporter()


def _token(surface: str):
    feature = SimpleNamespace(**dict.fromkeys(exporter.UNIDIC_FEATURE_FIELDS))
    return SimpleNamespace(surface=surface, feature=feature, is_unk=True)


def test_offsets_distinguish_codepoints_from_utf16_units():
    text = "猫𠮟𠮟犬"
    located = exporter._locate_tokens(text, [_token("猫"), _token("𠮟𠮟"), _token("犬")])
    record = exporter._token_record(text, *located[1])

    assert record["offsets"] == {
        "codepoint_start": 1,
        "codepoint_end": 3,
        "utf16_start": 1,
        "utf16_end": 5,
    }


def test_token_locator_skips_unrepresented_whitespace_but_never_guesses():
    text = "猫  猫"
    located = exporter._locate_tokens(text, [_token("猫"), _token("猫")])
    assert [(start, end) for _token_value, start, end in located] == [(0, 1), (3, 4)]

    with pytest.raises(exporter.GoldenExportError, match="not locatable"):
        exporter._locate_tokens("猫", [_token("犬")])


def test_tree_hash_is_path_and_content_sensitive_but_ignores_bytecode(tmp_path):
    first = tmp_path / "first"
    first.mkdir()
    (first / "a.txt").write_text("same", encoding="utf-8")
    original = exporter._sha256_tree(first)

    cache = first / "__pycache__"
    cache.mkdir()
    (cache / "ignored.pyc").write_bytes(b"ignored")
    assert exporter._sha256_tree(first) == original

    (first / "a.txt").rename(first / "b.txt")
    assert exporter._sha256_tree(first) != original


def test_corpus_rejects_duplicate_case_ids(tmp_path):
    corpus = tmp_path / "corpus.json"
    corpus.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "cases": [
                    {"id": "duplicate", "text": "猫"},
                    {"id": "duplicate", "text": "犬"},
                ],
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(exporter.GoldenExportError, match="duplicate corpus case id"):
        exporter._load_corpus(corpus)


def test_cli_requires_explicit_unidic_provenance():
    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT_PATH),
            "--engine-root",
            str(REPOSITORY_ROOT),
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 2
    assert "the following arguments are required: --dicdir" in result.stderr


def test_callable_boundary_rejects_ambient_unidic():
    with pytest.raises(exporter.GoldenExportError, match="--dicdir is required"):
        exporter.build_goldens(
            engine_root=REPOSITORY_ROOT,
            corpus_path=CORPUS_PATH,
            dicdir=None,
            assets={},
        )


def test_seeded_export_has_exact_contract_and_no_host_paths(tmp_path):
    output_path = tmp_path / "engine-goldens.json"
    subprocess.run(
        [
            sys.executable,
            str(SCRIPT_PATH),
            "--engine-root",
            str(REPOSITORY_ROOT),
            "--corpus",
            str(CORPUS_PATH),
            "--dicdir",
            unidic_lite.DICDIR,
            "--output",
            str(output_path),
        ],
        check=True,
    )
    result = json.loads(output_path.read_text(encoding="utf-8"))

    assert result["schema_version"] == 1
    assert tuple(result["unidic_feature_fields"]) == exporter.UNIDIC_FEATURE_FIELDS
    assert set(result["cases"]) == set(exporter.CASE_SECTIONS)
    assert len(result["cases"]["tokenization"]) == 8
    assert len(result["cases"]["morphology"]) == 8
    assert [case["id"] for case in result["cases"]["compounds"]] == ["compound-hashiridasu"]
    assert set(result["provenance"]["data"]["assets_sha256"]) == {"unidic_dicdir"}

    astral = next(case for case in result["cases"]["tokenization"] if case["id"] == "astral-oov-offsets")
    oov = next(token for token in astral["tokens"] if token["surface"] == "𠮟𠮟𠮟")
    assert oov["is_unknown"] is True
    assert oov["features"]["orthBase"] is None
    assert oov["offsets"] == {
        "codepoint_start": 1,
        "codepoint_end": 4,
        "utf16_start": 1,
        "utf16_end": 7,
    }

    rendered = json.dumps(result, ensure_ascii=False)
    assert str(REPOSITORY_ROOT) not in rendered
    assert len(result["provenance"]["engine"]["revision"]) == 40
    for section in ("engine", "tool", "runtime", "data"):
        for key, value in result["provenance"][section].items():
            if key.endswith("sha256") and isinstance(value, str):
                assert len(value) == 64
