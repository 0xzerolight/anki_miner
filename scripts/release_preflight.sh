#!/usr/bin/env bash
# Local release-CI preflight — run BEFORE pushing any v* tag.
#
# Mirrors the Linux build job of .github/workflows/release.yml as faithfully as
# a Linux box allows: isolated venv (pinned PyInstaller), SHA-verified
# vendor fetch (ffmpeg + alass + libmpv), PyInstaller build, the three bundle smokes
# (via scripts/bundle_smoke.sh — the same script CI runs), then AppImage + .deb.
#
# ONE deliberate divergence: this venv installs .[asr,zh,ko] where the release
# legs install .[asr]. The zh/ko engines arrive as downloadable language packs
# and the spec excludes them from the frozen graph — but an exclude can only be
# proven by freezing on a machine where the thing excluded IS installed, and
# this is the only build that has them. On a runner that never had jieba, an
# exclude that did nothing would look exactly like one that worked.
#
# CANNOT reproduce (CI-only, by platform): Windows Inno Setup, the Windows
# from-source bootloader, macOS arch-native ffmpeg. The three smokes are pure
# Python import checks, so import/collection failures (like the av miss that
# broke v2.7.1) surface here on Linux exactly as they did on Windows/macOS.
# The SEEDED asr smoke needs a Python 3.12 build venv (the pack pins cp312
# wheels): on any other python3 the seed step reports it unsupported and only
# the bare-absent asr leg runs here.
#
# Usage:
#   scripts/release_preflight.sh [--clean] [--skip-package] [--version X.Y.Z]
#     --clean         rebuild .venv-release and re-fetch vendor binaries
#     --skip-package  stop after the smokes (fast ~2min path; skips AppImage/.deb)
#     --version X.Y.Z assert anki_miner/__init__.py matches X.Y.Z (tag parity)
set -uo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_ROOT" || exit 2

CLEAN=0
SKIP_PACKAGE=0
WANT_VERSION=""
while [ $# -gt 0 ]; do
  case "$1" in
    --clean) CLEAN=1 ;;
    --skip-package) SKIP_PACKAGE=1 ;;
    --version) shift; WANT_VERSION="${1:?--version needs X.Y.Z}" ;;
    *) echo "Unknown arg: $1" >&2; exit 2 ;;
  esac
  shift
done

VENV="$REPO_ROOT/.venv-release"
CACHE="$REPO_ROOT/.release-cache"
PY="$VENV/bin/python"
PIP="$VENV/bin/pip"

# Pins mirrored from release.yml — bump together with the workflow.
PYINSTALLER_VERSION="6.20.0"
FFMPEG_URL="https://github.com/BtbN/FFmpeg-Builds/releases/download/autobuild-2026-05-31-13-22/ffmpeg-n8.1.1-9-g58d4114d36-linux64-gpl-shared-8.1.tar.xz"
FFMPEG_SHA256="5563b3454b754edbf066909c315729930018b8e1b00da4d97b52833dd094d109"
ALASS_URL="https://github.com/kaegi/alass/releases/download/v2.0.0/alass-linux64"
ALASS_SHA256="7bd0b9ae7e035d3ba940eacffb21243614df36231d47f21f0b4ce42001ab7fcd"
LIBMPV_URL="https://github.com/0xzerolight/anki_miner/releases/download/vendor-libmpv-20260712/libmpv-linux-x86_64.tar.gz"
LIBMPV_SHA256="5d9278463edab8f2a467f45c2c66416070d4e1543024df30fed2f721def663c1"
NFPM_VERSION="2.46.0"
NFPM_SHA256="43b4cb72cde2d6e61c02e5b330e3276882252bf67c057e089957f9dbd2c8de42"

FAILED=()
die() { echo "::error::$*" >&2; exit 1; }

# --- 0. host tools ------------------------------------------------------------
# Every other tool is fetched and SHA-verified below. appstreamcli is the one
# the packaging steps expect on PATH: render_metainfo.sh validates with it, and
# appimagetool rejects an unvalidated metainfo file.
if [ "$SKIP_PACKAGE" = "0" ] && ! command -v appstreamcli >/dev/null 2>&1; then
  die "appstreamcli not found — install it with: apt install appstream (or pass --skip-package)"
