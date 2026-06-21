#!/usr/bin/env bash
#
# Guided installer for drone-check (Linux / macOS).
#
# Sets up drone-check in a local virtual environment and, optionally, the
# "View in Configurator" (SITL) feature. On Linux the SITL binaries run
# NATIVELY (no WSL); macOS can capture/evaluate but cannot run the Linux SITL
# binaries.
#
# Run it from the repository root:
#     bash scripts/install.sh
#
# The "View in Configurator" feature has two interchangeable backends:
# bf-configd (lighter, read-only; the default) and SITL (full FC). This installer
# provisions their pre-built binaries — preferring a local bundle, otherwise
# downloading the bundle from the latest GitHub release (no toolchain needed).
#
# Options (for unattended runs):
#   --dev                 also install development dependencies (pytest)
#   --sitl                set up the Configurator feature without asking
#   --no-sitl             skip the Configurator feature without asking
#   --sitl-bundle <path>  install pre-built SITL binaries from this bundle
#   --bfcd-bundle <path>  install pre-built bf-configd binaries from this bundle
#
set -euo pipefail

GH_REPO="PSi86/drone-check"     # repo that hosts the binary bundle assets
GH_ASSET_TAG="binaries"          # dedicated release tag the bundles are attached to

DEV=0; WANT_SITL=""; BUNDLE=""; BFCD_BUNDLE=""
while [ "$#" -gt 0 ]; do
  case "$1" in
    --dev) DEV=1 ;;
    --sitl) WANT_SITL=1 ;;
    --no-sitl) WANT_SITL=0 ;;
    --sitl-bundle) shift; BUNDLE="${1:-}" ;;
    --bfcd-bundle) shift; BFCD_BUNDLE="${1:-}" ;;
    *) echo "unknown option: $1" >&2; exit 2 ;;
  esac
  shift
done

if [ -t 1 ]; then
  C='\033[36m'; G='\033[32m'; Y='\033[33m'; R='\033[31m'; Z='\033[0m'
else
  C=''; G=''; Y=''; R=''; Z=''
fi
info() { printf "${C}==> %s${Z}\n" "$1"; }
ok()   { printf "${G}    %s${Z}\n" "$1"; }
warn() { printf "${Y}    %s${Z}\n" "$1"; }
err()  { printf "${R}!!  %s${Z}\n" "$1"; }

# Repository root = parent of this script's dir.
REPO="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO"
info "drone-check installer - repository: $REPO"

# --- 1. Python -------------------------------------------------------------
info "Checking Python (3.10+ required)..."
PY=""
for cand in python3 python; do
  command -v "$cand" >/dev/null 2>&1 || continue
  v="$("$cand" -c 'import sys;print("%d %d"%sys.version_info[:2])' 2>/dev/null || echo "0 0")"
  set -- $v
  if [ "${1:-0}" -gt 3 ] || { [ "${1:-0}" -eq 3 ] && [ "${2:-0}" -ge 10 ]; }; then PY="$cand"; break; fi
done
if [ -z "$PY" ]; then
  err "Python 3.10+ not found."
  warn "Install it, e.g.:  sudo apt install python3   (Debian/Ubuntu)"
  warn "                   sudo dnf install python3   (Fedora)"
  warn "                   brew install python        (macOS)"
  exit 1
fi
ok "Found $("$PY" --version) ($PY)"

# venv + ensurepip must be present (Debian/Ubuntu split these into python3-venv).
if ! "$PY" -c 'import ensurepip, venv' >/dev/null 2>&1; then
  err "Python's venv/pip support is missing."
  warn "Install it, then re-run this installer:"
  warn "    sudo apt install -y python3-venv python3-pip   (Debian/Ubuntu)"
  exit 1
fi

# --- 2. Virtual environment + package -------------------------------------
VENV_PY="$REPO/.venv/bin/python"
if [ ! -x "$VENV_PY" ]; then
  info "Creating virtual environment (.venv)..."
  "$PY" -m venv .venv
fi
ok "Virtual environment ready (.venv)"

info "Installing drone-check and its dependencies (this may take a minute)..."
"$VENV_PY" -m pip install --upgrade pip --quiet
if [ "$DEV" -eq 1 ]; then spec=".[dev]"; else spec="."; fi
"$VENV_PY" -m pip install -e "$spec"
ok "drone-check installed. Run it as:  ./.venv/bin/drone-check <command>"

