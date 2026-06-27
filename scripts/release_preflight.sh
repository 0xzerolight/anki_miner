#!/usr/bin/env bash
# Local release-CI preflight — run BEFORE pushing any v* tag.
#
# Mirrors the Linux build job of .github/workflows/release.yml as faithfully as
# a Linux box allows: isolated venv (.[asr] + pinned PyInstaller), SHA-verified
# vendor fetch (ffmpeg + alass), PyInstaller build, the three bundle smokes
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
# Install/refresh the bundle deps exactly as CI does: .[asr] constrained by the
# lock, plus the pinned PyInstaller. Idempotent — pip no-ops if satisfied.
"$PIP" install ".[asr]" -c requirements.lock || die "pip install .[asr] failed"
"$PIP" install "pyinstaller==${PYINSTALLER_VERSION}" || die "pyinstaller install failed"
echo "pyinstaller: $("$VENV/bin/pyinstaller" --version)"
echo

# --- 3. vendor fetch (SHA-verified, cached) -----------------------------------
echo "=== vendor ffmpeg + alass ==="
mkdir -p "$CACHE" vendor/ffmpeg vendor/alass licenses/alass
if [ "$CLEAN" = "1" ]; then rm -f vendor/ffmpeg/ffmpeg vendor/ffmpeg/ffprobe vendor/alass/alass; fi

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

# --- 5. smokes (shared with CI) ----------------------------------------------
echo "=== bundle smokes ==="
if bash scripts/bundle_smoke.sh dist/AnkiMiner; then
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

  # --- 6b. .deb (mirror release.yml deb-stage strip + nfpm) -------------------
  echo "=== .deb ==="
  if [ ! -x "$CACHE/nfpm" ]; then
    NFPM_TGZ="$CACHE/nfpm.tar.gz"
    curl -fL "https://github.com/goreleaser/nfpm/releases/download/v${NFPM_VERSION}/nfpm_${NFPM_VERSION}_Linux_x86_64.tar.gz" -o "$NFPM_TGZ" || die "nfpm download failed"
    verify_sha "$NFPM_TGZ" "$NFPM_SHA256" || die "nfpm SHA256 mismatch"
    tar -xzf "$NFPM_TGZ" -C "$CACHE" nfpm
    chmod +x "$CACHE/nfpm"
  fi
  export VERSION
  rm -rf dist/deb-stage
  cp -a dist/AnkiMiner dist/deb-stage
  rm -f dist/deb-stage/_internal/bin/ffmpeg dist/deb-stage/_internal/bin/ffprobe
  rm -f dist/deb-stage/bin/ffmpeg dist/deb-stage/bin/ffprobe
  rm -rf dist/deb-stage/_internal/ctranslate2 dist/deb-stage/_internal/faster_whisper \
         dist/deb-stage/_internal/onnxruntime dist/deb-stage/_internal/av \
         dist/deb-stage/ctranslate2 dist/deb-stage/faster_whisper \
         dist/deb-stage/onnxruntime dist/deb-stage/av
  find dist/deb-stage -name 'libctranslate2*.so*' -delete
  BAD=$(find dist/deb-stage -type f \( -name 'ffmpeg' -o -name 'ffprobe' -o -name 'libctranslate2*.so*' \) ; \
        find dist/deb-stage -type d \( -name 'ctranslate2' -o -name 'faster_whisper' \))
  if [ -n "$BAD" ]; then
    echo "::error::ASR/ffmpeg artifacts still present in deb stage tree:"; echo "$BAD"
    FAILED+=("deb")
  elif "$CACHE/nfpm" package --config packaging/nfpm.yaml --packager deb \
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
