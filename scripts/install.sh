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
# Options (for unattended runs):
#   --dev                 also install development dependencies (pytest)
#   --sitl                set up the SITL feature without asking
#   --no-sitl             skip the SITL feature without asking
#   --sitl-bundle <path>  install pre-built SITL binaries from this bundle
#
set -euo pipefail

DEV=0; WANT_SITL=""; BUNDLE=""
while [ "$#" -gt 0 ]; do
  case "$1" in
    --dev) DEV=1 ;;
    --sitl) WANT_SITL=1 ;;
    --no-sitl) WANT_SITL=0 ;;
    --sitl-bundle) shift; BUNDLE="${1:-}" ;;
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

# --- 3. Optional: SITL ("View in Configurator") ----------------------------
if [ "$(uname -s)" != "Linux" ]; then
  info "SITL ('View in Configurator') needs Linux binaries and is not available on this OS."
  echo
  ok "Done. Start drone-check with:  ./.venv/bin/drone-check serve"
  ok "then open http://127.0.0.1:8000"
  exit 0
fi

if [ -z "$WANT_SITL" ]; then
  echo
  echo "The 'View in Configurator' feature opens a stored capture in the real"
  echo "Betaflight Configurator using a SITL instance (runs natively on Linux)."
  echo "Capture and rule-checking work fully WITHOUT it."
  printf "Set up the Configurator/SITL feature now? [y/N] "
  read -r ans || ans=""
  case "$ans" in y|Y|yes|j|J|ja) WANT_SITL=1 ;; *) WANT_SITL=0 ;; esac
fi

if [ "$WANT_SITL" -ne 1 ]; then
  info "Skipping the SITL feature. It stays hidden in the UI until binaries are present."
  echo
  ok "Done. Start drone-check with:  ./.venv/bin/drone-check serve"
  ok "then open http://127.0.0.1:8000"
  exit 0
fi

# Prefer installing a pre-built bundle (no toolchain needed).
if [ -z "$BUNDLE" ]; then
  BUNDLE="$(ls -1 "$REPO"/sitl-bundle*.tar.gz 2>/dev/null | head -1 || true)"
fi
if [ -n "$BUNDLE" ] && [ -f "$BUNDLE" ]; then
  info "Installing pre-built SITL binaries from $BUNDLE ..."
  "$VENV_PY" -m drone_check sitl install "$BUNDLE"
  ok "SITL binaries installed."
else
  warn "No pre-built SITL bundle found."
  warn "Either install a bundle you were given:"
  warn "    ./.venv/bin/drone-check sitl install <path-to-sitl-bundle.tar.gz>"
  warn "or build the binaries from source (needs a toolchain):"
  warn "    sudo apt install -y build-essential ruby git"
  warn "    bash scripts/build_sitl.sh 4.4.0 2025.12.2"
  warn "See docs/CONFIGURATOR.md. Until then, 'View in Configurator' stays hidden."
fi

echo
info "Cached SITL versions:"
"$VENV_PY" -m drone_check sitl list || true
echo
ok "Done. Start drone-check with:  ./.venv/bin/drone-check serve"
ok "then open http://127.0.0.1:8000"
