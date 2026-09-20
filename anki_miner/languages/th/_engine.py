"""Environment guard imported before any pythainlp import in this package.

``PYTHAINLP_OFFLINE`` blocks the runtime corpus catalogue (no network).
``PYTHAINLP_READ_ONLY`` stops the library creating ``$HOME/pythainlp-data`` --
measured: offline alone still creates it, and Anki Miner writes nothing outside
ANKI_MINER_HOME. A test that created a directory in the real ``$HOME`` is exactly
what ``tests/_home_isolation.py`` exists to prevent.

Both are read by pythainlp at ITS import time, so they have to be set before the
first one anywhere in the process -- including the single
``from pythainlp.util import reorder_vowels`` in ``normalize.py``, which is
reachable without ``tokenizer.py`` ever being imported. Hence one module that
every door imports first, rather than the same two lines in three places.

``setdefault``, not assignment: a user who deliberately set either variable keeps
their value.
"""

from __future__ import annotations

import os

os.environ.setdefault("PYTHAINLP_OFFLINE", "1")
os.environ.setdefault("PYTHAINLP_READ_ONLY", "1")
