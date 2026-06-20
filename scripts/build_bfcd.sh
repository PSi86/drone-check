#!/usr/bin/env bash
#
# Build a version-matched bf-configd backend binary from OFFICIAL Betaflight
# source, and place it in the bf-configd cache.
#
# bf-configd serves a `dump all` to the Configurator over MSP as a read-only
# snapshot. The backend is the real Betaflight CLI/config/MSP code, derived from
# the SITL host target with a small read-only guard: it refuses every MSP write,
# so the Configurator can view everything but cannot change or persist anything.
# Like the SITL build, the derivation is scripted (clone official tag -> patch
# in place -> build), so it tracks official Betaflight with no hand-maintained
# fork. drone-check itself never builds — it only selects a cached binary.
#
# Runs inside the Linux environment that hosts the binaries (WSL on Windows,
# native on Linux). One-time per firmware version/family.
#
# Usage:   bash build_bfcd.sh <version> [<version> ...]
# Example: bash build_bfcd.sh 4.5.3
#
# <version> is a Betaflight git tag. The binary is cached per *family*
# (4.5.3 -> family 4.5), matching config/bfcd_matrix.yaml.
#
# Requirements (install once):
#   sudo apt-get install -y build-essential ruby git
#
# Result: $CACHE_DIR/<family>/bf-configd.elf
#
set -euo pipefail

CACHE_DIR="${DRONE_CHECK_BFCD_CACHE:-$HOME/.cache/drone-check/bfcd}"
WORK_DIR="${DRONE_CHECK_BFCD_WORK:-$HOME/.cache/drone-check/bfcd-build}"
REPO="https://github.com/betaflight/betaflight"

# Optional extra patch series, one dir per family, applied after the scripted
# derivation (for changes not expressible as the in-place edits below).
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PATCHES_ROOT="${SCRIPT_DIR}/../bf-configd/patches"

if [ "$#" -lt 1 ]; then
  echo "usage: bash build_bfcd.sh <version> [<version> ...]" >&2
  exit 2
fi

# 4.5.3 -> 4.5 ; 2025.12.2 -> 2025.12 (mirrors bfcd.metadata.firmware_family).
family_of() { echo "$1" | cut -d. -f1-2; }

# Inject the read-only guard at the single MSP write chokepoint
# (mspCommonProcessInCommand), gated on -DCONFIGD. Idempotent.
apply_readonly_guard() {
  local msp="$1"
  if grep -q "bf-configd: read-only snapshot mode" "$msp"; then
    echo "   read-only guard already present"
    return 0
  fi
  if ! grep -q "mspCommonProcessInCommand(mspDescriptor_t" "$msp"; then
    echo "!! read-only guard anchor not found in $msp" >&2
    return 1
  fi
  awk '
/mspCommonProcessInCommand\(mspDescriptor_t/ { sig=1 }
sig==1 && /^\{/ {
    print
    print "#ifdef CONFIGD"
    print "    // bf-configd: read-only snapshot mode. Refuse every MSP write/in"
    print "    // command so the Configurator cannot modify or persist the inspected"
    print "    // configuration; reads (out commands) are still answered normally."
    print "    UNUSED(srcDesc); UNUSED(cmdMSP); UNUSED(src); UNUSED(mspPostProcessFn);"
    print "    return MSP_RESULT_ERROR;"
    print "#endif"
    sig=2
    next
}
{ print }
' "$msp" > "$msp.new" && mv "$msp.new" "$msp"
  echo "   read-only guard injected"
}

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

  # Host GCC is newer than these sources expect and the build treats warnings as
  # errors; relax -Werror so modern toolchains build (same as the SITL build).
  if grep -q -- "-Werror" Makefile; then
    sed -i "s/ -Werror / -Wno-error /g" Makefile
  fi

  # Speed up CLI dump loading: SITL polls its TCP UART every 0.5 s by default, so
  # match the 100 Hz serial task (10 ms). Patch whichever file still carries it.
  local slow
  for slow in $(grep -rl "dyad_setUpdateTimeout(0.5f)" src 2>/dev/null || true); do
    sed -i "s/dyad_setUpdateTimeout(0.5f)/dyad_setUpdateTimeout(0.01f)/" "$slow"
    echo ">> $tag: throttle patch applied to $slow"
  done

  # Re-enable the VTX config table (table only, no device driver) so vtxtable
  # powervalues/powerlabels are visible — exactly the data an inspector needs.
  # target.h moved with the platform refactor; prefer the new path, fall back.
  local sitl_target="" cand
  for cand in \
      src/platform/SIMULATOR/target/SITL/target.h \
      src/main/target/SITL/target.h; do
    if [ -f "$cand" ]; then sitl_target="$cand"; break; fi
  done
  if [ -n "$sitl_target" ] && ! grep -q "DRONE_CHECK_VTX" "$sitl_target"; then
    echo ">> $tag: enabling VTX config table in $sitl_target"
    cat >> "$sitl_target" <<'VTXEOF'

// drone-check: expose the VTX config table (config data only, no device
// hardware). Lets the Configurator show vtxtable powervalues/powerlabels.
#define DRONE_CHECK_VTX
#define USE_VTX_COMMON
#define USE_VTX_CONTROL
#define USE_VTX_TABLE
VTXEOF
  fi

  # The bf-configd read-only guard (the one thing that distinguishes the binary
  # from plain SITL): refuse all MSP writes under -DCONFIGD.
  echo ">> $tag: applying read-only guard"
  apply_readonly_guard src/main/msp/msp.c || return 1

  # Optional extra patches for this family.
  local patch_dir="$PATCHES_ROOT/betaflight-$family"
  if [ -d "$patch_dir" ] && ls "$patch_dir"/*.patch >/dev/null 2>&1; then
    local p
    for p in "$patch_dir"/*.patch; do
      if git apply --check "$p" >/dev/null 2>&1; then
        echo ">> $tag: applying $(basename "$p")"; git apply "$p"
      elif git apply --reverse --check "$p" >/dev/null 2>&1; then
        echo ">> $tag: $(basename "$p") already applied"
      else
        echo "!! $tag: patch does not apply cleanly: $(basename "$p")" >&2
        return 1
      fi
    done
  fi

  # Newer firmware (2024.x+) needs the board-config submodule hydrated first.
  if grep -rq "^configs:" Makefile mk 2>/dev/null && [ ! -d src/config/configs ]; then
    echo ">> $tag: hydrating board configs (make configs)"
    make configs
  fi

  # The build validates the ARM toolchain even for the host target.
  if grep -rq "arm_sdk_install" Makefile mk make 2>/dev/null; then
    make arm_sdk_install >/dev/null 2>&1 || make arm_sdk_install
  fi

  # Build the SITL host target with -DCONFIGD (the read-only guard) and static
  # linking (portable across glibc versions, like the SITL bundles). OPTIONS
  # tokens become -D defines via the Makefile, so no Makefile surgery is needed.
  echo ">> $tag: building bf-configd (TARGET=SITL OPTIONS='SITL_STATIC CONFIGD')"
  make TARGET=SITL OPTIONS="SITL_STATIC CONFIGD"

  if [ ! -f obj/main/betaflight_SITL.elf ]; then
    echo "!! $tag: build produced no binary" >&2
    return 1
  fi
  mkdir -p "$out_dir"
  cp obj/main/betaflight_SITL.elf "$elf"
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
