#!/usr/bin/env bash
# SPDX-License-Identifier: LicenseRef-1Cat-Community-1.0
set -euo pipefail
ONECAT_SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
# UUID arguments remain compatible; no arguments detects this machine automatically.
exec /usr/bin/python3 -I "$ONECAT_SCRIPT_DIR/configure-gpu-helper.py" --auto "$@"
