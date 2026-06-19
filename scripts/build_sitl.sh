#!/usr/bin/env bash
#
# Build a version-matched Betaflight SITL binary for drone-check's
# "view in Configurator" feature, and place it in the SITL cache.
#
# Runs inside WSL (Linux). One-time per firmware version. drone-check itself
# never builds — it only selects a pre-built binary from the cache.
#
# Usage:   bash build_sitl.sh <version> [<version> ...]
# Example: bash build_sitl.sh 4.4.0 4.5.4 2025.12.2
#
# <version> is a Betaflight git tag — both the old semver tags (e.g. 4.4.0) and
# the newer date-based tags (e.g. 2025.12.2) work; the script adapts to the
# source-tree layout each generation uses.
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
  mkdir -p "$WORK_DIR"
  if [ ! -d "$src/.git" ]; then
    rm -rf "$src"
    # Don't create the cache dir until we know the tag exists, so a bad tag
    # doesn't leave an empty version dir behind (which would look "cached").
    if ! git clone --depth 1 --branch "$tag" "$REPO" "$src"; then
      echo "!! $tag: clone failed — no such Betaflight tag?" >&2
      return 1
    fi
  fi
  cd "$src" || { echo "!! $tag: source dir missing" >&2; return 1; }

  # Host GCC is far newer than these (often years-old) sources expect, and the
  # build treats warnings as errors. Relax -Werror so modern toolchains build.
  if grep -q -- "-Werror" Makefile; then
    sed -i "s/ -Werror / -Wno-error /g" Makefile
  fi

  # SITL polls its TCP UART with a 0.5 s update timeout, so CLI echo is only
  # flushed every ~0.5 s when no new bytes arrive — which throttles loading a
  # dump line-by-line to a crawl. Match it to the 100 Hz serial task (10 ms) so
  # loading a configuration is several times faster. The call moved across
  # versions (src/main/target/SITL/target.c on 4.4.x; src/platform/SIMULATOR/
  # sitl.c on 2024.x+) and newer sources already ship 0.01f — so find whichever
  # file still carries the slow value and patch only that one.
  local slow
  for slow in $(grep -rl "dyad_setUpdateTimeout(0.5f)" src 2>/dev/null || true); do
    sed -i "s/dyad_setUpdateTimeout(0.5f)/dyad_setUpdateTimeout(0.01f)/" "$slow"
    echo ">> $tag: throttle patch applied to $slow"
  done

  # The stock SITL target compiles VTX out (USE_VTX is never defined for SITL),
  # so the VTX config table is invisible in the Configurator — exactly the data
  # an inspector most needs. Re-enable the VTX *config* stack (table only, no
  # device driver needed): vtxtable powervalues/powerlabels then load and show.
  # target.h moved with the platform refactor (src/main/target/SITL on 4.4.x;
  # src/platform/SIMULATOR/target/SITL on 2024.x+); on the new layout the file
  # explicitly #undefs the VTX defines, so appending the #defines after them
  # re-enables the stack. Prefer the new location, fall back to the old.
  local sitl_target="" cand
  for cand in \
      src/platform/SIMULATOR/target/SITL/target.h \
      src/main/target/SITL/target.h; do
    if [ -f "$cand" ]; then sitl_target="$cand"; break; fi
  done
  if [ -n "$sitl_target" ] && ! grep -q "DRONE_CHECK_VTX" "$sitl_target"; then
    echo ">> $tag: enabling VTX config table in $sitl_target"
    cat >> "$sitl_target" <<'VTXEOF'

// drone-check: expose the VTX config table in SITL (config data only, no device
// hardware). Lets the Configurator show vtxtable powervalues/powerlabels.
#define DRONE_CHECK_VTX
#define USE_VTX_COMMON
#define USE_VTX_CONTROL
#define USE_VTX_TABLE
VTXEOF
  fi

  # Newer firmware (2024.x+) keeps board configs in a separate repo pulled in as
  # the src/config git submodule, and the build refuses to start until it is
  # hydrated ("Have you hydrated configuration using: 'make configs'?"). SITL does
  # not use a board config, but the Makefile structurally requires the dir to
  # exist. `make configs` does a version-matched `git submodule update --init`.
  # Older firmware (4.4.x) has no such target — guard on it.
  if grep -rq "^configs:" Makefile mk 2>/dev/null && [ ! -d src/config/configs ]; then
    echo ">> $tag: hydrating board configs (make configs)"
    make configs
  fi

  # The build validates the ARM toolchain even for the SITL host target (the
  # tool check is unconditional). arm_sdk_install fetches it into the repo's
  # tools/ dir (no sudo, no PATH change). The rule lives in make/tools.mk on
  # 4.4.x and in mk/tools.mk on 2024.x+, so search the top Makefile and both
  # make-include dirs.
  if grep -rq "arm_sdk_install" Makefile mk make 2>/dev/null; then
    make arm_sdk_install >/dev/null 2>&1 || make arm_sdk_install
  fi

  # Build statically (OPTIONS=SITL_STATIC adds -static -static-libgcc, supported
  # by Betaflight's SITL.mk) so the binary carries its own libc/libm and runs on
  # ANY Linux/WSL regardless of the host glibc version. Without this the ELF is
  # pinned to the build host's glibc (e.g. needs >= 2.38 when built on a current
  # Ubuntu), which breaks running it on an older target distro — exactly what
  # makes the cached binaries portable enough to distribute. Older firmware whose
  # SITL.mk lacks the option just ignores it and builds dynamically.
  echo ">> $tag: building SITL (static)"
  make TARGET=SITL OPTIONS=SITL_STATIC

  if [ ! -f obj/main/betaflight_SITL.elf ]; then
    echo "!! $tag: build produced no betaflight_SITL.elf" >&2
    return 1
  fi
  mkdir -p "$out_dir"
  cp obj/main/betaflight_SITL.elf "$elf"
  chmod +x "$elf"
  echo ">> $tag: cached -> $elf"
}

# Build each requested version. A failure (e.g. a non-existent tag or a broken
# build) is reported but does not abort the rest of the batch.
rc=0
for tag in "$@"; do
  build_one "$tag" || { echo "!! $tag: skipped (see error above)" >&2; rc=1; }
done

echo
echo "SITL cache ($CACHE_DIR):"
ls -1 "$CACHE_DIR" 2>/dev/null || true

exit $rc