fi

echo "############################################################"
echo "# release preflight (Linux mirror of release.yml build job)"
echo "############################################################"

# --- 1. version check ---------------------------------------------------------
echo "=== version ==="
CODE_VERSION=$(python3 -c "import re,sys; print(re.search(r'__version__\s*=\s*[\"\x27]([^\"\x27]+)[\"\x27]', open('anki_miner/__init__.py').read()).group(1))")
echo "anki_miner/__init__.py __version__ = $CODE_VERSION"
if [ -n "$WANT_VERSION" ] && [ "$WANT_VERSION" != "$CODE_VERSION" ]; then
  die "Version mismatch: --version $WANT_VERSION != __init__.py $CODE_VERSION"
fi
VERSION="$CODE_VERSION"
echo

# --- 2. isolated build venv ---------------------------------------------------
echo "=== build venv (.venv-release) ==="
if [ "$CLEAN" = "1" ]; then rm -rf "$VENV"; fi
if [ ! -x "$PY" ]; then
  python3 -m venv "$VENV" || die "venv create failed"
  "$PIP" install --upgrade pip >/dev/null || die "pip upgrade failed"
fi
# Bundle deps constrained by the lock, plus the pinned PyInstaller. Idempotent —
# pip no-ops if satisfied. zh/ko are ON TOP of the release legs' .[asr] on
# purpose: see the divergence note in the header.
"$PIP" install ".[asr,zh,ko]" -c requirements.lock || die "pip install .[asr,zh,ko] failed"
"$PIP" install "pyinstaller==${PYINSTALLER_VERSION}" || die "pyinstaller install failed"
echo "pyinstaller: $("$VENV/bin/pyinstaller" --version)"
echo

# --- 3. vendor fetch (SHA-verified, cached) -----------------------------------
echo "=== vendor ffmpeg + alass + libmpv ==="
mkdir -p "$CACHE" vendor/ffmpeg vendor/alass vendor/libmpv \
  licenses/alass licenses/libmpv
if [ "$CLEAN" = "1" ]; then
  rm -f vendor/ffmpeg/ffmpeg vendor/ffmpeg/ffprobe vendor/ffmpeg/lib*.so.* vendor/alass/alass \
    vendor/libmpv/libmpv.so.2 licenses/libmpv/Copyright licenses/libmpv/SOURCES.txt
fi

verify_sha() { echo "$2  $1" | sha256sum -c - >/dev/null 2>&1; }

if [ ! -f vendor/ffmpeg/ffmpeg ] || [ ! -f vendor/ffmpeg/ffprobe ] || ! ls vendor/ffmpeg/lib*.so.* >/dev/null 2>&1; then
  # Only the fetch needs patchelf, and a populated vendor/ffmpeg skips it.
  command -v patchelf >/dev/null 2>&1 || die "patchelf not found — install it with: apt install patchelf"
  TARBALL="$CACHE/ffmpeg-linux64.tar.xz"
  if [ ! -f "$TARBALL" ] || ! verify_sha "$TARBALL" "$FFMPEG_SHA256"; then
    curl -fL "$FFMPEG_URL" -o "$TARBALL" || die "ffmpeg download failed"
  fi
  verify_sha "$TARBALL" "$FFMPEG_SHA256" || die "ffmpeg SHA256 mismatch"
  rm -rf "$CACHE/ff-extract"; mkdir -p "$CACHE/ff-extract"
  tar -xf "$TARBALL" -C "$CACHE/ff-extract"
  cp "$(find "$CACHE/ff-extract" -type f -path '*/bin/ffmpeg' | head -1)" vendor/ffmpeg/ffmpeg
  cp "$(find "$CACHE/ff-extract" -type f -path '*/bin/ffprobe' | head -1)" vendor/ffmpeg/ffprobe
  # One file per soname, dereferencing BtbN's symlink chain: the fully versioned
  # name is the same bytes and would ship twice. Mirrors release.yml.
  for _lib in "$CACHE"/ff-extract/*/lib/*.so.*; do
    case "${_lib##*/}" in *.so.*.*) continue ;; esac
    cp -L "$_lib" vendor/ffmpeg/ || die "ffmpeg shared library copy failed: $_lib"
  done
  # BtbN's rpath is a malformed literal ("-Wl:../lib"), so nothing resolves;
  # $ORIGIN finds the libraries that land beside the executables in
  # _internal/bin/. Mirrors release.yml.
  # shellcheck disable=SC2016  # $ORIGIN is resolved by the loader, not the shell
  patchelf --force-rpath --set-rpath '$ORIGIN' vendor/ffmpeg/ffmpeg vendor/ffmpeg/ffprobe \
    || die "patchelf failed to set the ffmpeg rpath"
  chmod +x vendor/ffmpeg/ffmpeg vendor/ffmpeg/ffprobe
