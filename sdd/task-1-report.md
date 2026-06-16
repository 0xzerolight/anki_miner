# Task 1 Report: Drop broken force-software-decode for AV1 preview

## Changes

### `anki_miner/gui/app.py`
- Deleted `_force_software_video_decode()` function (lines 44–54): the 11-line def
  that set `QT_FFMPEG_DECODING_HW_DEVICE_TYPES=","` via `os.environ.setdefault`.
- Deleted its call in `main()` (was line 111, right after `_scrub_pyinstaller_env()`).
- Applied `black` reformatting (removed one blank line left by the deletion).
- No other code was touched; `os` import retained (used elsewhere in the module).

### `tests/unit/test_app_env_scrub.py`
- Removed `_force_software_video_decode` from the import line.
- Removed `_HW_VAR = "QT_FFMPEG_DECODING_HW_DEVICE_TYPES"` module-level constant.
- Removed `test_forces_software_decode_when_unset` and `test_does_not_override_user_value`.
- All four remaining `_scrub_pyinstaller_env` tests kept unchanged.

## Test output

```
============================= test session starts ==============================
platform linux -- Python 3.13.7
collected 4 items

tests/unit/test_app_env_scrub.py ....                                    [100%]

============================== 4 passed in 0.42s ===============================
```

## Lint/format

`ruff check` and `black --check` both pass on both files.
