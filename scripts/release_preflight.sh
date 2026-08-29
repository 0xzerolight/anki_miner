#!/usr/bin/env bash
# Local release-CI preflight — run BEFORE pushing any v* tag.
#
# Mirrors the Linux build job of .github/workflows/release.yml as faithfully as
# a Linux box allows: isolated venv (.[asr,zh,ko] + pinned PyInstaller), SHA-verified
# vendor fetch (ffmpeg + alass + yt-dlp + libmpv), PyInstaller build, the three bundle smokes
# (via scripts/bundle_smoke.sh — the same script CI runs), then AppImage + .deb.
#
# CANNOT reproduce (CI-only, by platform): Windows Inno Setup, the Windows
# from-source bootloader, macOS arch-native ffmpeg. The three smokes are pure
# Python import checks, so import/collection failures (like the av miss that
# broke v2.7.1) surface here on Linux exactly as they did on Windows/macOS.
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
FFMPEG_URL="https://github.com/BtbN/FFmpeg-Builds/releases/download/autobuild-2026-05-31-13-22/ffmpeg-n8.1.1-9-g58d4114d36-linux64-gpl-8.1.tar.xz"
FFMPEG_SHA256="0d14781b885c491f5c3b799cbe7d3a26ba8a7eb01935483185e31ea7d79c8cd3"
ALASS_URL="https://github.com/kaegi/alass/releases/download/v2.0.0/alass-linux64"
ALASS_SHA256="7bd0b9ae7e035d3ba940eacffb21243614df36231d47f21f0b4ce42001ab7fcd"
LIBMPV_URL="https://github.com/0xzerolight/anki_miner/releases/download/vendor-libmpv-20260712/libmpv-linux-x86_64.tar.gz"
LIBMPV_SHA256="5d9278463edab8f2a467f45c2c66416070d4e1543024df30fed2f721def663c1"
NFPM_VERSION="2.46.0"
NFPM_SHA256="43b4cb72cde2d6e61c02e5b330e3276882252bf67c057e089957f9dbd2c8de42"

FAILED=()
die() { echo "::error::$*" >&2; exit 1; }

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
# Install/refresh the bundle deps exactly as CI does: .[asr,zh,ko] constrained by
# the lock, plus the pinned PyInstaller. Idempotent — pip no-ops if satisfied.
"$PIP" install ".[asr,zh,ko]" -c requirements.lock || die "pip install .[asr,zh,ko] failed"
"$PIP" install "pyinstaller==${PYINSTALLER_VERSION}" || die "pyinstaller install failed"
echo "pyinstaller: $("$VENV/bin/pyinstaller" --version)"
echo

# --- 3. vendor fetch (SHA-verified, cached) -----------------------------------
echo "=== vendor ffmpeg + alass + yt-dlp + libmpv ==="
mkdir -p "$CACHE" vendor/ffmpeg vendor/alass vendor/yt-dlp vendor/libmpv \
  licenses/alass licenses/yt-dlp licenses/libmpv
if [ "$CLEAN" = "1" ]; then
  rm -f vendor/ffmpeg/ffmpeg vendor/ffmpeg/ffprobe vendor/alass/alass vendor/yt-dlp/yt-dlp \
    vendor/libmpv/libmpv.so.2 licenses/libmpv/Copyright licenses/libmpv/SOURCES.txt
fi

verify_sha() { echo "$2  $1" | sha256sum -c - >/dev/null 2>&1; }