fi
echo "vendor/ffmpeg: $(ls vendor/ffmpeg)"

if [ ! -f vendor/libmpv/libmpv.so.2 ] || [ ! -f licenses/libmpv/Copyright ] || [ ! -f licenses/libmpv/SOURCES.txt ]; then
  LIBMPV_TARBALL="$CACHE/libmpv-linux-x86_64.tar.gz"
  if [ ! -f "$LIBMPV_TARBALL" ] || ! verify_sha "$LIBMPV_TARBALL" "$LIBMPV_SHA256"; then
    curl -fL "$LIBMPV_URL" -o "$LIBMPV_TARBALL" || die "libmpv download failed"
  fi
  verify_sha "$LIBMPV_TARBALL" "$LIBMPV_SHA256" || die "libmpv SHA256 mismatch"
  rm -rf "$CACHE/libmpv-extract"; mkdir -p "$CACHE/libmpv-extract"
  tar -xzf "$LIBMPV_TARBALL" -C "$CACHE/libmpv-extract"
  cp "$CACHE/libmpv-extract/libmpv.so.2" vendor/libmpv/
  cp "$CACHE/libmpv-extract/Copyright" "$CACHE/libmpv-extract/SOURCES.txt" licenses/libmpv/
fi
echo "vendor/libmpv: $(ls vendor/libmpv)"

if [ ! -f vendor/alass/alass ]; then
  ALASS_DL="$CACHE/alass-linux64"
  if [ ! -f "$ALASS_DL" ] || ! verify_sha "$ALASS_DL" "$ALASS_SHA256"; then
    curl -fL "$ALASS_URL" -o "$ALASS_DL" || die "alass download failed"
  fi
  verify_sha "$ALASS_DL" "$ALASS_SHA256" || die "alass SHA256 mismatch"
  cp "$ALASS_DL" vendor/alass/alass
  chmod +x vendor/alass/alass
  [ -f licenses/alass/LICENSE ] || curl -fL "https://raw.githubusercontent.com/kaegi/alass/v2.0.0/LICENSE" -o licenses/alass/LICENSE || true
fi
echo "vendor/alass: $(ls vendor/alass)"
echo

# --- 4. PyInstaller build -----------------------------------------------------
echo "=== pyinstaller build ==="
rm -rf build dist/AnkiMiner
"$VENV/bin/pyinstaller" anki_miner.spec || die "PyInstaller build failed"
[ -d dist/AnkiMiner ] || die "dist/AnkiMiner not produced"
echo

# --- 4b. pack seeds for the smokes --------------------------------------------
# The ASR engine is an in-app pack, so the seeded asr leg has to be handed one,
# fetched by the app's own installer (same pins, same extraction). Warn-only:
# the seed is skipped when this venv's Python is not the bundle's 3.12, and the
# smoke then skips that leg loudly; the bare-absent leg needs no seed.
echo "=== pack seeds ==="
"$PY" scripts/fetch_language_pack_seeds.py "$CACHE/pack_seeds" asr \
  || echo "WARNING: pack seed step reported a problem (continuing; the seeded asr smoke will skip)"
echo

