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
mkdir -p "$APPDIR/usr/share/metainfo"
mkdir -p "$APPDIR/usr/share/icons/hicolor/scalable/apps"
for _px in 48 64 128 256; do
    mkdir -p "$APPDIR/usr/share/icons/hicolor/${_px}x${_px}/apps"
done

# Copy PyInstaller output
cp -r "$REPO_ROOT/dist/AnkiMiner/"* "$APPDIR/usr/bin/"

# Install .desktop file. The AppDir root copy is a RELATIVE symlink, not a second
# copy: appimagetool reads the root one, desktop-file-validate is run against the
# source, and two copies are two things to drift.
cp "$REPO_ROOT/packaging/appimage/anki-miner.desktop" "$APPDIR/usr/share/applications/"
ln -sf usr/share/applications/anki-miner.desktop "$APPDIR/anki-miner.desktop"

# Install icons. The AppDir root icon and .DirIcon must be a PNG: file managers
# and appimaged read .DirIcon for the thumbnail and the desktop entry, and an SVG
# there leaves the AppImage with no icon at all on most desktops. 256x256 is the
# size the AppImage spec asks for.
cp "$REPO_ROOT/anki_miner/gui/resources/icons/anki_miner.svg" \
   "$APPDIR/usr/share/icons/hicolor/scalable/apps/anki-miner.svg"
for _px in 48 64 128 256; do
    cp "$REPO_ROOT/packaging/icons/anki-miner-${_px}.png" \
       "$APPDIR/usr/share/icons/hicolor/${_px}x${_px}/apps/anki-miner.png"
done
cp "$REPO_ROOT/packaging/icons/anki-miner-256.png" "$APPDIR/anki-miner.png"
ln -sf anki-miner.png "$APPDIR/.DirIcon"

# AppStream metadata. appimagetool REJECTS a metainfo file that fails validation,
# so render_metainfo.sh validating before writing is load-bearing, not belt-and-braces.
# Rendered into dist/ because packaging/nfpm.yaml reads the same file for the .deb.
METAINFO="$REPO_ROOT/dist/anki-miner.metainfo.xml"
"$REPO_ROOT/packaging/render_metainfo.sh" "$VERSION" "$METAINFO"
cp "$METAINFO" "$APPDIR/usr/share/metainfo/io.github._0xzerolight.AnkiMiner.metainfo.xml"

# AppRun. A script, not a symlink to the binary: the host Mesa driver is
# dlopened into this process during GL bring-up and resolves libstdc++ from the
# LD_LIBRARY_PATH that PyInstaller's bootloader points at our _internal/. When
# the host driver is newer than the bundled C++ runtime that aborts the process
# during QOpenGLWidget construction. See packaging/linux-launcher.sh.
install -m 0755 "$REPO_ROOT/packaging/linux-launcher.sh" "$APPDIR/usr/bin/anki-miner-launcher"
rm -f "$APPDIR/AppRun"
ln -sf usr/bin/anki-miner-launcher "$APPDIR/AppRun"

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