if [ ! -f vendor/ffmpeg/ffmpeg ] || [ ! -f vendor/ffmpeg/ffprobe ]; then
  TARBALL="$CACHE/ffmpeg-linux64.tar.xz"
  if [ ! -f "$TARBALL" ] || ! verify_sha "$TARBALL" "$FFMPEG_SHA256"; then
    curl -fL "$FFMPEG_URL" -o "$TARBALL" || die "ffmpeg download failed"
  fi
  verify_sha "$TARBALL" "$FFMPEG_SHA256" || die "ffmpeg SHA256 mismatch"
  rm -rf "$CACHE/ff-extract"; mkdir -p "$CACHE/ff-extract"
  tar -xf "$TARBALL" -C "$CACHE/ff-extract"
  cp "$(find "$CACHE/ff-extract" -type f -path '*/bin/ffmpeg' | head -1)" vendor/ffmpeg/ffmpeg
  cp "$(find "$CACHE/ff-extract" -type f -path '*/bin/ffprobe' | head -1)" vendor/ffmpeg/ffprobe
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

# yt-dlp: version + digest come from .github/ytdlp-pin.json, the same file
# release.yml reads, so a bump cannot land in one place only. Vendoring it here is
# not optional bookkeeping — step 5's bundled smoke asserts the binary is present at
# sys._MEIPASS/bin/, so without this the youtube leg fails.
YTDLP_PIN=".github/ytdlp-pin.json"
YTDLP_VERSION="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["version"])' "$YTDLP_PIN")"
YTDLP_ASSET="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["assets"]["linux"]["asset"])' "$YTDLP_PIN")"
YTDLP_SHA256="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["assets"]["linux"]["sha256"])' "$YTDLP_PIN")"
YTDLP_INSTALL_AS="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["assets"]["linux"]["install_as"])' "$YTDLP_PIN")"
YTDLP_URL="https://github.com/yt-dlp/yt-dlp/releases/download/${YTDLP_VERSION}/${YTDLP_ASSET}"

# Warn-only: a stale pin must not block a local preflight, and the gate itself
# already degrades to a warning when the GitHub API is unreachable.
"$VENV/bin/python" scripts/check_ytdlp_pin.py || echo "WARNING: yt-dlp pin check reported a problem (continuing)"

if [ ! -f "vendor/yt-dlp/${YTDLP_INSTALL_AS}" ]; then
  YTDLP_DL="$CACHE/${YTDLP_ASSET}-${YTDLP_VERSION}"
  if [ ! -f "$YTDLP_DL" ] || ! verify_sha "$YTDLP_DL" "$YTDLP_SHA256"; then
    curl -fL "$YTDLP_URL" -o "$YTDLP_DL" || die "yt-dlp download failed"
  fi
  verify_sha "$YTDLP_DL" "$YTDLP_SHA256" || die "yt-dlp SHA256 mismatch"
  cp "$YTDLP_DL" "vendor/yt-dlp/${YTDLP_INSTALL_AS}"
  chmod +x "vendor/yt-dlp/${YTDLP_INSTALL_AS}"
  [ -f licenses/yt-dlp/LICENSE ] || curl -fL "https://raw.githubusercontent.com/yt-dlp/yt-dlp/${YTDLP_VERSION}/LICENSE" -o licenses/yt-dlp/LICENSE || true
  # Same pair release.yml fetches — the spec bundles the whole directory, so the
  # local preflight build must not produce a bundle the real matrix would not.
  [ -f licenses/yt-dlp/THIRD_PARTY_LICENSES.txt ] || curl -fL "https://raw.githubusercontent.com/yt-dlp/yt-dlp/${YTDLP_VERSION}/THIRD_PARTY_LICENSES.txt" -o licenses/yt-dlp/THIRD_PARTY_LICENSES.txt || true
fi
echo "vendor/yt-dlp: $(ls vendor/yt-dlp)"
echo

# --- 4. PyInstaller build -----------------------------------------------------
echo "=== pyinstaller build ==="
rm -rf build dist/AnkiMiner
"$VENV/bin/pyinstaller" anki_miner.spec || die "PyInstaller build failed"
[ -d dist/AnkiMiner ] || die "dist/AnkiMiner not produced"
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
if BUNDLE_SMOKE_SKIP_WHISPERCPP=1 bash scripts/bundle_smoke.sh dist/AnkiMiner; then
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
