"""Tests for anki_miner.gui.utils.dialog_paths.resolve_start_dir."""

from __future__ import annotations

from pathlib import Path

from anki_miner.gui.utils.dialog_paths import resolve_start_dir

# ---------------------------------------------------------------------------
# file_mode=True: start in the parent of current
# ---------------------------------------------------------------------------


def test_file_mode_existing_file_returns_parent(tmp_path: Path) -> None:
    """file_mode=True with an existing file returns the file's parent dir."""
    f = tmp_path / "episode.mkv"
    f.touch()
    assert resolve_start_dir(str(f), file_mode=True) == str(tmp_path)


# ---------------------------------------------------------------------------
# file_mode=False: start in current itself when it's an existing dir
# ---------------------------------------------------------------------------


def test_folder_mode_existing_dir_returns_itself(tmp_path: Path) -> None:
    """file_mode=False with an existing dir returns that dir."""
    assert resolve_start_dir(str(tmp_path), file_mode=False) == str(tmp_path)


# ---------------------------------------------------------------------------
# Nonexistent deep path: walks up to first existing ancestor
# ---------------------------------------------------------------------------


def test_walks_up_to_existing_ancestor(tmp_path: Path) -> None:
    """A nonexistent deep path resolves to the deepest existing ancestor."""
    deep = tmp_path / "a" / "b" / "c"
    # tmp_path exists, a/b/c does not
    result = resolve_start_dir(str(deep), file_mode=True)
    assert result == str(tmp_path)


def test_walks_up_folder_mode_nonexistent(tmp_path: Path) -> None:
    """file_mode=False with a nonexistent path also walks up."""
    deep = tmp_path / "x" / "y"
    result = resolve_start_dir(str(deep), file_mode=False)
    assert result == str(tmp_path)


# ---------------------------------------------------------------------------
# Empty / None / whitespace current falls back to default_dir
# ---------------------------------------------------------------------------


def test_empty_string_uses_default_dir(tmp_path: Path) -> None:
    """Empty current string falls back to an existing default_dir."""
    assert resolve_start_dir("", file_mode=True, default_dir=tmp_path) == str(tmp_path)


def test_none_current_uses_default_dir(tmp_path: Path) -> None:
    """None current falls back to an existing default_dir."""
    assert resolve_start_dir(None, file_mode=True, default_dir=tmp_path) == str(tmp_path)


def test_whitespace_only_current_uses_default_dir(tmp_path: Path) -> None:
    """Whitespace-only current is treated as empty and falls back to default_dir."""
    assert resolve_start_dir("   ", file_mode=True, default_dir=tmp_path) == str(tmp_path)


# ---------------------------------------------------------------------------
# Nothing valid → home
# ---------------------------------------------------------------------------


def test_no_valid_input_returns_home() -> None:
    """Empty current and no default_dir returns Path.home()."""
    assert resolve_start_dir("", file_mode=True) == str(Path.home())


def test_nonexistent_default_dir_returns_home(tmp_path: Path) -> None:
    """Nonexistent default_dir still falls through to home."""
    missing = tmp_path / "does_not_exist"
    assert resolve_start_dir(None, file_mode=True, default_dir=missing) == str(Path.home())


def test_none_default_dir_returns_home() -> None:
    """default_dir=None explicitly falls through to home."""
    assert resolve_start_dir(None, file_mode=True, default_dir=None) == str(Path.home())


# ---------------------------------------------------------------------------
# Result is never "/"
# ---------------------------------------------------------------------------


def test_result_never_root_for_empty_current() -> None:
    result = resolve_start_dir("", file_mode=True)
    assert result != "/"


def test_result_never_root_for_none() -> None:
    result = resolve_start_dir(None, file_mode=False)
    assert result != "/"


def test_result_never_root_for_nonexistent_path(tmp_path: Path) -> None:
    deep = tmp_path / "a" / "b"
    result = resolve_start_dir(str(deep), file_mode=True)
    assert result != "/"


def test_result_never_root_with_default_dir(tmp_path: Path) -> None:
    result = resolve_start_dir("", file_mode=False, default_dir=tmp_path)
    assert result != "/"


# ---------------------------------------------------------------------------
# Regression: absolute path whose only existing ancestor is "/" → never root
# ---------------------------------------------------------------------------


