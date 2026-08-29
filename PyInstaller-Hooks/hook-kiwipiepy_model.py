"""PyInstaller hook for kiwipiepy_model (the default Kiwi model).

The model files are pure data inside the package directory; Kiwi() resolves them
through the installed package, so they must land at the same package path in the
frozen tree. Harmless when the package is absent.
"""

import importlib.util

datas: list[tuple[str, str]] = []

try:
    _spec = importlib.util.find_spec("kiwipiepy_model")
except Exception:  # noqa: BLE001 - a broken/absent install means "nothing to collect"
    _spec = None

if _spec is not None:
    from PyInstaller.utils.hooks import collect_data_files

    datas = collect_data_files("kiwipiepy_model", include_py_files=False)
