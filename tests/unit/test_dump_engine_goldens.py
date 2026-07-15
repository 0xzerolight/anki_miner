from __future__ import annotations

import importlib.util
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
import unidic_lite

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = REPOSITORY_ROOT / "scripts" / "dump_engine_goldens.py"
CORPUS_PATH = REPOSITORY_ROOT / "tests" / "fixtures" / "goldens" / "tokenizer-v1.json"
RUNTIME_LOCK_PATH = REPOSITORY_ROOT / "scripts" / "golden-runtime-requirements.txt"
GOLDEN_WORKFLOW_PATH = REPOSITORY_ROOT / ".github" / "workflows" / "android-engine-goldens.yml"
PINNED_ENGINE_REVISION = "ba3b3cfbcc53e57a440c8b9f157209851408c62a"


def _load_exporter():
    spec = importlib.util.spec_from_file_location("dump_engine_goldens", SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


exporter = _load_exporter()


def test_repository_contains_pinned_engine_revision():
    result = subprocess.run(
        ["git", "-C", str(REPOSITORY_ROOT), "cat-file", "-e", f"{PINNED_ENGINE_REVISION}^{{commit}}"],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, "CI checkout must fetch full history for the pinned Android engine revision"


@pytest.fixture(scope="session")
def clean_engine_root(tmp_path_factory: pytest.TempPathFactory) -> Path:
    destination = tmp_path_factory.mktemp("golden-engine") / "checkout"
    subprocess.run(
        ["git", "clone", "--quiet", "--no-checkout", str(REPOSITORY_ROOT), str(destination)],
        check=True,
    )
    subprocess.run(
        ["git", "-C", str(destination), "checkout", "--quiet", PINNED_ENGINE_REVISION],
        check=True,
    )
    return destination


def _clone_engine(source: Path, destination: Path) -> Path:
    subprocess.run(["git", "clone", "--quiet", "--no-checkout", str(source), str(destination)], check=True)
    subprocess.run(
        ["git", "-C", str(destination), "checkout", "--quiet", PINNED_ENGINE_REVISION],
        check=True,
    )
    return destination


def _export_command(engine_root: Path, output_path: Path) -> list[str]:
    return [
        sys.executable,
        str(SCRIPT_PATH),
        "--engine-root",
        str(engine_root),
        "--corpus",
        str(CORPUS_PATH),
        "--dicdir",
        unidic_lite.DICDIR,
        "--compact",
        "--output",
        str(output_path),
    ]


def _token(surface: str):
    feature = SimpleNamespace(**dict.fromkeys(exporter.UNIDIC_FEATURE_FIELDS))
    return SimpleNamespace(surface=surface, feature=feature, is_unk=True)


def _write_corpus(path: Path, payload: object) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def _canonical_hash_payload(value: dict[str, object]) -> str:
    without_hash = dict(value)
    without_hash.pop("sha256")
    return exporter._sha256_bytes(exporter._canonical_json_bytes(without_hash))


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


def test_token_record_requires_all_26_unidic_fields():
    token = _token("猫")
    del token.feature.aModeType
    with pytest.raises(exporter.GoldenExportError, match="missing UniDic fields: aModeType"):
        exporter._token_record("猫", token, 0, 1)


def test_token_locator_permits_only_whitespace_gaps():
    text = "猫 \n 猫"
    located = exporter._locate_tokens(text, [_token("猫"), _token("猫")])
    assert [(start, end) for _token_value, start, end in located] == [(0, 1), (4, 5)]

    for invalid_text, tokens, message in (
        ("猫犬猫", [_token("猫"), _token("猫")], "omitted non-whitespace"),
        ("猫犬", [_token("猫")], "omitted trailing non-whitespace"),
        ("猫犬", [_token("犬"), _token("猫")], "omitted non-whitespace"),
        ("猫", [_token("")], "must not be empty"),
    ):
        with pytest.raises(exporter.GoldenExportError, match=message):
            exporter._locate_tokens(invalid_text, tokens)


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


def test_tree_hash_rejects_symlinks_and_special_files(tmp_path):
    tree = tmp_path / "tree"
    tree.mkdir()
    target = tmp_path / "target"
    target.write_text("outside", encoding="utf-8")
    (tree / "linked").symlink_to(target)
    with pytest.raises(exporter.GoldenExportError, match="symlinks are forbidden"):
        exporter._sha256_tree(tree)

    (tree / "linked").unlink()
    fifo = tree / "fifo"
    os.mkfifo(fifo)
    with pytest.raises(exporter.GoldenExportError, match="non-regular tree entry"):
        exporter._sha256_tree(tree)


def test_distribution_hash_includes_sibling_native_artifacts(tmp_path):
    package = tmp_path / "example"
    native_directory = tmp_path / "example.libs"
    metadata = tmp_path / "example-1.0.dist-info"
    package.mkdir()
    native_directory.mkdir()
    metadata.mkdir()
    module = package / "__init__.py"
    native = native_directory / "libexample.so"
    record = metadata / "RECORD"
    module.write_text("VALUE = 1\n", encoding="utf-8")
    native.write_bytes(b"native-one")
    record.write_text("environment-specific install record", encoding="utf-8")
    entries = [Path("example/__init__.py"), Path("example.libs/libexample.so"), Path("example-1.0.dist-info/RECORD")]
    distribution = SimpleNamespace(
        files=entries,
        metadata={"Name": "example"},
        locate_file=lambda entry: tmp_path / entry,
    )

    files = exporter._distribution_file_map(distribution)
    assert set(files) == {"example/__init__.py", "example.libs/libexample.so"}
    original = exporter._sha256_named_files(files)
    native.write_bytes(b"native-two")
    assert exporter._sha256_named_files(files) != original


def test_corpus_rejects_duplicate_case_ids(tmp_path):
    corpus = tmp_path / "corpus.json"
    case = {
        "id": "duplicate",
        "text": "猫",
        "coverage": ["duplicate-check"],
        "expect": {"token": {"surface": "猫"}},
    }
    _write_corpus(corpus, {"schema_version": 1, "cases": [case, dict(case)]})

    with pytest.raises(exporter.GoldenExportError, match="duplicate corpus case id"):
        exporter._load_corpus(corpus)


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (lambda payload: payload.update({"unknown": True}), "corpus root has unknown keys"),
        (lambda payload: payload["cases"][0].update({"unknown": True}), "corpus case has unknown keys"),
        (
            lambda payload: payload["cases"][0]["expect"].update({"unknown": True}),
            "expect has unknown keys",
        ),
        (
            lambda payload: payload["cases"][0]["expect"]["token"].update({"orth_base": "猫"}),
            "expect.token has unknown keys",
        ),
        (
            lambda payload: payload["cases"][0]["expect"].update({"token": None, "word": {"surface": "猫"}}),
            "expect.token must be an object",
        ),
    ],
)
def test_corpus_schema_is_closed(tmp_path, mutation, message):
    payload = {
        "schema_version": 1,
        "cases": [
            {
                "id": "closed",
                "text": "猫",
                "coverage": ["schema"],
                "expect": {"token": {"surface": "猫"}},
            }
        ],
    }
    mutation(payload)
    corpus = tmp_path / "corpus.json"
    _write_corpus(corpus, payload)
    with pytest.raises(exporter.GoldenExportError, match=message):
        exporter._load_corpus(corpus)


