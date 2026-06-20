#!/usr/bin/env bash
#
# Build a version-matched bf-configd backend binary from OFFICIAL Betaflight
# source, and place it in the bf-configd cache.
#
# bf-configd serves a `dump all` to the Configurator over MSP without starting
# the flight loop (a lighter, read-only alternative to SITL). The backend is the
# real Betaflight CLI/config/MSP code built for a host CONFIGD target, so it is
# produced the same way SITL is: clone the official tag, apply the bf-configd
# patch series, build, cache. drone-check itself never builds — it only selects
# a pre-built binary from the cache.
#
# Runs inside the Linux environment that hosts the binaries (WSL on Windows,
# native on Linux). One-time per firmware version/family.
#
# Usage:   bash build_bfcd.sh <version> [<version> ...]
# Example: bash build_bfcd.sh 4.5.3
#
# <version> is a Betaflight git tag (old semver e.g. 4.5.3 or date-based e.g.
# 2025.12.2). The binary is cached per *family* (4.5.3 -> family 4.5), matching
# the compatibility matrix in config/bfcd_matrix.yaml.
#
# Requirements (install once):
#   sudo apt-get install -y build-essential ruby git
#
# Result: $CACHE_DIR/<family>/bf-configd.elf
#
# STATUS: scaffolding. The CONFIGD target and the patch series under
# bf-configd/patches/ are not implemented yet, so this script wires up the full
# clone -> patch -> build -> cache pipeline but stops with a clear message when a
# family has no patch series. See bf-configd/README.md and docs/bfcd/.
#
set -euo pipefail

CACHE_DIR="${DRONE_CHECK_BFCD_CACHE:-$HOME/.cache/drone-check/bfcd}"
WORK_DIR="${DRONE_CHECK_BFCD_WORK:-$HOME/.cache/drone-check/bfcd-build}"
REPO="https://github.com/betaflight/betaflight"

# Patch series live next to this script, one dir per firmware family.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PATCHES_ROOT="${SCRIPT_DIR}/../bf-configd/patches"

if [ "$#" -lt 1 ]; then
  echo "usage: bash build_bfcd.sh <version> [<version> ...]" >&2
  exit 2
fi

# 4.5.3 -> 4.5 ; 2025.12.2 -> 2025.12 (mirrors bfcd.metadata.firmware_family).
family_of() { echo "$1" | cut -d. -f1-2; }

build_one() {
  local tag="$1"
  local family
  family="$(family_of "$tag")"
  local out_dir="$CACHE_DIR/$family"
  local elf="$out_dir/bf-configd.elf"
  if [ -f "$elf" ]; then
    echo ">> $tag (family $family): already cached ($elf)"
    return 0
  fi

  local patch_dir="$PATCHES_ROOT/betaflight-$family"
  if [ ! -d "$patch_dir" ] || ! ls "$patch_dir"/*.patch >/dev/null 2>&1; then
    echo "!! $tag: no bf-configd patch series for family $family" >&2
    echo "   (expected patches in $patch_dir/*.patch)" >&2
    echo "   The native CONFIGD backend is not implemented yet — see" >&2
    echo "   bf-configd/README.md for the patch plan (BFCD-003..005)." >&2
    return 1
  fi

  local src="$WORK_DIR/betaflight-$tag"
  echo ">> $tag: preparing source in $src"
  mkdir -p "$WORK_DIR"
  if [ ! -d "$src/.git" ]; then
    rm -rf "$src"
    if ! git clone --depth 1 --branch "$tag" "$REPO" "$src"; then
      echo "!! $tag: clone failed — no such Betaflight tag?" >&2
      return 1
    fi
  fi
  cd "$src" || { echo "!! $tag: source dir missing" >&2; return 1; }

  # Apply the bf-configd patch series (CONFIGD target, fake-serial CLI/MSP layer,
  # runtime stubs). Idempotent: skip a patch that is already applied.
  local p
  for p in "$patch_dir"/*.patch; do
    if git apply --check "$p" >/dev/null 2>&1; then
      echo ">> $tag: applying $(basename "$p")"
      git apply "$p"
    elif git apply --reverse --check "$p" >/dev/null 2>&1; then
      echo ">> $tag: $(basename "$p") already applied"
    else
      echo "!! $tag: patch does not apply cleanly: $(basename "$p")" >&2
      return 1
    fi
  done

  # Host GCC is newer than these sources expect and the build treats warnings as
  # errors; relax -Werror so modern toolchains build (same as the SITL build).
  if grep -q -- "-Werror" Makefile; then
    sed -i "s/ -Werror / -Wno-error /g" Makefile
  fi

  # Newer firmware (2024.x+) needs the board-config submodule hydrated before the
  # build will start, even for a host target.
  if grep -rq "^configs:" Makefile mk 2>/dev/null && [ ! -d src/config/configs ]; then
    echo ">> $tag: hydrating board configs (make configs)"
    make configs
  fi

  # The build validates the ARM toolchain even for a host target; fetch it into
  # the repo's tools/ dir (no sudo, no PATH change).
  if grep -rq "arm_sdk_install" Makefile mk make 2>/dev/null; then
    make arm_sdk_install >/dev/null 2>&1 || make arm_sdk_install
  fi

  # Build the CONFIGD host target (added by the patch series), static so the
  # binary is portable across glibc versions like the SITL bundles.
  echo ">> $tag: building CONFIGD (static)"
  make TARGET=CONFIGD OPTIONS=SITL_STATIC

  local built=""
  for cand in obj/main/betaflight_CONFIGD.elf obj/main/bf-configd.elf; do
    if [ -f "$cand" ]; then built="$cand"; break; fi
  done
  if [ -z "$built" ]; then
    echo "!! $tag: build produced no CONFIGD binary" >&2
    return 1
  fi
  mkdir -p "$out_dir"
  cp "$built" "$elf"
  chmod +x "$elf"
  echo ">> $tag: cached -> $elf"
}

rc=0
for tag in "$@"; do
  build_one "$tag" || { echo "!! $tag: skipped (see error above)" >&2; rc=1; }
done

echo
echo "bf-configd cache ($CACHE_DIR):"
ls -1 "$CACHE_DIR" 2>/dev/null || true

exit $rc
