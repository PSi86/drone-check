#!/usr/bin/env bash
#
# Build a version-matched Betaflight SITL binary for drone-check's
# "view in Configurator" feature, and place it in the SITL cache.
#
# Runs inside WSL (Linux). One-time per firmware version. drone-check itself
# never builds — it only selects a pre-built binary from the cache.
#
# Usage:   bash build_sitl.sh <version> [<version> ...]
# Example: bash build_sitl.sh 4.4.0 4.5.4
#
# Requirements (install once):
#   sudo apt-get install -y build-essential ruby git
#
# Result: $CACHE_DIR/<version>/betaflight_SITL.elf
#
set -euo pipefail

CACHE_DIR="${DRONE_CHECK_SITL_CACHE:-$HOME/.cache/drone-check/sitl}"
WORK_DIR="${DRONE_CHECK_SITL_WORK:-$HOME/.cache/drone-check/build}"
REPO="https://github.com/betaflight/betaflight"

if [ "$#" -lt 1 ]; then
  echo "usage: bash build_sitl.sh <version> [<version> ...]" >&2
  exit 2
fi

build_one() {
  local tag="$1"
  local out_dir="$CACHE_DIR/$tag"
  local elf="$out_dir/betaflight_SITL.elf"
  if [ -f "$elf" ]; then
    echo ">> $tag: already cached ($elf)"
    return 0
  fi

  local src="$WORK_DIR/betaflight-$tag"
  echo ">> $tag: preparing source in $src"
  mkdir -p "$WORK_DIR" "$out_dir"
  if [ ! -d "$src/.git" ]; then
    rm -rf "$src"
    git clone --depth 1 --branch "$tag" "$REPO" "$src"
  fi
  cd "$src"

  # Host GCC is far newer than these (often years-old) sources expect, and the
  # build treats warnings as errors. Relax -Werror so modern toolchains build.
  if grep -q -- "-Werror" Makefile; then
    sed -i "s/ -Werror / -Wno-error /g" Makefile
  fi

  # The stock SITL target compiles VTX out (USE_VTX is never defined for SITL),
  # so the VTX config table is invisible in the Configurator — exactly the data
  # an inspector most needs. Re-enable the VTX *config* stack (table only, no
  # device driver needed): vtxtable powervalues/powerlabels then load and show.
  local sitl_target="src/main/target/SITL/target.h"
  if [ -f "$sitl_target" ] && ! grep -q "DRONE_CHECK_VTX" "$sitl_target"; then
    cat >> "$sitl_target" <<'VTXEOF'

// drone-check: expose the VTX config table in SITL (config data only, no device
// hardware). Lets the Configurator show vtxtable powervalues/powerlabels.
#define DRONE_CHECK_VTX
#define USE_VTX_COMMON
#define USE_VTX_CONTROL
#define USE_VTX_TABLE
VTXEOF
  fi

  # Older Makefiles validate the ARM toolchain even for the SITL host target.
  # arm_sdk_install fetches it into the repo's tools/ dir (no sudo, no PATH change).
  if grep -q "arm_sdk_install" Makefile 2>/dev/null; then
    make arm_sdk_install >/dev/null 2>&1 || make arm_sdk_install
  fi

  echo ">> $tag: building SITL"
  make TARGET=SITL

  if [ ! -f obj/main/betaflight_SITL.elf ]; then
    echo "!! $tag: build produced no betaflight_SITL.elf" >&2
    return 1
  fi
  cp obj/main/betaflight_SITL.elf "$elf"
  chmod +x "$elf"
  echo ">> $tag: cached -> $elf"
}

for tag in "$@"; do
  build_one "$tag"
done

echo
echo "SITL cache ($CACHE_DIR):"
ls -1 "$CACHE_DIR"
