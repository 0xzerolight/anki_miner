"""Sequencing for named settings profiles: boot reconcile, switch, create.

``gui_config.json`` stays the live config; profiles are sidecar snapshots owned
by :mod:`anki_miner.gui.utils.profile_store`. What lives here is the ORDER in
which a switch moves state, which is where every data-loss path of this feature
sits:

* the outgoing profile is snapshotted to disk BEFORE anything else moves;
* a live config that cannot be attributed to a stored profile is saved as a NEW
  profile, never adopted into an existing id — adopting one makes the very first
  switch snapshot the live config over a profile that was never live, and
  profile files have no ``.bak``;
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
from PyQt6.QtWidgets import QApplication

from anki_miner import __version__
from anki_miner.config import AnkiMinerConfig
from anki_miner.gui.controllers import language_switch
from anki_miner.gui.resources.styles.theme import Theme
from anki_miner.gui.utils.config_commit import ConfigCommitError
from anki_miner.gui.utils.config_manager import GUIConfigManager
from anki_miner.gui.utils.profile_store import Profile, ProfileStore
from anki_miner.gui.widgets.base import ScreenIssue, report_screen_issue
from anki_miner.languages.registry import config_language, get_profile
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
#   stats_db_path -> StatsService(...), constructed once and never rebuilt
#   log_path      -> the logging handler installed at startup
#
# themes_root is deliberately NOT here: the Theme re-seed below hands the
# incoming value to Theme.initialize, which re-runs discovery against it, so the
# folder root IS live. (The theme TREE still cannot rescan mid-session, which is
# the Settings panel's own note, not a restart note about this field.)
#
# Sibling field partitions, same idiom: SettingsTab._EXTERNAL_ONLY_FIELDS
# (settings_tab.py:120), SettingsTab._RESET_PRESERVE_UI (:141) and
# GUIConfigManager.machine_specific_fields() (config_manager.py:450).
_BOOT_ONLY_FIELDS = frozenset({"ui_language", "ui_zoom", "ui_font_scale", "stats_db_path", "log_path"})


def _boot_only_values(config: AnkiMinerConfig) -> dict[str, object]:
    """Snapshot the boot-only field values of ``config``."""
    return {name: getattr(config, name) for name in _BOOT_ONLY_FIELDS}


def _language_change(outgoing: AnkiMinerConfig, incoming: AnkiMinerConfig) -> bool:
    """Whether this profile switch is also a mining-language switch (spec 6.1)."""
    return incoming.language != outgoing.language


def _incoming_language_name(incoming: AnkiMinerConfig) -> str:
    """The incoming language's own name (中文), never the bare code.

    Degraded through ``config_language`` first: ``AnkiMinerConfig`` accepts every
    whitelisted code, including one whose profile is not registered yet, and an
    unregistered code would otherwise raise ``ValueError`` out of the switch.
    """
    return get_profile(config_language(incoming)).display_name


def _boot_only_label(field: str) -> str:
    """User-facing label for a boot-only field name (falls back to the name)."""
    labels = {
        "ui_language": QCoreApplication.translate("ProfileController", "Language"),
        "ui_zoom": QCoreApplication.translate("ProfileController", "Interface scale"),
        "ui_font_scale": QCoreApplication.translate("ProfileController", "Text size"),
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
    shown as a screen issue: it is set on every refusal, and also on a
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
        """Theme state for an incoming profile.

        ``font_scale`` deliberately keeps the *running* process's boot scale
        rather than the incoming config's: text size is restart-to-apply
        (D39b-A), so a profile switch must not silently re-style the window.
        The incoming value is still persisted, still reaches the Text size
        combo through ``load_from_config``, and is named in the boot-only
        restart note.
        """
        return cls(
            active=config.theme,
            favorites=config.theme_favorites,
            user_dir=config.themes_root,
            font_scale=Theme.get_font_scale(),
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
            ``release_dictionary_resources``, ``update_config``,
            ``reload_settings_panels``, the header combo and the status bar.
            Held as a reference (the ``BackgroundTaskController`` idiom) rather
            than as a bag of injected callables — this class needs seven of them
            and they must all address the same window.
    """

    def __init__(self, window: MainWindow) -> None:
        self._window = window
        # Boot values of the restart-only fields, taken by bootstrap(). Compared
        # against on every switch so an A->B->A round trip stops warning; None
        # means bootstrap never ran, so there is no baseline to compare with.
        self._boot_only: dict[str, object] | None = None
        # Display name of the active profile, as last seen on disk. Only ever
        # read when the active profile's FILE has vanished, so the outgoing
        # snapshot that recreates it comes back named "A" rather than "a"; a
        # rename since then is picked up from the listing, which wins.
        self._active_name: str | None = None

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
        self._active_name = next((profile.name for profile in profiles if profile.id == active_id), None)
        # Visible from the first profile on (HeaderWidget.set_profiles owns the
        # rule); a reconcile that could not enumerate the directory passes () and
        # the block stays out of the header.
        self._header().set_profiles(profiles, active_id)

    def _reconcile(self) -> tuple[tuple[Profile, ...], str | None]:
        """Return the stored profiles and the id the session should start on.

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
        # scan_profiles, not list_profiles: the lenient wrapper reports a
        # directory it could not scan as EMPTY, which would fall into the branch
        # below and adopt the live config as `default.json` — overwriting a real
        # profile that a transient permission/IO error merely hid, with no .bak
        # to recover from. `None` means "cannot enumerate", so nothing is written
        # and nothing is adopted.
        profiles = ProfileStore.scan_profiles()
        if profiles is None:
            # Leaving the pointer unset is already handled everywhere: the first
            # switch writes no outgoing snapshot rather than aiming it at a
            # guess, and save_config stamps no marker. The cost is that a later
            # save this session drops the (still valid) marker from
            # gui_config.json, so the next boot with a readable directory
            # preserves the live config as a new profile instead of resolving
            # it — clutter, not loss. The marker cannot be trusted here either:
            # with no known-id list there is nothing to validate it against, and
            # it must never reach ProfileStore unchecked.
            logger.warning("Could not enumerate the stored settings profiles; starting with no active profile")
            return (), None
        if not profiles:
            # First launch under profiles: adopt the live config as "Default" so
            # every existing user lands in a silent one-profile world.
            try:
                ProfileStore.write_profile(_DEFAULT_PROFILE_ID, self._window.config, name=_DEFAULT_PROFILE_NAME)
            except (OSError, ValueError, TypeError):
                # Leave the pointer unset rather than claiming a profile that
                # has no file: save_config then stamps no marker, the next boot
                # retries, and the first switch writes no outgoing snapshot.
                logger.warning("Could not create the default settings profile", exc_info=True)
                return (), None
            return ProfileStore.list_profiles(), _DEFAULT_PROFILE_ID

        marker = GUIConfigManager.read_active_profile_id()
        if marker is not None and any(profile.id == marker for profile in profiles):
            return profiles, marker

        recovered = self._recover_unidentified_config(marker, profiles)
        if recovered is None:
            return profiles, None
        return ProfileStore.list_profiles(), recovered.id

    def _recover_unidentified_config(self, marker: str | None, profiles: tuple[Profile, ...]) -> Profile | None:
        """Save the live config as a NEW profile when it belongs to no stored one.

        Deliberately not the obvious "fall back to ``default``, else the first
        profile": that borrowed id becomes the outgoing id of the first switch,
        whose snapshot then writes the live config over a stored profile that
        was never live. Profile files have no ``.bak``, so the loss is
        permanent, and the vanished-file warning in ``_switch_locked`` does not
        even fire — the borrowed id IS a known id. Reachable whenever a profile
        file is deleted outside the app, gui_config.json is rebuilt from a
        pre-marker ``.bak``, or ``load_config_with_provenance`` fell through to
        ``create_default_config()``.

        Creating a new profile instead loses nothing and touches nothing that
        already exists.

        Returns:
            The created profile, or ``None`` when even the create failed — the
            caller then leaves the pointer unset, which is already correct: an
            unknown live identity makes the first switch skip the outgoing
            snapshot rather than aim it at a guess.
        """
        try:
            profile = ProfileStore.create(self._recovered_profile_name(profiles), self._window.config)
        except (OSError, ValueError, TypeError) as exc:
            logger.warning(
                "Active settings profile %r resolves to no stored file and the current settings could not be "
                "saved as a new profile (%s); starting with no active profile",
                marker,
                exc,
            )
            return None
        logger.warning(
            "Active settings profile %r resolves to no stored file; saved the current settings as '%s' (%s) "
            "instead of adopting an existing profile, which the first switch would have overwritten",
            marker,
            profile.name,
            profile.id,
        )
        return profile

    @staticmethod
    def _recovered_profile_name(profiles: tuple[Profile, ...]) -> str:
        """A recovery-profile display name no stored profile already holds.

        ``ProfileStore.create`` rejects a case-insensitive duplicate name with
        ``ValueError``, and a boot that cannot identify the live config must not
        give up just because a previous recovery already used the plain name.
        """
        base = QCoreApplication.translate("ProfileController", "Recovered settings")
        taken = {profile.name.casefold() for profile in profiles}
        if base.casefold() not in taken:
            return base
        # Terminates: the candidates are distinct and ``taken`` is finite
        # (ProfileStore caps the directory at MAX_PROFILES entries).
        suffix = 2
        while True:
            candidate = tr_format(
                QCoreApplication.translate("ProfileController", "Recovered settings %1"),
                suffix,
            )
            if candidate.casefold() not in taken:
                return candidate
            suffix += 1

    # ------------------------------------------------------------------
    # Switch / create
    # ------------------------------------------------------------------

    def switch_to(self, profile_id: str) -> SwitchResult:
        """Make ``profile_id`` the live config, or refuse without side effects."""
        if profile_id == GUIConfigManager.ACTIVE_PROFILE_ID:
            self.sync_header()
            return SwitchResult(switched=False)

        try:
            with self._window._dictionary_mutation_guard(_MUTATION_KIND) as ready:
                result = self._switch_locked(profile_id) if ready else SwitchResult(switched=False, reason=self._busy())
        finally:
            # EVERY terminal path re-syncs the header, exceptions included:
            # currentIndexChanged has already moved the combo to B by the time a
            # refusal is decided, and a combo showing B while A is live is the
            # worst state for a control that swaps every setting.
            self.sync_header()
        self._warn(result)
        return result

    def create_from_current(self, name: str) -> SwitchResult:
        """Snapshot the live config into a new profile and switch onto it."""
        try:
            with self._window._dictionary_mutation_guard(_MUTATION_KIND) as ready:
                result = self._create_locked(name) if ready else SwitchResult(switched=False, reason=self._busy())
        finally:
            self.sync_header()
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
        except (OSError, ValueError, TypeError) as exc:
            logger.warning("Could not create settings profile %r: %s", name, exc)
            return SwitchResult(
                switched=False,
                reason=tr_format(
                    QCoreApplication.translate("ProfileController", "Could not create the profile '%1': %2"),
                    name,
                    exc,
                ),
            )
        result = self._switch_locked(profile.id)
        if result.switched:
            return result
        try:
            ProfileStore.delete(profile.id)
        except (OSError, ValueError) as exc:
            logger.warning("Could not remove settings profile '%s' after its switch failed: %s", profile.id, exc)
            cleanup_reason = tr_format(
                QCoreApplication.translate(
                    "ProfileController",
                    "The new profile '%1' (%2) remains because cleanup failed: %3. Delete it manually.",
                ),
                profile.name,
                f"{profile.id}.json",
                exc,
            )
            reason = f"{result.reason} {cleanup_reason}" if result.reason else cleanup_reason
            return SwitchResult(switched=False, reason=reason)
        return result

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
            # The listing wins (it picks up a rename made this session); the
            # remembered name is the fallback for a file that vanished under us,
            # so the id is only ever used as a display name when nothing else
            # knows what this profile was called.
            outgoing_name = names.get(outgoing_id) or self._active_name or outgoing_id
            if outgoing_id not in names:
                # The file vanished under us (deleted outside the app). Writing
                # it back resurrects the profile; NOT writing it would drop
                # every edit made since the last snapshot, because
                # gui_config.json is about to become the incoming config.
                logger.warning("Active settings profile '%s' has no stored file; recreating it", outgoing_id)
            try:
                ProfileStore.write_profile(outgoing_id, outgoing_config, name=outgoing_name)
            except (OSError, ValueError, TypeError) as exc:
                logger.warning("Could not snapshot settings profile '%s': %s", outgoing_id, exc)
                return SwitchResult(
                    switched=False,
                    reason=tr_format(
                        QCoreApplication.translate(
                            "ProfileController",
                            "Could not save the current profile '%1': %2. Nothing was switched.",
                        ),
                        outgoing_name,
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

        # 2b. Trigger 2 of the mining-language switch (spec 6.1). The snapshot
        # already carries that language's scoped values, so there is no stash
        # swap to run here — what is still owed is the queue rule, refused
        # BEFORE the commit, where a refusal is still free. After it, a refusal
        # would cost the user the profile switch as well.
        language_change = _language_change(outgoing_config, incoming)
        pending = language_switch.queued_screens(window) if language_change else ()
        if pending and not language_switch.confirm_queue_flush(window, pending, _incoming_language_name(incoming)):
            return SwitchResult(switched=False, reason=self._queued_work())

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
        persisted = False
        try:
            # The version re-stamp keeps a profile snapshotted before an app
            # upgrade from re-arming commit_boot's "Anki Miner updated" dialog.
            # It carves no field out of the stored file, which keeps its own.
            window.update_config(replace(incoming, last_known_version=__version__))
            persisted = True
        except ConfigCommitError as error:
            commit_error = error
            persisted = error.result.persisted
        except Exception as error:  # noqa: BLE001 - an unexpected raise must not strand the pointer
            commit_error = error
            # No result to consult, so use the durable evidence update_config
            # leaves: it assigns self.config only after save_config returned.
            persisted = window.config is not outgoing_config
        finally:
            # In a FINALLY, not in the except clauses: a BaseException
            # (KeyboardInterrupt, SystemExit) passes straight through
            # update_config and both handlers above, leaving `persisted` False
            # whichever side of the save it escaped from. So the flag alone
            # cannot decide, and the rollback is gated on the durable evidence
            # too: update_config assigns self.config only AFTER save_config
            # returned, and always to a fresh replace(...) object, so
            # `window.config is outgoing_config` is that assignment's own
            # witness and cannot false-negative.
            #
            # Pre-save escape — nothing reached disk: undo the in-memory pointer
            # and the theme re-seed so a refused switch leaves no residue. A
            # pointer left ahead of a config that never moved would make every
            # later save this session stamp the incoming id onto the OUTGOING
            # settings.
            #
            # Post-save escape — gui_config.json already holds the incoming
            # settings AND the incoming marker (update_config runs the
            # file-dialog re-seed, the service rebuild and the config_refreshed
            # fan-out after the assignment, each guarded only by `except
            # Exception`). Reverting there is the same permanent loss through
            # the other door: later saves would re-stamp the OUTGOING id onto
            # the INCOMING settings, the next boot would attribute them to the
            # outgoing profile, and the first switch-away would write them over
            # its file — profile files have no .bak. Restoring the theme is
            # wrong for the same reason: the singleton would hold A's favorites
            # while B is live, so the next star/unstar writes A's into B.
            if not persisted and window.config is outgoing_config:
                GUIConfigManager.ACTIVE_PROFILE_ID = outgoing_id
                self._restore_theme(outgoing_theme)

        if not persisted:
            # Rolled back in the finally above; deliberately no apply_to_app,
            # because the running app was never repainted.
            return SwitchResult(switched=False, reason=self._could_not_apply(incoming_name, commit_error))

        # The switch is durable from here on, even if the refresh half failed;
        # the pointer stays where it is.
        self._active_name = incoming_name
        # Before apply_to_app, so the freshly rebuilt panels are covered by that
        # single repolish rather than needing a second one.
        refresh_error = commit_error or self._repaint_settings()
        self._apply_theme()
        self._header().refresh_favorites()
        self._note_restart_fields(incoming)

        if language_change:
            # A profile snapshot is already-configured settings, so never a
            # first visit: no setup prompt on this path.
            language_switch.commit_language_change(window, outgoing_config, flush=bool(pending), first_visit=False)

        if refresh_error is not None:
            logger.warning("Settings profile '%s' is live but the refresh failed: %s", profile_id, refresh_error)
            return SwitchResult(
                switched=True,
                reason=tr_format(
                    QCoreApplication.translate(
                        "ProfileController",
                        "Switched to '%1', but the running window could not be fully refreshed: %2. "
                        "Restart Anki Miner if something looks wrong.",
                    ),
                    incoming_name,
                    refresh_error,
                ),
            )
        return SwitchResult(switched=True)

    def _repaint_settings(self) -> Exception | None:
        """Force the Settings panels to redraw from the now-live config.

        NOT left to ``update_config``'s ``config_refreshed`` fan-out.
        ``SettingsTab.update_config`` skips its reload whenever the whole diff
        falls inside ``_EXTERNAL_ONLY_FIELDS`` — a gate that protects unsaved
        panel edits during unrelated commits (OVH-007) and must keep doing so.
        Two profiles differing only in theme / favorites / font scale / language
        produce exactly that diff (the ``last_known_version`` re-stamp above and
        ``update_config``'s ``config_version`` bump are both inside the allowlist
        too, so neither can force the reload), and the tab would go on rendering
        the profile the user just left — including a theme tree drawing the
        OUTGOING favorites over an already re-seeded ``Theme`` singleton, so the
        next star click toggles the opposite of what is drawn.

        Returns:
            The exception a failed repaint raised, or ``None``. Returned rather
            than propagated so it is reported exactly like ``update_config``'s
            own post-save refresh failures: the switch is already durable and a
            redraw must not undo it.
        """
        try:
            self._window.reload_settings_panels()
        except Exception as error:  # noqa: BLE001 - a failed redraw must not strand a durable switch
            logger.exception("Could not repaint the Settings panels for the incoming profile")
            return error
        return None

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

    def sync_header(self) -> None:
        """Point the header combo at whatever the session actually ended on.

        Public because it is also the profile manager's refresh hook: its
        rename/delete paths go straight to ``ProfileStore`` and never pass
        through a switch, so they need the same re-point this class runs from
        every terminal path.
        """
        self._header().set_profiles(ProfileStore.list_profiles(), GUIConfigManager.ACTIVE_PROFILE_ID)

    def _warn(self, result: SwitchResult) -> None:
        """Surface a refusal (or a degraded refresh) on the main window (D24).

        Not the status bar, whose line expires: a refused profile switch has to
        stay readable. Reported on the window rather than in the modal profile
        manager the switch usually starts from — the banner persists, so it is
        still there when that dialog closes, and it cannot stop a run.
        """
        if result.reason is None:
            return
        report_screen_issue(self._window, ScreenIssue(summary=result.reason))

    @staticmethod
    def _apply_theme() -> None:
        """Repaint the app once for the freshly seeded theme state.

        Exactly one call per switch: each is a whole-app stylesheet repolish on
        the GUI thread, re-measured on the real composed window at 1647 ms
        (1999 widgets, Qt 6.11). Re-installing an application stylesheet costs
        ~800 ms even when the sheet is a single rule, so the count of calls is
        the only lever here until D39-C removes the re-install entirely.
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
    def _queued_work() -> str:
        return QCoreApplication.translate(
            "ProfileController",
            "That profile mines another language and the queues still hold work. Nothing was switched.",
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
