#!/usr/bin/env bash
# Shared bundled-smoke runner — single source of truth for release.yml AND the
# local release_preflight.sh. Given a PyInstaller onedir (e.g. dist/AnkiMiner),
# run the three bundle-validation smokes the release asserts and fail closed on
# any miss. Keep this in lock-step with the smokes documented in release.yml;
# both call this script so they cannot drift.
#
# Usage: scripts/bundle_smoke.sh <dist_dir>     # e.g. dist/AnkiMiner
#
# Smokes (all headless via QT_QPA_PLATFORM=offscreen; none touch the network):
#   1. youtube   ANKI_MINER_SMOKE=youtube                  -> BUNDLED_SMOKE_PASS
#   2. asr       ANKI_MINER_SMOKE=asr  HF_HUB_OFFLINE=1     -> BUNDLED_SMOKE_PASS
#   3. ffmpeg    bundled ffmpeg has the required encoders   -> encoders present
set -euo pipefail

DIST="${1:?Usage: bundle_smoke.sh <dist_dir> (e.g. dist/AnkiMiner)}"
if [ ! -d "$DIST" ]; then
  echo "::error::dist dir not found: $DIST" >&2
  exit 2
fi

# Locate the app binary at the onedir root (AnkiMiner / AnkiMiner.exe).
APP=""
for cand in "$DIST/AnkiMiner" "$DIST/AnkiMiner.exe"; do
  [ -f "$cand" ] && APP="$cand" && break
done
if [ -z "$APP" ]; then
  echo "::error::AnkiMiner binary not found in $DIST" >&2
  ls -la "$DIST" >&2 || true
  exit 2
fi
echo "App binary: $APP"

FAILED=()

# --- 1. YouTube smoke: yt-dlp extractor registry survived PyInstaller ---------
echo "=== smoke: youtube ==="
if ANKI_MINER_SMOKE=youtube QT_QPA_PLATFORM=offscreen "$APP" 2>&1 | tee smoke_youtube.log \
  && grep -q "BUNDLED_SMOKE_PASS" smoke_youtube.log; then
  echo "PASS youtube"
else
  echo "FAIL youtube"
  FAILED+=("youtube")
fi
echo

# --- 2. ASR smoke: faster-whisper + ctranslate2 + av resolve (no download) ----
# Skipped on builds without the [asr] extra (Intel macOS: onnxruntime has no
# x86_64-mac wheel). BUNDLE_SMOKE_SKIP_ASR=1 -> skip; the bundle ships no
# faster-whisper, so the smoke would (correctly) fail.
echo "=== smoke: asr ==="
if [ "${BUNDLE_SMOKE_SKIP_ASR:-}" = "1" ]; then
  echo "SKIP asr (BUNDLE_SMOKE_SKIP_ASR=1 — build has no [asr] extra)"
elif ANKI_MINER_SMOKE=asr HF_HUB_OFFLINE=1 QT_QPA_PLATFORM=offscreen "$APP" 2>&1 | tee smoke_asr.log \
  && grep -q "BUNDLED_SMOKE_PASS" smoke_asr.log; then
  echo "PASS asr"
else
  echo "FAIL asr"
  FAILED+=("asr")
fi
echo

# --- 3. ffmpeg encoder smoke: bundled ffmpeg ships the required encoders -------
# libwebp_anim is asserted separately because still-image libwebp builds would
# otherwise pass while animated-WebP screenshots fail at runtime.
# The required set is overridable via BUNDLE_SMOKE_FFMPEG_ENCODERS (space-
# separated). Default is the full set. Intel macOS drops libsvtav1: evermeet.cx
# x86_64 ffmpeg ships no SVT-AV1, and the app degrades gracefully (AVIF animated
# screenshots fall back / report unavailable; WebP animated + static still work).
REQUIRED_ENCODERS="${BUNDLE_SMOKE_FFMPEG_ENCODERS:-libmp3lame libopus libsvtav1 libwebp libwebp_anim}"
echo "=== smoke: ffmpeg encoders (required: $REQUIRED_ENCODERS) ==="
FF=""
for name in ffmpeg ffmpeg.exe; do
  FF=$(find "$DIST" -type f -name "$name" | head -1)
  [ -n "$FF" ] && break
done
if [ -z "$FF" ]; then
  echo "::error::Bundled ffmpeg not found under $DIST — spec did not bundle vendor/ffmpeg/"
  find "$DIST" -maxdepth 3 -name 'ffmpeg*' || true
  FAILED+=("ffmpeg-encoders")
else
  echo "Found bundled ffmpeg: $FF"
  chmod +x "$FF" 2>/dev/null || true
  ENC=$("$FF" -hide_banner -encoders 2>/dev/null || true)
  MISSING=""
  for e in $REQUIRED_ENCODERS; do
    echo "$ENC" | grep -q "$e" || MISSING="$MISSING $e"
  done
  if [ -n "$MISSING" ]; then
    echo "::error::Bundled ffmpeg is missing required encoder(s):$MISSING"
    FAILED+=("ffmpeg-encoders")
  else
    echo "BUNDLED_FFMPEG_ENCODERS_PASS: $REQUIRED_ENCODERS"
    echo "PASS ffmpeg-encoders"
  fi
fi
echo

# --- summary ------------------------------------------------------------------
if [ ${#FAILED[@]} -gt 0 ]; then
  echo "BUNDLE_SMOKE_FAILED: ${FAILED[*]}"
  exit 1
fi
echo "BUNDLE_SMOKE_ALL_PASS"
