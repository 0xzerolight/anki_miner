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

# --- 2b. whisper.cpp (pywhispercpp Vulkan) IMPORT/LOADABILITY gate -------------
# IMPORT/LOADABILITY ONLY. This proves the from-source Vulkan pywhispercpp wheel
# replaced the CPU wheel and was collected into the frozen tree, and that the
# bundled binary can load the ggml/whisper native chain far enough to return an
# integer Vulkan device count. It proves NOTHING about Vulkan transcription: the
# CI runners have no GPU, so the device count is expected to be 0 (CPU fallback)
# and a real GPU transcription pass can only be validated manually on hardware.
#
# Three cheap, GPU-free assertions, all fail-closed:
#   (a) the ggml-vulkan backend MODULE is present in the bundle (libggml-vulkan*
#       on Linux / ggml-vulkan*.dll on Windows). Its presence is what makes
#       _engine.whisper_cpp_available() report a GPU-capable build; if the wheel
#       replacement or the hook collection silently failed, this lib is missing
#       and the bundle would be a CPU-only build masquerading as Vulkan.
#   (b) the frozen binary's hidden Vulkan probe (ANKI_MINER_ASR_VULKAN_PROBE=1,
#       app.main routes it to _vulkan_probe before any Qt init) runs the cold
#       ctypes load of ggml-vulkan in a child and prints the device count — a
#       single integer on stdout, exit 0. This is exactly the value
#       _engine.vulkan_device_count() parses, so a clean integer here is the
#       "isinstance(vulkan_device_count(), int)" loadability gate.
#   (c) the frozen binary actually `import pywhispercpp.model`s (via
#       ANKI_MINER_SMOKE=whispercpp -> _run_whispercpp_bundled_smoke, which calls
#       get_whisper_cpp_model_cls()). This is the REAL runtime import path the
#       Vulkan engine takes — pywhispercpp.model pulls pywhispercpp.constants
#       (-> platformdirs) and pywhispercpp.utils (-> requests, tqdm) at module
#       load. Neither (a) the filesystem find nor (b) the ctypes probe imports
#       pywhispercpp.model, so only (c) catches a transitive runtime dep missing
#       from the bundle env (e.g. platformdirs not installed) or a frozen
#       find_spec.origin failure. IMPORT/LOADABILITY ONLY — no GPU assertion.
#
# Skipped on macOS (BOTH arm64 and Intel): macOS stays on the CT2/Metal path and
# ships no Vulkan pywhispercpp wheel, so none of these assertions apply. Set
# BUNDLE_SMOKE_SKIP_WHISPERCPP=1 on the macOS builds.
echo "=== smoke: whispercpp-vulkan (import/loadability only — NOT a GPU test) ==="
if [ "${BUNDLE_SMOKE_SKIP_WHISPERCPP:-}" = "1" ]; then
  echo "SKIP whispercpp-vulkan (BUNDLE_SMOKE_SKIP_WHISPERCPP=1 — macOS CT2/Metal build)"
else
  WHISPERCPP_OK=1
  # (a) ggml-vulkan backend MODULE present in the frozen tree.
  VK_LIB=""
  for pat in 'libggml-vulkan*.so*' 'ggml-vulkan*.dll' 'libggml-vulkan*.dylib'; do
    VK_LIB=$(find "$DIST" -type f -name "$pat" | head -1)
    [ -n "$VK_LIB" ] && break
  done
  if [ -z "$VK_LIB" ]; then
    echo "::error::ggml-vulkan backend lib not found under $DIST — Vulkan pywhispercpp wheel was not bundled (wheel replacement or hook collection failed)"
    find "$DIST" -name 'libggml*' -o -name 'ggml*.dll' 2>/dev/null | head -20 || true
    WHISPERCPP_OK=0
  else
    echo "Found ggml-vulkan backend lib: $VK_LIB"
  fi
  # (b) frozen binary's Vulkan probe prints a single integer device count, exit 0.
  if PROBE_OUT=$(ANKI_MINER_ASR_VULKAN_PROBE=1 QT_QPA_PLATFORM=offscreen "$APP" 2>probe_err.log); then
    PROBE_OUT=$(printf '%s' "$PROBE_OUT" | tr -d '[:space:]')
    if printf '%s' "$PROBE_OUT" | grep -Eq '^[0-9]+$'; then
      echo "Vulkan device-count probe returned an integer: $PROBE_OUT (0 expected on GPU-less runners)"
    else
      echo "::error::Vulkan probe did not print a single integer device count: '$PROBE_OUT'"
      cat probe_err.log >&2 || true
      WHISPERCPP_OK=0
    fi
  else
    # The probe ALWAYS exits 0 by contract (its Python try/except prints "0" on any
    # error — an absent lib or a missing NEEDED dep raises OSError and is caught). The
    # ONE uncatchable case is a C++ abort: on a runner with the Vulkan loader but NO
    # ICD (no GPU driver — the norm for hosted CI), ggml-vulkan's get_device_count
    # calls vk::createInstance, which THROWS vk::IncompatibleDriverError ->
    # std::terminate -> SIGABRT, which ctypes/Python cannot catch, so the frozen
    # binary exits nonzero. That abort still PROVES loadability: the binary loaded
    # libggml-vulkan, resolved its symbols, and ran through libvulkan as far as
    # createInstance. The shipping app tolerates this identically — vulkan_device_count()
    # runs this probe as a subprocess and treats a nonzero exit as 0 devices (CPU
    # fallback). So an IncompatibleDriver abort is the GPU-less loadability-proven
    # outcome; any OTHER nonzero exit is a genuine load failure and still fails closed.
    if grep -qiE 'IncompatibleDriver|VK_ERROR_INCOMPATIBLE_DRIVER' probe_err.log; then
      echo "Vulkan probe aborted with IncompatibleDriver (loader present, no ICD on this GPU-less runner) — loadability proven, 0 devices"
    else
      echo "::error::Vulkan probe exited nonzero for a non-driver reason (frozen binary could not load the ASR/ggml chain)"
      cat probe_err.log >&2 || true
      WHISPERCPP_OK=0
    fi
  fi
  # (c) frozen binary imports pywhispercpp.model — the REAL runtime import chain
  # (pulls platformdirs/requests/tqdm). Catches a transitive dep missing from the
  # bundle env that (a)/(b) cannot. Import/loadability only; no GPU.
  if ANKI_MINER_SMOKE=whispercpp QT_QPA_PLATFORM=offscreen "$APP" 2>&1 | tee smoke_whispercpp.log \
    && grep -q "BUNDLED_SMOKE_PASS" smoke_whispercpp.log; then
    echo "pywhispercpp.model import resolved in the frozen bundle"
  else
    echo "::error::pywhispercpp.model failed to import from the frozen bundle (transitive runtime dep missing, e.g. platformdirs, or find_spec.origin failed)"
    WHISPERCPP_OK=0
  fi
  if [ "$WHISPERCPP_OK" = "1" ]; then
    echo "BUNDLED_WHISPERCPP_VULKAN_LOADABLE_PASS"
    echo "PASS whispercpp-vulkan"
  else
    echo "FAIL whispercpp-vulkan"
    FAILED+=("whispercpp-vulkan")
  fi
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