def test_section_status_freezes_implemented_and_pending_contract():
    cases = {section: [] for section in exporter.CASE_SECTIONS}
    for section in ("tokenization", "morphology", "compounds"):
        cases[section] = [{"id": section}]

    status = exporter._validated_section_status(cases)
    assert {name for name, value in status.items() if value["state"] == "implemented"} == {
        "tokenization",
        "morphology",
        "compounds",
    }
    assert all(value.get("reason", "").strip() for value in status.values() if value["state"] == "pending")

    cases["filtering"] = [{"id": "cannot-look-covered"}]
    with pytest.raises(exporter.GoldenExportError, match="pending golden section unexpectedly contains records"):
        exporter._validated_section_status(cases)


def test_ci_runtime_lock_covers_every_hashed_distribution():
    pins = {
        line.split("==", 1)[0].lower().replace("_", "-")
        for line in RUNTIME_LOCK_PATH.read_text(encoding="utf-8").splitlines()
        if line and not line.startswith("#")
    }
    expected = {name.lower().replace("_", "-") for name, _imports in exporter.RUNTIME_DISTRIBUTIONS}
    assert pins == expected
    assert all(
        "==" in line for line in RUNTIME_LOCK_PATH.read_text(encoding="utf-8").splitlines() if line[:1].isalpha()
    )


