"""Sequencing for named settings profiles: boot reconcile, switch, create.

``gui_config.json`` stays the live config; profiles are sidecar snapshots owned
by :mod:`anki_miner.gui.utils.profile_store`. What lives here is the ORDER in
which a switch moves state, which is where every data-loss path of this feature
sits:

* the outgoing profile is snapshotted to disk BEFORE anything else moves;
* the incoming file is read BEFORE the active-profile pointer advances;
* the pointer advances BEFORE the commit and is rolled back whenever the commit
  did not reach disk (``ConfigCommitResult.persisted``) — the naive
  advance-then-commit order silently rewrites the outgoing profile with the
  incoming identity on the next save of the session;
* the ``Theme`` singleton is re-seeded BEFORE ``update_config`` fans
  ``config_refreshed`` out, because the Settings UI panel renders from the
  singleton inside that fan-out.

Storage policy (which ids are legal, what a name must look like, deletion) is
``ProfileStore``'s and dialogs call it directly, so ``rename``/``delete`` are
deliberately NOT methods here. This class is sequencing only.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import TYPE_CHECKING, Protocol, cast

from PyQt6.QtCore import QCoreApplication
from PyQt6.QtWidgets import QApplication, QMessageBox

from anki_miner import __version__
from anki_miner.config import AnkiMinerConfig
from anki_miner.gui.resources.styles.theme import Theme
from anki_miner.gui.utils.config_commit import ConfigCommitError
from anki_miner.gui.utils.config_manager import GUIConfigManager
from anki_miner.gui.utils.profile_store import Profile, ProfileStore
from anki_miner.utils.i18n import tr_format

if TYPE_CHECKING:
    from anki_miner.gui.main_window import MainWindow

logger = logging.getLogger(__name__)

# Identity of the profile every existing user is silently migrated into, so a
# one-profile world looks exactly like no profiles at all. The display name is
# deliberately NOT translated: it is persisted into the profile file, and a
# translated value would freeze whatever language happened to be active at
# first launch into the file forever (and read wrong after a language change).
_DEFAULT_PROFILE_ID = "default"
_DEFAULT_PROFILE_NAME = "Default"

# Mutation kind held on the dictionary panel for the duration of a switch.
_MUTATION_KIND = "profile-switch"

# Config fields applied ONCE, before or during a single boot-time construction,
# that no live config_refreshed can re-apply — so a profile that changes one of
# them needs a restart note rather than silent divergence:
#   ui_language   -> install_translators (app.py); widgets capture tr() at build
#   ui_zoom       -> QT_SCALE_FACTOR, which Qt reads once at process start
#   themes_root   -> Theme.initialize's discovery roots
#   stats_db_path -> StatsService(...), constructed once and never rebuilt
#   log_path      -> the logging handler installed at startup
#
# Sibling field partitions, same idiom: SettingsTab._EXTERNAL_ONLY_FIELDS
# (settings_tab.py:120), SettingsTab._RESET_PRESERVE_UI (:141) and
# GUIConfigManager.machine_specific_fields() (config_manager.py:450).
_BOOT_ONLY_FIELDS = frozenset({"ui_language", "ui_zoom", "themes_root", "stats_db_path", "log_path"})


def _boot_only_values(config: AnkiMinerConfig) -> dict[str, object]:
    """Snapshot the boot-only field values of ``config``."""
    return {name: getattr(config, name) for name in _BOOT_ONLY_FIELDS}


def _boot_only_label(field: str) -> str:
    """User-facing label for a boot-only field name (falls back to the name)."""
    labels = {
        "ui_language": QCoreApplication.translate("ProfileController", "Language"),
        "ui_zoom": QCoreApplication.translate("ProfileController", "Interface scale"),
        "themes_root": QCoreApplication.translate("ProfileController", "Themes folder"),
        "stats_db_path": QCoreApplication.translate("ProfileController", "Statistics database"),
        "log_path": QCoreApplication.translate("ProfileController", "Log file"),
    }
    return labels.get(field, field)


class _ProfileHeader(Protocol):
    """The header surface a profile switch drives.

    ``HeaderWidget`` implements both — ``refresh_favorites`` already, and
    ``set_profiles`` with the profile combo. Naming the pair here keeps the
    dependency to two methods, so a test fake cannot silently drift from what
    this controller actually calls.
    """

    def set_profiles(self, profiles: Sequence[Profile], active_id: str | None) -> None: ...

    def refresh_favorites(self) -> None: ...


@dataclass(frozen=True)
class SwitchResult:
    """Outcome of a switch attempt.

    ``reason`` is a translated, user-facing message the controller has already
    shown (``QMessageBox.warning``): it is set on every refusal, and also on a
    switch that DID happen but could not fully refresh the running window. So
    ``switched`` is the branch callers act on; ``reason`` is only there for
    tests and logs. A plain no-op (already on that profile) carries neither.
    """

    switched: bool
    reason: str | None = None


@dataclass(frozen=True)
class _ThemeState:
    """The four ``Theme`` singleton fields a profile owns.

    Captured before the re-seed so a refused switch can put the singleton back
    exactly as it was — the re-seed necessarily happens BEFORE the commit that
    may fail (see :meth:`ProfileController._switch_locked`).
    """

    active: str
    favorites: tuple[str, ...]
    user_dir: Path | None
    font_scale: float

    @classmethod
    def capture(cls) -> _ThemeState:
        return cls(
            active=Theme.get_current_mode(),
            favorites=Theme.get_favorites(),
            # No public accessor exists for the user themes directory; it is
            # write-only through initialize(). Reading the attribute is
            # cheaper than widening Theme's surface for one caller.
            user_dir=Theme._user_dir,
            font_scale=Theme.get_font_scale(),
        )

    @classmethod
    def of_config(cls, config: AnkiMinerConfig) -> _ThemeState:
        return cls(
            active=config.theme,
            favorites=config.theme_favorites,
            user_dir=config.themes_root,
            font_scale=config.ui_font_scale,
        )

    def seed(self) -> None:
        """Re-seed the singleton wholesale (does NOT repaint the app).

        ``Theme.initialize`` is the only entry point that sets all four fields,
        re-runs theme discovery for a changed user dir and drops the compiled
        QSS cache. The public per-field setters are not equivalent:
        ``set_favorites`` silently drops keys that are not in the CURRENT
        discovery set, which would trim a profile's favorites to whatever the
        outgoing themes folder happened to contain.

        ``shipped_dir`` and ``state_listener`` are carried through explicitly
        because ``initialize`` resets every parameter it is not given — dropping
        them would rediscover the wrong shipped themes and detach whatever
        write-through listener the app (or a test harness) installed.
        """
        Theme.initialize(
            active=self.active,
            favorites=self.favorites,
            user_dir=self.user_dir,
            font_scale=self.font_scale,
            shipped_dir=Theme._shipped_dir_override,
            state_listener=Theme._state_listener,
        )


class ProfileController:
    """Boot reconcile plus the switch/create sequencing for settings profiles.

    Args:
        window: Owning main window. Read for the live config and driven for
            everything a switch has to move: ``_dictionary_mutation_guard``,
            ``release_dictionary_resources``, ``update_config``, the header
            combo and the status bar. Held as a reference (the
            ``BackgroundTaskController`` idiom) rather than as a bag of
            injected callables — this class needs six of them and they must all
            address the same window.
    """

    def __init__(self, window: MainWindow) -> None:
        self._window = window
        # Boot values of the restart-only fields, taken by bootstrap(). Compared
        # against on every switch so an A->B->A round trip stops warning; None
        # means bootstrap never ran, so there is no baseline to compare with.
        self._boot_only: dict[str, object] | None = None

    # ------------------------------------------------------------------
    # Boot
    # ------------------------------------------------------------------

    def bootstrap(self) -> None:
        """Reconcile stored profiles with the marker in gui_config.json.

        Runs once from ``commit_boot`` — pure local file I/O, no network, no
        dialogs. Must not raise for any recoverable condition: the caller's
        log-and-swallow would otherwise skip the header population and leave the
        combo empty for the whole session.

        Ordering note (already satisfied — do not move this earlier): by
        ``commit_boot`` time ``app.py``'s ``load_config_with_provenance`` has
        already done any ``.bak`` recovery / primary repair, so the marker read
        below sees the repaired primary rather than the corrupt one.
        """
        self._boot_only = _boot_only_values(self._window.config)

        profiles, active_id = self._reconcile()
        GUIConfigManager.ACTIVE_PROFILE_ID = active_id
        # Stays hidden at fewer than two profiles, so an existing user who never
        # creates one sees no change at all.
        self._header().set_profiles(profiles, active_id)

    def _reconcile(self) -> tuple[tuple[Profile, ...], str | None]:
        """Return the stored profiles and the id the session should start on."""
        profiles = ProfileStore.list_profiles()
        if profiles:
            return profiles, self._resolve_active_id(profiles)

        # First launch under profiles: adopt the live config as "Default" so
        # every existing user lands in a silent one-profile world.
        try:
            ProfileStore.write_profile(_DEFAULT_PROFILE_ID, self._window.config, name=_DEFAULT_PROFILE_NAME)
        except (OSError, ValueError):
            # Leave the pointer unset rather than claiming a profile that has no
            # file: save_config then stamps no marker and the next boot retries.
            logger.warning("Could not create the default settings profile", exc_info=True)
            return (), None
        return ProfileStore.list_profiles(), _DEFAULT_PROFILE_ID

    @staticmethod
    def _resolve_active_id(profiles: tuple[Profile, ...]) -> str:
        """Pick the active id, validating the stored marker against ``profiles``.

        The marker is checked for MEMBERSHIP in the known-id list and is never
        handed to ``ProfileStore`` unchecked: ``read_active_profile_id`` returns
        any non-empty string found in gui_config.json, so a hand-edited or
        restored file can carry something like ``"../gui_config"``.
        (``ProfileStore._validate_id`` is a second layer, not the first one.)

        An absent marker is NORMAL, not corruption: a fresh install, a
        ``create_default_config()`` fallback, or a ``.bak`` recovery of a file
        written before the marker existed all produce it, and
        ``_repair_primary_from_backup`` copies the file without going through
        ``save_config``, so the marker can also legitimately be one save stale.
        """
        known = {profile.id for profile in profiles}
        marker = GUIConfigManager.read_active_profile_id()
        if marker is not None and marker in known:
            return marker

        fallback = _DEFAULT_PROFILE_ID if _DEFAULT_PROFILE_ID in known else profiles[0].id
        if marker is None:
            logger.info("No active settings profile recorded; using '%s'", fallback)
        else:
            logger.info("Active settings profile '%s' has no stored file; using '%s'", marker, fallback)
        return fallback

    # ------------------------------------------------------------------
    # Switch / create
    # ------------------------------------------------------------------

    def switch_to(self, profile_id: str) -> SwitchResult:
        """Make ``profile_id`` the live config, or refuse without side effects."""
        if profile_id == GUIConfigManager.ACTIVE_PROFILE_ID:
            self._sync_header()
            return SwitchResult(switched=False)

        try:
            with self._window._dictionary_mutation_guard(_MUTATION_KIND) as ready:
                result = self._switch_locked(profile_id) if ready else SwitchResult(switched=False, reason=self._busy())
        finally:
            # EVERY terminal path re-syncs the header, exceptions included:
            # currentIndexChanged has already moved the combo to B by the time a
            # refusal is decided, and a combo showing B while A is live is the
            # worst state for a control that swaps every setting.
            self._sync_header()
        self._warn(result)
        return result

    def create_from_current(self, name: str) -> SwitchResult:
        """Snapshot the live config into a new profile and switch onto it."""
        try:
            with self._window._dictionary_mutation_guard(_MUTATION_KIND) as ready:
                result = self._create_locked(name) if ready else SwitchResult(switched=False, reason=self._busy())
        finally:
            self._sync_header()
        self._warn(result)
        return result

    def _create_locked(self, name: str) -> SwitchResult:
        """Create then switch, inside the already-held mutation guard."""
        # Checked BEFORE the create (and again inside _switch_locked, where it
        # guards a plain switch) so a refusal leaves no profile the user did not
        # ask to be inactive — which would also pop the previously hidden combo
        # into view with two entries. The call is idempotent.
        if not self._window.release_dictionary_resources():
            return SwitchResult(switched=False, reason=self._busy_mining())

        try:
            profile = ProfileStore.create(name, self._window.config)
        except (OSError, ValueError) as exc:
            logger.warning("Could not create settings profile %r: %s", name, exc)
            return SwitchResult(
                switched=False,
                reason=tr_format(
                    QCoreApplication.translate("ProfileController", "Could not create the profile '%1': %2"),
                    name,
                    exc,
                ),
            )
        return self._switch_locked(profile.id)

    def _switch_locked(self, profile_id: str) -> SwitchResult:
        """The switch body, run inside a held ``_dictionary_mutation_guard``."""
        window = self._window
        names = {profile.id: profile.name for profile in ProfileStore.list_profiles()}
        incoming_name = names.get(profile_id, profile_id)

        # Covers mining, card backfill and prewarm — none of which the settings
        # preflight knows about — and drops the SQLite handles a chain swap
        # wants dropped anyway.
        if not window.release_dictionary_resources():
            return SwitchResult(switched=False, reason=self._busy_mining())

        outgoing_id = GUIConfigManager.ACTIVE_PROFILE_ID
        outgoing_config = window.config

        # 1. Durable snapshot of what we are leaving, before anything moves.
        if outgoing_id is not None:
            if outgoing_id not in names:
                # The file vanished under us (deleted outside the app). Writing
                # it back resurrects the profile; NOT writing it would drop
                # every edit made since the last snapshot, because
                # gui_config.json is about to become the incoming config.
                logger.warning("Active settings profile '%s' has no stored file; recreating it", outgoing_id)
            try:
                ProfileStore.write_profile(outgoing_id, outgoing_config, name=names.get(outgoing_id, outgoing_id))
            except (OSError, ValueError) as exc:
                logger.warning("Could not snapshot settings profile '%s': %s", outgoing_id, exc)
                return SwitchResult(
                    switched=False,
                    reason=tr_format(
                        QCoreApplication.translate(
                            "ProfileController",
                            "Could not save the current profile '%1': %2. Nothing was switched.",
                        ),
                        names.get(outgoing_id, outgoing_id),
                        exc,
                    ),
                )

        # 2. Read the incoming file. read_profile propagates by design (it must
        # never fall back to defaults), and a corrupt/oversized file raises
        # _ConfigReadError — a ValueError subclass, NOT an OSError, because
        # read_json_bounded swallows read OSErrors into its sentinel. Nothing is
        # left inconsistent by refusing here: the snapshot above is a correct
        # copy of the config that is still live.
        try:
            incoming = ProfileStore.read_profile(profile_id)
        except (OSError, ValueError, TypeError) as exc:
            logger.warning("Could not read settings profile '%s': %s", profile_id, exc)
            return SwitchResult(
                switched=False,
                reason=tr_format(
                    QCoreApplication.translate(
                        "ProfileController",
                        "Could not read the profile file %1: %2. Nothing was switched.",
                    ),
                    f"{profile_id}.json",
                    exc,
                ),
            )

        # 3. Re-seed Theme BEFORE the commit, because update_config's
        # config_refreshed fan-out reaches UISettingsPanel.load_from_config,
        # which renders the Active theme and captures the Revert baseline FROM
        # THE SINGLETON. Seeding afterwards renders the outgoing profile's theme
        # as active and pins Revert to the wrong target; worse, the next
        # star/unstar would build the new config from stale singleton state and
        # write profile A's favorites into profile B.
        outgoing_theme = _ThemeState.capture()
        try:
            _ThemeState.of_config(incoming).seed()
        except Exception as exc:  # noqa: BLE001 - theme discovery must not strand a switch
            logger.exception("Could not apply the theme of settings profile '%s'", profile_id)
            self._restore_theme(outgoing_theme)
            return SwitchResult(switched=False, reason=self._could_not_apply(incoming_name, exc))

        # 4. Move the pointer, THEN commit. update_config raises before touching
        # anything when save_config fails, so a pointer advanced past a failed
        # commit would make every later save this session (settings debounce,
        # closeEvent, the deferred close) stamp the incoming id onto the
        # OUTGOING settings — and the next switch-away would then overwrite the
        # incoming profile with them.
        GUIConfigManager.ACTIVE_PROFILE_ID = profile_id
        commit_error: Exception | None = None
        persisted = True
        try:
            # The version re-stamp keeps a profile snapshotted before an app
            # upgrade from re-arming commit_boot's "Anki Miner updated" dialog.
            # It carves no field out of the stored file, which keeps its own.
            window.update_config(replace(incoming, last_known_version=__version__))
        except ConfigCommitError as error:
            commit_error = error
            persisted = error.result.persisted
        except Exception as error:  # noqa: BLE001 - an unexpected raise must not strand the pointer
            commit_error = error
            # No result to consult, so use the durable evidence update_config
            # leaves: it assigns self.config only after save_config returned.
            persisted = window.config is not outgoing_config

        if commit_error is not None and not persisted:
            # Nothing reached disk: undo the in-memory pointer and the theme
            # re-seed so a refused switch leaves no residue at all. Deliberately
            # no apply_to_app here — the running app was never repainted.
            GUIConfigManager.ACTIVE_PROFILE_ID = outgoing_id
            self._restore_theme(outgoing_theme)
            return SwitchResult(switched=False, reason=self._could_not_apply(incoming_name, commit_error))

        # The switch is durable from here on, even if the refresh half failed;
        # the pointer stays where it is.
        self._apply_theme()
        self._header().refresh_favorites()
        self._note_restart_fields(incoming)

        if commit_error is not None:
            logger.warning("Settings profile '%s' is live but the refresh failed: %s", profile_id, commit_error)
            return SwitchResult(
                switched=True,
                reason=tr_format(
                    QCoreApplication.translate(
                        "ProfileController",
                        "Switched to '%1', but the running window could not be fully refreshed: %2. "
                        "Restart Anki Miner if something looks wrong.",
                    ),
                    incoming_name,
                    commit_error,
                ),
            )
        return SwitchResult(switched=True)

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _header(self) -> _ProfileHeader:
        """The window's header, narrowed to the two methods used here.

        A cast rather than an isinstance narrow: the window types this as the
        concrete ``HeaderWidget``, while everything this controller needs — and
        everything a test fake must provide — is :class:`_ProfileHeader`.
        """
        return cast("_ProfileHeader", self._window.header)

    def _sync_header(self) -> None:
        """Point the header combo at whatever the session actually ended on."""
        self._header().set_profiles(ProfileStore.list_profiles(), GUIConfigManager.ACTIVE_PROFILE_ID)

    def _warn(self, result: SwitchResult) -> None:
        """Surface a refusal (or a degraded refresh) as the house dialog.

        A modal, not the status bar: a switch is usually started from the modal
        profile manager, where a status-bar line is invisible. The status bar is
        used only for the informational restart note.
        """
        if result.reason is None:
            return
        QMessageBox.warning(
            self._window,
            QCoreApplication.translate("ProfileController", "Settings Profiles"),
            result.reason,
        )

    @staticmethod
    def _apply_theme() -> None:
        """Repaint the app once for the freshly seeded theme state.

        Exactly one call per switch: each is a measured ~870 ms whole-app
        stylesheet repolish on the GUI thread.
        """
        app = QApplication.instance()
        if isinstance(app, QApplication):
            Theme.apply_to_app(app)

    @staticmethod
    def _restore_theme(state: _ThemeState) -> None:
        """Put the singleton back after a refused switch (best effort)."""
        try:
            state.seed()
        except Exception:  # noqa: BLE001 - a failed restore must not mask the refusal
            logger.exception("Could not restore the previous theme state")

    def _note_restart_fields(self, incoming: AnkiMinerConfig) -> None:
        """Status-bar note for fields this profile cannot apply without a restart.

        Compared against the BOOT snapshot rather than the outgoing config, so
        an A->B->A round trip correctly stops warning.
        """
        baseline = self._boot_only
        if baseline is None:
            return
        changed = sorted(
            _boot_only_label(name) for name, value in _boot_only_values(incoming).items() if value != baseline[name]
        )
        if not changed:
            return
        self._window.status_bar.set_operation(
            tr_format(
                QCoreApplication.translate("ProfileController", "Restart Anki Miner to apply: %1"),
                ", ".join(changed),
            ),
            "info",
        )

    @staticmethod
    def _busy() -> str:
        """Refusal text for a guard that refused (settings preflight / JMdict).

        The JMdict leg shows its own dialog before refusing, so that case gets
        two; the preflight leg shows none, and silently doing nothing to a
        control that swaps every setting is the worse failure.
        """
        return QCoreApplication.translate(
            "ProfileController",
            "Settings are still being saved, or a dictionary change is in progress. Try again in a moment.",
        )

    @staticmethod
    def _busy_mining() -> str:
        return QCoreApplication.translate(
            "ProfileController",
            "Mining or card backfill is still using the dictionaries. Stop it and try again.",
        )

    @staticmethod
    def _could_not_apply(profile_name: str, error: object) -> str:
        return tr_format(
            QCoreApplication.translate(
                "ProfileController",
                "Could not apply the profile '%1': %2. Your current settings are unchanged.",
            ),
            profile_name,
            error,
        )
