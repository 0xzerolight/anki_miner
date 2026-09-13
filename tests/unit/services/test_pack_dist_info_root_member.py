"""S18: a ``*.dist-info/`` root member extracts beside the package and is discoverable.

pymorphy3 finds its dictionaries through entry points, which importlib.metadata
reads from a dist-info directory on sys.path — the pack root.
"""

from __future__ import annotations

import importlib.metadata
import zipfile

from anki_miner.languages.pack_spec import ArtifactSpec, PackComponent
from anki_miner.services.pack_installer import _extract_component, component_complete


def test_a_dist_info_directory_member_is_promoted_and_read(tmp_path):
    wheel = tmp_path / "zz_dicts-1.0-py3-none-any.whl"
    with zipfile.ZipFile(wheel, "w") as zf:
        zf.writestr("zz_dicts/__init__.py", "")
        zf.writestr("zz_dicts/data/words.dawg", "x")
        zf.writestr("zz_dicts-1.0.dist-info/METADATA", "Metadata-Version: 2.1\nName: zz_dicts\nVersion: 1.0\n")
        zf.writestr("zz_dicts-1.0.dist-info/entry_points.txt", "[zz_dictionaries]\nzz = zz_dicts\n")
    spec = ArtifactSpec(
        url="https://example.invalid/zz_dicts-1.0-py3-none-any.whl",
        sha256="0" * 64,
        kind="wheel",
        member_prefix="zz_dicts/",
        root_members=("zz_dicts-1.0.dist-info/",),
    )
    comp = PackComponent(import_name="zz_dicts", required=True, sentinels=("__init__.py",), universal=spec)
    root = tmp_path / "pack"
    root.mkdir()

    _extract_component(wheel, root, comp, spec)

    assert component_complete(root, comp)
    (dist,) = [d for d in importlib.metadata.distributions(path=[str(root)]) if d.metadata["Name"] == "zz_dicts"]
    assert [ep.value for ep in dist.entry_points if ep.group == "zz_dictionaries"] == ["zz_dicts"]
