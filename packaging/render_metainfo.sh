#!/usr/bin/env bash
# Render packaging/appstream/anki-miner.metainfo.xml.in for one release.
#
# Both Linux artifacts carry the same AppStream metadata: the AppImage installs
# it into usr/share/metainfo/ (AppImageHub reads it, and appimagetool REJECTS a
# metainfo file that fails validation), the .deb into /usr/share/metainfo/
# (GNOME Software and KDE Discover read it there). Rendering it from one
# template is what keeps the <release> block from going stale.
#
# Usage: packaging/render_metainfo.sh <version> <out-path>
set -euo pipefail

VERSION="${1:?Usage: render_metainfo.sh <version> <out-path>}"
OUT="${2:?Usage: render_metainfo.sh <version> <out-path>}"
REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
TEMPLATE="$REPO_ROOT/packaging/appstream/anki-miner.metainfo.xml.in"

# SOURCE_DATE_EPOCH when set (reproducible builds), today otherwise.
DATE="$(date -u -d "@${SOURCE_DATE_EPOCH:-$(date +%s)}" +%Y-%m-%d 2>/dev/null \
  || date -u -r "${SOURCE_DATE_EPOCH:-$(date +%s)}" +%Y-%m-%d)"

mkdir -p "$(dirname "$OUT")"
sed -e "s|@VERSION@|${VERSION}|g" -e "s|@DATE@|${DATE}|g" "$TEMPLATE" > "$OUT"

# Fail closed. An invalid metainfo file is worse than none: appimagetool refuses
# to build with one, and a software centre silently drops the whole component.
appstreamcli validate --no-net "$OUT"

echo "Rendered metainfo: $OUT (version ${VERSION}, date ${DATE})"
