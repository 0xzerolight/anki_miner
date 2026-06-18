"""Configuration for the end-to-end GUI test harness.

``E2EConfig`` is a frozen dataclass (mirroring the project's ``AnkiMinerConfig``
convention) holding harness-wide settings: an isolated test home, the
distinctive throwaway deck name that doubles as a collision safeguard, the
AnkiConnect endpoint, and curation/timeout knobs. Defaults are overridable via
documented environment variables through :meth:`E2EConfig.from_env`.
"""

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

# Environment variable names recognised by ``from_env``. Centralised so the
# standalone runner and the tests reference the same strings.
ENV_HOME = "ANKI_MINER_E2E_HOME"
ENV_ANKICONNECT_URL = "ANKI_MINER_E2E_ANKICONNECT_URL"

#: Single source of truth for valid curation policy names.
CURATION_POLICIES: tuple[str, ...] = ("all", "first_n", "none")


def validate_curation_policy(policy: str, first_n: int) -> None:
    """Raise ``ValueError`` if *policy* / *first_n* is not a valid combination.

    Shared by :class:`E2EConfig` and :class:`~tests.e2e.curation.AutoCurationResponder`
    so the allowed values and error messages have a single source of truth.

    Raises:
        ValueError: unknown policy name, or ``first_n`` policy with ``first_n <= 0``.
    """
    if policy not in CURATION_POLICIES:
        raise ValueError(f"curation_policy must be one of 'all', 'first_n', 'none', got {policy!r}")
    if policy == "first_n" and first_n <= 0:
        raise ValueError(f"curation_policy 'first_n' requires first_n > 0, got {first_n}")


#: Distinctive deck name used by the harness. The verbose, space-laden name is
#: a SAFETY FEATURE: it must be hard to collide with a deck a real user already
#: has, so the cleanup/mutation paths can never plausibly nuke real study data.
DEFAULT_DECK_NAME = "AnkiMiner E2E TEST"


@dataclass(frozen=True)
class E2EConfig:
    """Immutable settings for one E2E harness session.

    Frozen for the same reason ``AnkiMinerConfig`` is: it is read from multiple
    places (and potentially threads) and must never be mutated in place. Use
    ``dataclasses.replace()`` to derive a variant.
    """

    #: Isolated home directory for harness state (kept away from the real
    #: ``~/.anki_miner``). Env override: ``ANKI_MINER_E2E_HOME``.
    test_home: Path = field(default_factory=lambda: Path.home() / ".anki_miner_e2e")
    #: The throwaway deck the harness creates, fills, reads back, and deletes.
    #: Its distinctiveness is a guard against touching a real deck.
    deck_name: str = DEFAULT_DECK_NAME
    #: AnkiConnect endpoint. Must be loopback (enforced by the gateway).
    #: Env override: ``ANKI_MINER_E2E_ANKICONNECT_URL``.
    ankiconnect_url: str = "http://127.0.0.1:8765"
    #: Anki note type the harness builds cards against.  The distinctive name
    #: avoids colliding with stock "Basic" and signals harness ownership.
    note_type: str = "AnkiMiner E2E Basic"
    #: How many mined candidates to curate into cards per run.
    #: ``"all"`` keeps everything, ``"first_n"`` keeps the first ``first_n``,
    #: ``"none"`` curates nothing.
    curation_policy: Literal["all", "first_n", "none"] = "all"
    #: Cap used when ``curation_policy == "first_n"``.
    first_n: int = 0
    #: Per-result wait budget (seconds) for the harness result reader.
    result_timeout_s: int = 120
    #: Whole-session wait budget (seconds).
    session_timeout_s: int = 300
    #: Where per-run artifacts/output trees are written. Defaults under
    #: ``test_home``; see ``__post_init__`` for the derivation.
    runs_root: Path = field(default=None)  # type: ignore[assignment]

    def __post_init__(self) -> None:
        """Normalise paths, validate the curation policy, derive ``runs_root``.

        Frozen dataclass: assignment goes through ``object.__setattr__`` (the
        supported pattern, see ``AnkiMinerConfig.__post_init__``).
        """
        # Validate the curation policy up front so a misconfigured run can't
        # silently mine nothing. ``first_n`` of 0 would behave like ``"none"``
        # but give no signal, so it is rejected loudly.
        validate_curation_policy(self.curation_policy, self.first_n)
        if isinstance(self.test_home, str):
            object.__setattr__(self, "test_home", Path(self.test_home))
        # Derive runs_root from the (possibly env-overridden) test_home unless
        # the caller pinned an explicit one. Default sentinel is None so that an
        # explicit test_home flows through to runs_root automatically.
        if self.runs_root is None:
            object.__setattr__(self, "runs_root", self.test_home / "runs")
        elif isinstance(self.runs_root, str):
            object.__setattr__(self, "runs_root", Path(self.runs_root))

    @classmethod
    def from_env(cls) -> "E2EConfig":
        """Build a config applying the documented environment overrides.

        Only ``test_home`` and ``ankiconnect_url`` have env overrides; the rest
        use the dataclass defaults. An unset/empty env var falls through to the
        field default. ``runs_root`` is left to ``__post_init__`` so it tracks an
        overridden ``test_home``.
        """
        defaults = cls()
        home = os.environ.get(ENV_HOME)
        url = os.environ.get(ENV_ANKICONNECT_URL)
        return cls(
            test_home=Path(home) if home else defaults.test_home,
            ankiconnect_url=url or defaults.ankiconnect_url,
        )
