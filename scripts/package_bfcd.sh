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
# Usage:   bash package_bfcd.sh <output.tar.gz> [<version> ...]
#          (no versions -> bundle every cached version)
# Example: bash package_bfcd.sh /mnt/c/Users/me/bfcd-bundle.tar.gz 4.5.3 2025.12.4
#
set -euo pipefail

CACHE_DIR="${DRONE_CHECK_BFCD_CACHE:-$HOME/.cache/drone-check/bfcd}"

if [ "$#" -lt 1 ]; then
  echo "usage: bash package_bfcd.sh <output.tar.gz> [<version> ...]" >&2
  exit 2
fi
out="$1"; shift

if [ ! -d "$CACHE_DIR" ]; then
  echo "!! no bf-configd cache at $CACHE_DIR — build some first (build_bfcd.sh)" >&2
  exit 1
fi

# Versions: the ones given, else every cached version that has an elf.
versions=("$@")
if [ "${#versions[@]}" -eq 0 ]; then
  for d in "$CACHE_DIR"/*/; do
    [ -f "$d/bf-configd.elf" ] && versions+=("$(basename "$d")")
  done
fi
if [ "${#versions[@]}" -eq 0 ]; then
  echo "!! no built bf-configd binaries found in $CACHE_DIR" >&2
  exit 1
fi

stage="$(mktemp -d)"
trap 'rm -rf "$stage"' EXIT

echo "drone-check bf-configd bundle" > "$stage/bundle-info.txt"
echo "created: $(date -u '+%Y-%m-%dT%H:%M:%SZ')" >> "$stage/bundle-info.txt"
echo "versions:" >> "$stage/bundle-info.txt"

missing=0
for v in "${versions[@]}"; do
  elf="$CACHE_DIR/$v/bf-configd.elf"
  if [ ! -f "$elf" ]; then
    echo "!! $v: not built (no $elf) — skipping" >&2
    missing=1
    continue
  fi
  mkdir -p "$stage/$v"
  cp "$elf" "$stage/$v/bf-configd.elf"
  # Note whether it is portable (statically linked) for the info file.
  if file "$elf" | grep -q "statically linked"; then link="static"; else link="dynamic"; fi
  printf "  %-12s %8d bytes  %s\n" "$v" "$(stat -c%s "$elf")" "$link" >> "$stage/bundle-info.txt"
done

cd "$stage"
# Checksums over the elf files, verifiable on install with `sha256sum -c`.
find . -name 'bf-configd.elf' | sort | xargs sha256sum > SHA256SUMS

mkdir -p "$(dirname "$out")"
tar -czf "$out" .
echo
echo ">> wrote $(du -h "$out" | cut -f1) bundle: $out"
cat bundle-info.txt
[ "$missing" -eq 0 ] || echo "(some requested versions were missing; see warnings above)"
