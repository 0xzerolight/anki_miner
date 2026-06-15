# Releasing

Maintainer-facing release SOP. Contributors should not need to run any of these steps.

## Prerequisites

- Push access to `0xzerolight/anki_miner`.
- PyPI trusted publisher already configured for the project; releases publish via `publish.yml` on tag push.
- No outstanding regressions in `## [Unreleased]` of `CHANGELOG.md`.

## Steps

1. **Sync the version string.** Confirm `anki_miner/__init__.py:__version__` matches the tag you intend to push. The release workflow validates this and refuses to publish on mismatch (Issue #10).

2. **Roll the changelog.** In `CHANGELOG.md`, rename `## [Unreleased]` to `## [X.Y.Z] - YYYY-MM-DD`. Add a new empty `## [Unreleased]` at the top with empty `### Added` / `### Changed` / `### Fixed` / `### Removed` placeholders so the next contribution has somewhere to land.

3. **Commit and push to `main`.**

   ```bash
   git commit -m "chore(release): vX.Y.Z"
   git push origin main
   ```

4. **Tag and push.**

   ```bash
   git tag vX.Y.Z
   git push origin vX.Y.Z
   ```

5. **Release workflow runs.** On the tag, `.github/workflows/release.yml` builds per-OS artifacts:

   - Linux: PyInstaller bundle, AppImage, `nfpm`-built `.deb`.
   - Windows: PyInstaller bundle, Inno Setup installer.
   - macOS (arm64): PyInstaller bundle.

   Each Linux/Windows/macOS build runs a bundled YouTube smoke test (`ANKI_MINER_SMOKE=youtube`), expecting the string `BUNDLED_SMOKE_PASS` in output. Artifacts upload to the GitHub Release for the tag.

6. **PyPI publish runs.** `.github/workflows/publish.yml` builds and publishes the sdist + wheel to PyPI via trusted publishing on the same tag.

7. **Verify the Release page.** Check that all expected assets attached:

   - `AnkiMiner-*-Linux-x86_64.AppImage`
   - `anki-miner_*_amd64.deb`
   - `AnkiMiner-*-Setup.exe`
   - `AnkiMiner-macOS-arm64.tar.gz`

   And that PyPI lists the new version: <https://pypi.org/project/anki-miner/>.

8. **Smoke-test one installer.** Run the installer for at least one OS (typically the AppImage on Linux for speed). Confirm the GUI launches and a sample mine completes end-to-end.

9. **Announce.** Wherever you announce releases (Discussions, social, etc.). The in-app update banner picks the new release up automatically on every user's next launch.

## Recovering from a bad release

- **Workflow failed mid-publish**: the tag is on the remote but artifacts are incomplete. Delete the GitHub Release (not the tag), fix forward, push a new patch tag (`vX.Y.Z+1`). Do not retag the same version — PyPI rejects re-uploads.
- **PyPI accepted a broken wheel**: yank the version on PyPI (`pip` skips yanked releases unless a user explicitly pins to it), then publish a patch release with the fix.
- **Update banner pointing at broken release**: the banner reads the GitHub Release API. Marking the GitHub Release as a draft hides it from the banner until you re-publish a fixed version.
