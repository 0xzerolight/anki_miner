# Releasing

Maintainer-facing release SOP. Contributors should not need to run any of these steps.

## Prerequisites

- Push access to `0xzerolight/anki_miner`.
- PyPI trusted publisher already configured for the project; releases publish via `publish.yml` on tag push.
- No outstanding regressions in `## [Unreleased]` of `CHANGELOG.md`.

## Preflight (run BEFORE tagging)

The release workflow only runs on a `v*` tag push, so a build or smoke failure is
discovered *after* the tag is public (this is how v2.7.1 shipped broken — a bundled
`av` module the ASR smoke caught only in CI). Run the local preflight first:

```bash
scripts/release_preflight.sh                 # full Linux mirror: build + smokes + AppImage + .deb
scripts/release_preflight.sh --skip-package  # fast ~2min path: build + smokes only
```

It mirrors the Linux release job (isolated `.[asr]` venv + pinned PyInstaller,
SHA-verified ffmpeg/alass vendor fetch, PyInstaller build, then the four bundle
smokes via `scripts/bundle_smoke.sh` — the same script CI runs) and must print
`PREFLIGHT ALL GREEN` before you tag. It cannot reproduce the Windows (Inno Setup,
from-source bootloader) or macOS arch-native ffmpeg steps; those stay CI-only. Three
of the four smokes are pure-Python import checks, so import/collection failures
surface on Linux identically to Windows/macOS; the fourth (whisper.cpp/pywhispercpp
Vulkan loadability) is a native `ctypes` cold-load that runs only on Linux and
Windows (skipped on macOS, which stays on the CT2/Metal path).

For a full-matrix rehearsal without cutting a release, run the dry-run gate:

```bash
scripts/release_dryrun.sh                 # default: linux-windows
scripts/release_dryrun.sh all             # all four legs (run once, green, right before tagging)
```

It dispatches the real `release.yml` build matrix via `workflow_dispatch` (`gh
workflow run --ref <branch>`). Dispatch is a dry-run by construction — the `ci-gate`
and `release` jobs are `push`-only and `publish.yml` has no dispatch trigger, so it
cuts **no tag, no GitHub Release, and no PyPI upload**. After a green build it proves
those negatives and that the Vulkan smoke actually executed, then prints `RELEASE
DRY-RUN GREEN`. Two preconditions: the dispatch-enabled `release.yml` must be on the
default branch (`main`), and the branch under test must be pushed to origin. Re-run
and fix until green before tagging.

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
   - macOS (Intel, `macos-15-intel`): PyInstaller bundle, without the `[asr]` extra (see the Intel-macOS note below).

   Each Linux/Windows/macOS build runs `scripts/bundle_smoke.sh` — four bundle smokes (youtube extractor registry, offline ASR native-lib resolution, ffmpeg encoder set, and a whisper.cpp/pywhispercpp Vulkan import-loadability gate — a Linux+Windows-only native loadability check, skipped on macOS), the same script the preflight runs. Artifacts upload to the GitHub Release for the tag.

6. **PyPI publish runs.** `.github/workflows/publish.yml` builds and publishes the sdist + wheel to PyPI via trusted publishing on the same tag.

7. **Verify the Release page.** Check that all expected assets attached:

   - `AnkiMiner-*-Linux-x86_64.AppImage`
   - `anki-miner_*_amd64.deb`
   - `AnkiMiner-*-Setup.exe`
   - `AnkiMiner-macOS-arm64.tar.gz`
   - `AnkiMiner-macOS-x86_64.tar.gz`
   - `AnkiMiner-*-pywhispercpp-vulkan.sha256` (Linux + Windows — bundled Vulkan wheel provenance)

   And that PyPI lists the new version: <https://pypi.org/project/anki-miner/>.

   > **Intel macOS build is special.** The `AnkiMiner-macOS-x86_64.tar.gz` build runs on
   > GitHub's `macos-15-intel` runner and ships **without local Whisper ASR**: it installs
   > the base package (no `[asr]` extra) because faster-whisper hard-requires onnxruntime,
   > which dropped all macOS x86_64 wheels at 1.20 (the last Intel-mac builds need numpy<2,
   > but the lock pins numpy 2.x), making `[asr]` unresolvable there. The app degrades
   > gracefully — ASR probes via `find_spec` and the Subtitle Generation tab reports it
   > unavailable. The asr bundle smoke is skipped on this job (`BUNDLE_SMOKE_SKIP_ASR=1`).
   >
   > **Runner lifespan.** The earlier `macos-13` runner was retired on 2025-12-04 — a
   > retired label gets no machine and the job queues forever (this hung the v2.7.1/v2.7.2
   > releases). `macos-15-intel` is GitHub's last x86_64 Actions image, served only through
   > ~August 2027. Before then, drop the Intel matrix entry (ship macOS arm64-only). Do not
   > use a `-large` runner variant.

8. **Smoke-test one installer.** Run the installer for at least one OS (typically the AppImage on Linux for speed). Confirm the GUI launches and a sample mine completes end-to-end.

9. **Announce.** Wherever you announce releases (Discussions, social, etc.). The in-app update banner picks the new release up automatically on every user's next launch.

## Antivirus false positives (Windows)

The Windows build is an unsigned PyInstaller bundle shipping `yt-dlp` and `ffmpeg`, so Microsoft Defender (and other engines) periodically flag it as malware. It is always a false positive. When a flag is reported after a release:

1. Download the flagged `AnkiMiner-*-Setup.exe` from the Release.
2. Submit it at <https://www.microsoft.com/en-us/wdsi/filesubmission> under "software developer" / incorrectly detected as malware. Microsoft threat researchers re-evaluate and de-list, usually within hours to a couple of days.
3. For other engines reported (ESET, Avast, etc.), submit to that vendor's false-positive portal.

The build is already hardened against the common heuristics — no UPX, embedded PE version metadata, and a source-built PyInstaller bootloader (see `anki_miner.spec` and `release.yml`). The durable fix is Authenticode code signing (planned via SignPath Foundation's free OSS program); signed builds accrue Defender/SmartScreen reputation so the flagging stops recurring per release.

## Recovering from a bad release

- **Workflow failed mid-publish**: the tag is on the remote but artifacts are incomplete. Delete the GitHub Release (not the tag), fix forward, push a new patch tag (`vX.Y.Z+1`). Do not retag the same version — PyPI rejects re-uploads.
- **PyPI accepted a broken wheel**: yank the version on PyPI (`pip` skips yanked releases unless a user explicitly pins to it), then publish a patch release with the fix.
- **Update banner pointing at broken release**: the banner reads the GitHub Release API. Marking the GitHub Release as a draft hides it from the banner until you re-publish a fixed version.