# Serial access on Linux needs the user in the 'dialout' group.
if [ "$(uname -s)" = "Linux" ] && ! id -nG 2>/dev/null | tr ' ' '\n' | grep -qx dialout; then
  warn "For USB serial access, add yourself to the 'dialout' group (then re-login):"
  warn "    sudo usermod -aG dialout \"$USER\""
fi

# --- 3. Optional: "View in Configurator" backends (bf-configd + SITL) -------
if [ "$(uname -s)" != "Linux" ]; then
  info "'View in Configurator' needs Linux binaries and is not available on this OS."
  echo
  ok "Done. Start drone-check with:  ./.venv/bin/drone-check serve"
  ok "then open http://127.0.0.1:8000"
  exit 0
fi

if [ -z "$WANT_SITL" ]; then
  echo
  echo "The 'View in Configurator' feature opens a stored capture in the real"
  echo "Betaflight Configurator. It uses bf-configd (lighter, read-only; the"
  echo "default) or SITL — both run natively on Linux. Capture and rule-checking"
  echo "work fully WITHOUT it."
  printf "Set up the Configurator feature now? [Y/n] "
  read -r ans || ans=""
  case "$ans" in n|N|no|nein) WANT_SITL=0 ;; *) WANT_SITL=1 ;; esac
fi

if [ "$WANT_SITL" -ne 1 ]; then
  info "Skipping the Configurator feature. It stays hidden in the UI until binaries are present."
  echo
  ok "Done. Start drone-check with:  ./.venv/bin/drone-check serve"
  ok "then open http://127.0.0.1:8000"
  exit 0
fi

# Download the *-bundle release asset whose name contains $1 to $2, from the
# dedicated binaries release. Prefer the GitHub CLI (works for the private repo,
# using the user's existing auth); fall back to curl if the repo is public.
fetch_asset() {
  local pat="$1" out="$2" url
  info "Fetching $pat from the '$GH_ASSET_TAG' release ..."
  if command -v gh >/dev/null 2>&1 \
     && gh release download "$GH_ASSET_TAG" --repo "$GH_REPO" \
          --pattern "${pat}*.tar.gz" --output "$out" --clobber >/dev/null 2>&1; then
    return 0
  fi
  command -v curl >/dev/null 2>&1 || return 1
  url="$(curl -fsSL "https://api.github.com/repos/$GH_REPO/releases/tags/$GH_ASSET_TAG" 2>/dev/null \
    | grep -oE 'https://[^"]*'"$pat"'[^"]*\.tar\.gz' | head -1)"
  [ -n "$url" ] || return 1
  curl -fsSL "$url" -o "$out"
}

# Provision one backend: prefer an explicit/local bundle, else download it from
# the latest release. $1 = name (bfcd|sitl), $2 = explicit bundle, $3 = build hint.
provision() {
  local name="$1" bundle="$2" build="$3" label
  [ "$name" = "bfcd" ] && label="bf-configd" || label="SITL"
  if [ -z "$bundle" ]; then
    bundle="$(ls -1 "$REPO/$name-bundle"*.tar.gz 2>/dev/null | head -1 || true)"
  fi
  if [ -z "$bundle" ]; then
    local tmp; tmp="$(mktemp 2>/dev/null || echo "/tmp/$name-bundle.tar.gz")"
    if fetch_asset "$name-bundle" "$tmp"; then bundle="$tmp"; fi
  fi
  if [ -n "$bundle" ] && [ -f "$bundle" ]; then
    info "Installing pre-built $label binaries ..."
    if "$VENV_PY" -m drone_check "$name" install "$bundle"; then
      ok "$label binaries installed."
    else
      warn "$label bundle install failed (see above)."
    fi
  else
    warn "No $label bundle found locally or on the latest release."
    warn "  install one you were given:  ./.venv/bin/drone-check $name install <bundle.tar.gz>"
    warn "  or build from source:        $build"
  fi
}

# bf-configd is the default backend, so provision it first; SITL is the alternative.
provision bfcd "$BFCD_BUNDLE" "sudo apt install -y build-essential ruby git && bash scripts/build_bfcd.sh 4.5.3 4.4.0 2025.12.2"
provision sitl "$BUNDLE"      "sudo apt install -y build-essential ruby git && bash scripts/build_sitl.sh 4.5.3 4.4.0 2025.12.2"

echo
info "Cached bf-configd families:"; "$VENV_PY" -m drone_check bfcd list || true
info "Cached SITL versions:";       "$VENV_PY" -m drone_check sitl list || true
echo
ok "Done. Start drone-check with:  ./.venv/bin/drone-check serve"
ok "then open http://127.0.0.1:8000"
