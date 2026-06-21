#!/usr/bin/env bash
#
# Bundle the locally cached, pre-built bf-configd backend binaries into a single
# portable archive so they can be installed on another machine with
#   drone-check bfcd install <bundle>
# (or by extracting into the bf-configd cache dir by hand).
#
# The binaries are built statically (see build_bfcd.sh) and depend only on the
# Linux kernel, so the bundle runs under any WSL distro without a toolchain.
# They are read-only by construction (every MSP write is refused).
#
# Runs inside the Linux environment that hosts the binaries (WSL or native).
#
# Usage:   bash package_bfcd.sh <output.tar.gz> [<family> ...]
#          (no families -> bundle every cached family)
# Example: bash package_bfcd.sh /mnt/c/Users/me/bfcd-bundle.tar.gz 4.5 2025.12
#
set -euo pipefail

CACHE_DIR="${DRONE_CHECK_BFCD_CACHE:-$HOME/.cache/drone-check/bfcd}"

if [ "$#" -lt 1 ]; then
  echo "usage: bash package_bfcd.sh <output.tar.gz> [<family> ...]" >&2
  exit 2
fi
out="$1"; shift

if [ ! -d "$CACHE_DIR" ]; then
  echo "!! no bf-configd cache at $CACHE_DIR — build some first (build_bfcd.sh)" >&2
  exit 1
fi

# Families: the ones given, else every cached family that has an elf.
families=("$@")
if [ "${#families[@]}" -eq 0 ]; then
  for d in "$CACHE_DIR"/*/; do
    [ -f "$d/bf-configd.elf" ] && families+=("$(basename "$d")")
  done
fi
if [ "${#families[@]}" -eq 0 ]; then
  echo "!! no built bf-configd binaries found in $CACHE_DIR" >&2
  exit 1
fi

stage="$(mktemp -d)"
trap 'rm -rf "$stage"' EXIT

echo "drone-check bf-configd bundle" > "$stage/bundle-info.txt"
echo "created: $(date -u '+%Y-%m-%dT%H:%M:%SZ')" >> "$stage/bundle-info.txt"
echo "families:" >> "$stage/bundle-info.txt"

missing=0
for f in "${families[@]}"; do
  elf="$CACHE_DIR/$f/bf-configd.elf"
  if [ ! -f "$elf" ]; then
    echo "!! $f: not built (no $elf) — skipping" >&2
    missing=1
    continue
  fi
  mkdir -p "$stage/$f"
  cp "$elf" "$stage/$f/bf-configd.elf"
  # Note whether it is portable (statically linked) for the info file.
  if file "$elf" | grep -q "statically linked"; then link="static"; else link="dynamic"; fi
  printf "  %-12s %8d bytes  %s\n" "$f" "$(stat -c%s "$elf")" "$link" >> "$stage/bundle-info.txt"
done

cd "$stage"
# Checksums over the elf files, verifiable on install with `sha256sum -c`.
find . -name 'bf-configd.elf' | sort | xargs sha256sum > SHA256SUMS

mkdir -p "$(dirname "$out")"
tar -czf "$out" .
echo
echo ">> wrote $(du -h "$out" | cut -f1) bundle: $out"
cat bundle-info.txt
[ "$missing" -eq 0 ] || echo "(some requested families were missing; see warnings above)"
