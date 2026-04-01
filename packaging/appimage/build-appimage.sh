#!/usr/bin/env bash
set -euo pipefail

VERSION="${1:?Usage: build-appimage.sh <version>}"
REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"

APPDIR="$REPO_ROOT/dist/AnkiMiner.AppDir"
APPIMAGETOOL_VERSION="continuous"
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

# Download appimagetool if not present
if [ ! -f "$APPIMAGETOOL" ]; then
    echo "Downloading appimagetool..."
    wget -q -O "$APPIMAGETOOL" \
        "https://github.com/AppImage/appimagetool/releases/download/${APPIMAGETOOL_VERSION}/appimagetool-x86_64.AppImage"
    chmod +x "$APPIMAGETOOL"
fi

# Build AppImage (--appimage-extract-and-run avoids FUSE requirement on CI)
export ARCH=x86_64
"$APPIMAGETOOL" --appimage-extract-and-run "$APPDIR" \
    "$REPO_ROOT/dist/AnkiMiner-${VERSION}-Linux-x86_64.AppImage"

echo "AppImage created: dist/AnkiMiner-${VERSION}-Linux-x86_64.AppImage"