def test_vanished_mount_absolute_path_never_root(tmp_path: Path) -> None:
    # absolute path whose only existing ancestor is "/" → must not return "/"
    result = resolve_start_dir(
        "/definitely_absent_mount_xyz/episode.mkv",
        file_mode=True,
        default_dir=tmp_path,
    )
    assert result == str(tmp_path)
    result2 = resolve_start_dir("/definitely_absent_mount_xyz/episode.mkv", file_mode=True)
    assert result2 == str(Path.home())
    assert result2 != "/"


def test_root_path_directly_never_returned(tmp_path: Path) -> None:
    # Passing "/" itself as current must fall through to default_dir/home
    result = resolve_start_dir("/", file_mode=False, default_dir=tmp_path)
    assert result == str(tmp_path)
    assert result != "/"


# ---------------------------------------------------------------------------
# file_mode=False + existing FILE: uses the file's parent dir
# ---------------------------------------------------------------------------


def test_folder_mode_existing_file_returns_parent(tmp_path: Path) -> None:
    """file_mode=False with an existing file falls back to its parent dir."""
    f = tmp_path / "subtitles.srt"
    f.touch()
    result = resolve_start_dir(str(f), file_mode=False)
    assert result == str(tmp_path)


# ---------------------------------------------------------------------------
# remembered_dir: the folder last ACCEPTED for this workflow and role (D7)
# ---------------------------------------------------------------------------


def test_remembered_dir_is_used_when_the_field_is_empty(tmp_path: Path) -> None:
    remembered = tmp_path / "library"
    remembered.mkdir()
    assert resolve_start_dir(None, file_mode=True, remembered_dir=remembered) == str(remembered)


def test_a_valid_field_path_outranks_the_remembered_dir(tmp_path: Path) -> None:
    """What you are working on now beats where you were last time."""
    remembered = tmp_path / "library"
    remembered.mkdir()
    current = tmp_path / "current" / "episode.mkv"
    current.parent.mkdir()
    current.touch()

    result = resolve_start_dir(str(current), file_mode=True, remembered_dir=remembered)

    assert result == str(current.parent)


def test_remembered_dir_outranks_the_configured_default(tmp_path: Path) -> None:
    """A default is a guess; a remembered folder is evidence."""
    remembered = tmp_path / "library"
    default = tmp_path / "configured"
    remembered.mkdir()
    default.mkdir()

    result = resolve_start_dir(None, file_mode=False, remembered_dir=remembered, default_dir=default)

    assert result == str(remembered)


def test_a_deleted_remembered_dir_falls_through_to_the_default(tmp_path: Path) -> None:
    default = tmp_path / "configured"
    default.mkdir()

    result = resolve_start_dir(
        None,
        file_mode=False,
        remembered_dir=tmp_path / "unmounted",
        default_dir=default,
    )

    assert result == str(default)


def test_a_deleted_remembered_dir_with_no_default_falls_through_to_home(tmp_path: Path) -> None:
    assert resolve_start_dir(None, file_mode=True, remembered_dir=tmp_path / "gone") == str(Path.home())


def test_a_remembered_path_that_is_a_file_is_ignored(tmp_path: Path) -> None:
    """Only directories are remembered; anything else is not a start dir."""
    stray = tmp_path / "not-a-folder.srt"
    stray.touch()

    assert resolve_start_dir(None, file_mode=True, remembered_dir=stray) == str(Path.home())


def test_a_remembered_dir_is_never_walked_up_from(tmp_path: Path) -> None:
    """The ancestor of a folder chosen once is not somewhere the user asked for."""
    default = tmp_path / "configured"
    default.mkdir()

    result = resolve_start_dir(
        None,
        file_mode=False,
        remembered_dir=tmp_path / "series" / "season-2",
        default_dir=default,
    )

    assert result == str(default)


def test_all_four_levels_in_order(tmp_path: Path) -> None:
    field = tmp_path / "field"
    remembered = tmp_path / "remembered"
    default = tmp_path / "default"
    for d in (field, remembered, default):
        d.mkdir()

    assert resolve_start_dir(str(field), file_mode=False, remembered_dir=remembered, default_dir=default) == str(field)
    assert resolve_start_dir(None, file_mode=False, remembered_dir=remembered, default_dir=default) == str(remembered)
    assert resolve_start_dir(None, file_mode=False, remembered_dir=None, default_dir=default) == str(default)
    assert resolve_start_dir(None, file_mode=False) == str(Path.home())
