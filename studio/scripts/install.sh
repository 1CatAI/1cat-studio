#!/usr/bin/env bash
# SPDX-License-Identifier: LicenseRef-1Cat-Community-1.0
set -euo pipefail
ONECAT_BUNDLE=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
ONECAT_PREFIX="${XDG_DATA_HOME:-$HOME/.local/share}/onecat-studio-app"
ONECAT_DATA="${XDG_DATA_HOME:-$HOME/.local/share}/onecat-studio"
ONECAT_SERVICE=1
while (( $# )); do
  case "$1" in
    --prefix) ONECAT_PREFIX=$2; shift 2;;
    --state-dir) ONECAT_DATA=$2; shift 2;;
    --no-service) ONECAT_SERVICE=0; shift;;
    *) echo 'Usage: scripts/install.sh [--prefix PATH] [--state-dir PATH] [--no-service]' >&2; exit 2;;
  esac
done
if [[ $(uname -s) != Linux || $(uname -m) != x86_64 ]]; then
  echo 'This bundle requires Linux x86_64.' >&2; exit 2
fi
ONECAT_BASE_PYTHON=$(find "$ONECAT_BUNDLE/python" -path '*/bin/python3.12' -type f -print -quit)
if [[ -z "$ONECAT_BASE_PYTHON" ]]; then echo 'Offline Python is missing.' >&2; exit 2; fi
exec "$ONECAT_BASE_PYTHON" "$ONECAT_BUNDLE/scripts/install.py" "$ONECAT_BUNDLE" "$ONECAT_PREFIX" "$ONECAT_DATA" "$ONECAT_SERVICE"