def test_golden_workflow_pins_python_patch_version():
    workflow = GOLDEN_WORKFLOW_PATH.read_text(encoding="utf-8")
    assert re.findall(r'^\s+python-version: "([0-9.]+)"$', workflow, flags=re.MULTILINE) == ["3.13.7"]


def test_cli_requires_explicit_unidic_provenance(clean_engine_root):
    result = subprocess.run(
        [sys.executable, str(SCRIPT_PATH), "--engine-root", str(clean_engine_root)],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 2
    assert "the following arguments are required: --dicdir" in result.stderr


def test_callable_boundary_rejects_preloaded_engine(clean_engine_root):
    code = f"""
import importlib.util
import sys
from pathlib import Path
sys.path.insert(0, {str(REPOSITORY_ROOT)!r})
import anki_miner.config
spec = importlib.util.spec_from_file_location('golden_exporter', {str(SCRIPT_PATH)!r})
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
try:
    module.build_goldens(
        engine_root=Path({str(clean_engine_root)!r}),
        corpus_path=Path({str(CORPUS_PATH)!r}),
        dicdir=Path({unidic_lite.DICDIR!r}),
        assets={{}},
    )
except module.GoldenExportError as exc:
    print(exc)
    raise SystemExit(0)
raise SystemExit(1)
"""
    result = subprocess.run([sys.executable, "-c", code], check=False, capture_output=True, text=True)
    assert result.returncode == 0
    assert "engine modules were imported before isolation" in result.stdout


def test_callable_derivation_cleans_engine_modules(clean_engine_root):
    code = f"""
import importlib.util
import json
import sys
from pathlib import Path
spec = importlib.util.spec_from_file_location('golden_exporter', {str(SCRIPT_PATH)!r})
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
result = module.build_goldens(
    engine_root=Path({str(clean_engine_root)!r}),
    corpus_path=Path({str(CORPUS_PATH)!r}),
    dicdir=Path({unidic_lite.DICDIR!r}),
    assets={{}},
)
print(json.dumps({{
    'revision': result['provenance']['engine']['revision'],
    'remaining': sorted(name for name in sys.modules if name == 'anki_miner' or name.startswith('anki_miner.')),
}}))
"""
    result = subprocess.run([sys.executable, "-c", code], check=True, capture_output=True, text=True)
    payload = json.loads(result.stdout)
    assert payload == {"revision": PINNED_ENGINE_REVISION, "remaining": []}
    ignored = subprocess.run(
        ["git", "-C", str(clean_engine_root), "ls-files", "--others", "--ignored", "--exclude-standard"],
        check=True,
        capture_output=True,
        text=True,
    )
    assert ignored.stdout == ""


def test_engine_root_must_be_clean_git_toplevel(clean_engine_root, tmp_path):
    with pytest.raises(exporter.GoldenExportError, match="must be the Git top level"):
        exporter._engine_revision(clean_engine_root / "anki_miner")

    dirty_root = _clone_engine(clean_engine_root, tmp_path / "dirty")
    (dirty_root / "fugashi.py").write_text("raise RuntimeError('must never import')\n", encoding="utf-8")
    result = subprocess.run(
        _export_command(dirty_root, tmp_path / "dirty.json"),
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 2
    assert "engine checkout is not clean" in result.stderr
    assert "fugashi.py" in result.stderr

    ignored_root = _clone_engine(clean_engine_root, tmp_path / "ignored")
    (ignored_root / ".git" / "info" / "exclude").write_text("fugashi.py\n", encoding="utf-8")
    (ignored_root / "fugashi.py").write_text("raise RuntimeError('must never import')\n", encoding="utf-8")
    ignored_result = subprocess.run(
        _export_command(ignored_root, tmp_path / "ignored.json"),
        check=False,
        capture_output=True,
        text=True,
    )
    assert ignored_result.returncode == 2
    assert "engine checkout contains ignored files" in ignored_result.stderr
    assert "fugashi.py" in ignored_result.stderr


def test_input_root_symlinks_are_rejected(tmp_path):
    target = tmp_path / "target"
    target.mkdir()
    linked = tmp_path / "linked"
    linked.symlink_to(target, target_is_directory=True)
    with pytest.raises(exporter.GoldenExportError, match="must not be a symlink"):
        exporter._normalise_input_path(linked, label="test input")


def test_tagger_requires_mecabrc_and_rejects_external_dictionary_metadata(tmp_path, monkeypatch):
    dicdir = tmp_path / "dicdir"
    dicdir.mkdir()
    (dicdir / "sys.dic").write_bytes(b"placeholder")
    with pytest.raises(exporter.GoldenExportError, match="has no mecabrc"):
        exporter._make_tagger(dicdir)

    (dicdir / "mecabrc").write_text("", encoding="utf-8")
    outside = tmp_path / "outside.dic"
    outside.write_bytes(b"outside")

    class WrongTagger:
        dictionary_info = [{"filename": str(outside)}]

    import fugashi

    monkeypatch.setattr(fugashi, "Tagger", lambda _arguments: WrongTagger())
    with pytest.raises(exporter.GoldenExportError, match="outside --dicdir"):
        exporter._make_tagger(dicdir)


def test_seeded_export_has_exact_contract_and_is_byte_repeatable(clean_engine_root, tmp_path):
    first_path = tmp_path / "engine-goldens-first.json"
    second_path = tmp_path / "engine-goldens-second.json"
    subprocess.run(_export_command(clean_engine_root, first_path), check=True)
    subprocess.run(_export_command(clean_engine_root, second_path), check=True)

    assert first_path.read_bytes() == second_path.read_bytes()
    result = json.loads(first_path.read_text(encoding="utf-8"))

    assert result["schema_version"] == 1
    assert tuple(result["unidic_feature_fields"]) == exporter.UNIDIC_FEATURE_FIELDS
    assert set(result["cases"]) == set(exporter.CASE_SECTIONS)
    assert set(result["section_status"]) == set(exporter.CASE_SECTIONS)
    assert len(result["cases"]["tokenization"]) == 8
    assert len(result["cases"]["morphology"]) == 8
    assert [case["id"] for case in result["cases"]["compounds"]] == ["compound-hashiridasu"]
    assert set(result["provenance"]["data"]["assets_sha256"]) == {"unidic_dicdir"}
    assert result["provenance"]["engine"]["revision"] == PINNED_ENGINE_REVISION

    for section, status in result["section_status"].items():
        if status["state"] == "implemented":
            assert result["cases"][section]
        else:
            assert status["state"] == "pending"
            assert status["reason"].strip()
            assert result["cases"][section] == []

    astral = next(case for case in result["cases"]["tokenization"] if case["id"] == "astral-oov-offsets")
    oov = next(token for token in astral["tokens"] if token["surface"] == "𠮟𠮟𠮟")
    assert oov["is_unknown"] is True
    assert oov["features"]["orthBase"] is None
    assert len(oov["features"]) == 26
    assert oov["offsets"] == {
        "codepoint_start": 1,
        "codepoint_end": 4,
        "utf16_start": 1,
        "utf16_end": 7,
    }
    assert all(len(token["features"]) == 26 for case in result["cases"]["tokenization"] for token in case["tokens"])

    compound = result["cases"]["compounds"][0]
    merged = next(word for word in compound["output"]["words"] if word["surface"] == "走り出し")
    assert {
        key: merged[key]
        for key in ("surface", "lemma", "orth_base", "mined_form", "surface_start", "surface_end", "highlight_end")
    } == {
        "surface": "走り出し",
        "lemma": "走り出す",
        "orth_base": "走り出す",
        "mined_form": "走り出す",
        "surface_start": 4,
        "surface_end": 8,
        "highlight_end": 9,
    }

    runtime = result["provenance"]["runtime"]
    assert runtime["sha256"] == _canonical_hash_payload(runtime)
    assert set(runtime["dependencies"]) == {name for name, _imports in exporter.RUNTIME_DISTRIBUTIONS}
    for dependency in runtime["dependencies"].values():
        assert set(dependency) == {"version", "content_sha256"}
        assert dependency["version"]
        assert len(dependency["content_sha256"]) == 64
    data = result["provenance"]["data"]
    assert data["sha256"] == _canonical_hash_payload(data)
    assert len(data["assets_sha256"]["unidic_dicdir"]) == 64

    rendered = json.dumps(result, ensure_ascii=False)
    for host_path in (str(REPOSITORY_ROOT), str(clean_engine_root), unidic_lite.DICDIR):
        assert host_path not in rendered
    for section in ("engine", "tool", "runtime", "data"):
        assert (
            len(
                result["provenance"][section][
                    "sha256" if section in {"runtime", "data"} else "tree_sha256" if section == "engine" else "sha256"
                ]
            )
            == 64
        )
