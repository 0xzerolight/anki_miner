#!/usr/bin/env bash
set -euo pipefail

VERSION="${1:?Usage: build-appimage.sh <version>}"
REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"

APPDIR="$REPO_ROOT/dist/AnkiMiner.AppDir"
# Pin to a dated/versioned release + SHA256 instead of the mutable "continuous"
# build (which was unpinned AND unverified — the worst supply-chain gap here).
# SHA256 matches the GitHub release asset digest for 1.9.1 (cross-verified by
# downloading and hashing the asset). Bump both together when upgrading.
APPIMAGETOOL_VERSION="1.9.1"
APPIMAGETOOL_SHA256="ed4ce84f0d9caff66f50bcca6ff6f35aae54ce8135408b3fa33abfc3cb384eb0"
APPIMAGETOOL="$REPO_ROOT/dist/appimagetool"

echo "Building AppImage for Anki Miner v${VERSION}..."

# Create AppDir structure
mkdir -p "$APPDIR/usr/bin"
mkdir -p "$APPDIR/usr/share/applications"
mkdir -p "$APPDIR/usr/share/icons/hicolor/scalable/apps"

# Copy PyInstaller output
cp -r "$REPO_ROOT/dist/AnkiMiner/"* "$APPDIR/usr/bin/"

# Install .desktop file
cp "$REPO_ROOT/packaging/appimage/anki-miner.desktop" "$APPDIR/anki-miner.desktop"
cp "$REPO_ROOT/packaging/appimage/anki-miner.desktop" "$APPDIR/usr/share/applications/"

# Install icon
cp "$REPO_ROOT/anki_miner/gui/resources/icons/anki_miner.svg" "$APPDIR/anki-miner.svg"
cp "$REPO_ROOT/anki_miner/gui/resources/icons/anki_miner.svg" \
   "$APPDIR/usr/share/icons/hicolor/scalable/apps/anki-miner.svg"

# Create AppRun symlink
ln -sf usr/bin/AnkiMiner "$APPDIR/AppRun"

# Download appimagetool if not present, then verify its SHA256 (fail-closed:
# a checksum mismatch blocks the build rather than packaging an unverified tool).
if [ ! -f "$APPIMAGETOOL" ]; then
    echo "Downloading appimagetool ${APPIMAGETOOL_VERSION}..."
    wget -q -O "$APPIMAGETOOL" \
        "https://github.com/AppImage/appimagetool/releases/download/${APPIMAGETOOL_VERSION}/appimagetool-x86_64.AppImage"
fi
echo "${APPIMAGETOOL_SHA256}  ${APPIMAGETOOL}" | sha256sum -c -
chmod +x "$APPIMAGETOOL"

# Build AppImage (--appimage-extract-and-run avoids FUSE requirement on CI)
export ARCH=x86_64
"$APPIMAGETOOL" --appimage-extract-and-run "$APPDIR" \
    "$REPO_ROOT/dist/AnkiMiner-${VERSION}-Linux-x86_64.AppImage"

echo "AppImage created: dist/AnkiMiner-${VERSION}-Linux-x86_64.AppImage"