# --- 5. smokes (shared with CI) ----------------------------------------------
# The whispercpp-vulkan leg is skipped here and ONLY here. It asserts that a
# Vulkan-enabled pywhispercpp loads out of the bundle, and pywhispercpp lives in
# the [asr-vulkan] extra, not [asr] — the Linux release job installs [asr] and
# then replaces pywhispercpp with a wheel it builds from source against the
# Vulkan SDK (release.yml "Build pywhispercpp Vulkan wheel"). This script
# installs [asr] alone by design, so the leg can only ever report a missing
# backend. scripts/release_dryrun.sh is what proves it, and it fails closed if
# the leg reports SKIP on either the Linux or the Windows job.
echo "=== bundle smokes ==="
# The youtube leg needs an app-managed yt-dlp: the bundle ships none. Cached like
# the vendor downloads — the seed is a pinned release, so a present one is current.
# The cache test is the FILE pair, not the directory: a failed fetch leaves an
# empty bin/ behind (the updater stages into it before downloading), and a
# directory test would then never re-fetch.
YTDLP_SEED="$CACHE/ytdlp_seed"
if [ ! -f "$YTDLP_SEED/bin/yt-dlp" ] || [ ! -f "$YTDLP_SEED/bin/yt-dlp.verified" ]; then
  "$VENV/bin/python" scripts/fetch_ytdlp_seed.py "$YTDLP_SEED" \
    || echo "WARNING: yt-dlp seed fetch reported a problem (the youtube leg will skip)"
fi
if BUNDLE_SMOKE_SKIP_WHISPERCPP=1 BUNDLE_SMOKE_YTDLP_SEED="$YTDLP_SEED" BUNDLE_SMOKE_PACK_SEEDS="$CACHE/pack_seeds" bash scripts/bundle_smoke.sh dist/AnkiMiner; then
  echo "smokes: PASS"
else
  echo "smokes: FAIL"
  FAILED+=("smokes")
fi
echo

if [ "$SKIP_PACKAGE" = "1" ]; then
  echo "--skip-package: stopping after smokes."
else
  # --- 6a. AppImage -----------------------------------------------------------
  echo "=== AppImage ==="
  if bash packaging/appimage/build-appimage.sh "$VERSION"; then
    echo "AppImage: PASS"
  else
    echo "AppImage: FAIL"
    FAILED+=("appimage")
  fi
  echo

  # --- 6b. .deb (mirror release.yml: full AppImage tree, no strip) ------------
  echo "=== .deb ==="
  # packaging/nfpm.yaml packs dist/anki-miner.metainfo.xml. The AppImage step
  # renders the same file, but a skipped or failed AppImage must not surface
  # here as an nfpm "file not found" — render again; it is idempotent.
  DEB_OK=1
  if ! bash packaging/render_metainfo.sh "$VERSION" "dist/anki-miner.metainfo.xml"; then
    echo ".deb: FAIL (metainfo render/validate)"
    FAILED+=("deb")
    DEB_OK=0
  fi
  if [ "$DEB_OK" = "1" ]; then
    if [ ! -x "$CACHE/nfpm" ]; then
      NFPM_TGZ="$CACHE/nfpm.tar.gz"
      curl -fL "https://github.com/goreleaser/nfpm/releases/download/v${NFPM_VERSION}/nfpm_${NFPM_VERSION}_Linux_x86_64.tar.gz" -o "$NFPM_TGZ" || die "nfpm download failed"
      verify_sha "$NFPM_TGZ" "$NFPM_SHA256" || die "nfpm SHA256 mismatch"
      tar -xzf "$NFPM_TGZ" -C "$CACHE" nfpm
      chmod +x "$CACHE/nfpm"
    fi
    export VERSION
    if "$CACHE/nfpm" package --config packaging/nfpm.yaml --packager deb \
          --target "dist/anki-miner_${VERSION}_amd64.deb"; then
      echo ".deb: PASS -> dist/anki-miner_${VERSION}_amd64.deb"
    else
      echo ".deb: FAIL"
      FAILED+=("deb")
    fi
  fi
  echo
fi

# --- summary ------------------------------------------------------------------
echo "############################################################"
echo "# SUMMARY (version $VERSION)"
echo "# NOTE: Windows (Inno Setup, from-source bootloader) and macOS"
echo "#       arch-native ffmpeg are CI-only — NOT covered locally."
echo "############################################################"
if [ ${#FAILED[@]} -gt 0 ]; then
  echo "PREFLIGHT FAILED: ${FAILED[*]}"
  exit 1
fi
echo "PREFLIGHT ALL GREEN — safe to tag v${VERSION}"
