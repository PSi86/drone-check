#!/usr/bin/env bash
#
# Bundle the locally cached, pre-built Betaflight SITL binaries into a single
# portable archive so they can be installed on another machine with
#   drone-check sitl install <bundle>
# (or by extracting into the SITL cache dir by hand).
#
# The binaries are built statically (see build_sitl.sh) and depend only on the
# Linux kernel, so the bundle runs under any WSL distro without a toolchain.
#
# Runs inside WSL.
#
# Usage:   bash package_sitl.sh <output.tar.gz> [<version> ...]
#          (no versions -> bundle every cached version)
# Example: bash package_sitl.sh /mnt/c/Users/me/sitl-bundle.tar.gz
#
set -euo pipefail

CACHE_DIR="${DRONE_CHECK_SITL_CACHE:-$HOME/.cache/drone-check/sitl}"

if [ "$#" -lt 1 ]; then
  echo "usage: bash package_sitl.sh <output.tar.gz> [<version> ...]" >&2
  exit 2
fi
out="$1"; shift

if [ ! -d "$CACHE_DIR" ]; then
  echo "!! no SITL cache at $CACHE_DIR — build some first (build_sitl.sh)" >&2
  exit 1
fi

# Versions: the ones given, else every cached version that has an elf.
versions=("$@")
if [ "${#versions[@]}" -eq 0 ]; then
  for d in "$CACHE_DIR"/*/; do
    [ -f "$d/betaflight_SITL.elf" ] && versions+=("$(basename "$d")")
  done
fi
if [ "${#versions[@]}" -eq 0 ]; then
  echo "!! no built SITL binaries found in $CACHE_DIR" >&2
  exit 1
fi

stage="$(mktemp -d)"
trap 'rm -rf "$stage"' EXIT

echo "drone-check SITL bundle" > "$stage/bundle-info.txt"
echo "created: $(date -u '+%Y-%m-%dT%H:%M:%SZ')" >> "$stage/bundle-info.txt"
echo "versions:" >> "$stage/bundle-info.txt"

missing=0
for v in "${versions[@]}"; do
  elf="$CACHE_DIR/$v/betaflight_SITL.elf"
  if [ ! -f "$elf" ]; then
    echo "!! $v: not built (no $elf) — skipping" >&2
    missing=1
    continue
  fi
  mkdir -p "$stage/$v"
  cp "$elf" "$stage/$v/betaflight_SITL.elf"
  # Note whether it is portable (statically linked) for the info file.
  if file "$elf" | grep -q "statically linked"; then link="static"; else link="dynamic"; fi
  printf "  %-12s %8d bytes  %s\n" "$v" "$(stat -c%s "$elf")" "$link" >> "$stage/bundle-info.txt"
done

cd "$stage"
# Checksums over the elf files, verifiable on install with `sha256sum -c`.
find . -name 'betaflight_SITL.elf' | sort | xargs sha256sum > SHA256SUMS

mkdir -p "$(dirname "$out")"
tar -czf "$out" .
echo
echo ">> wrote $(du -h "$out" | cut -f1) bundle: $out"
cat bundle-info.txt
[ "$missing" -eq 0 ] || echo "(some requested versions were missing; see warnings above)"
