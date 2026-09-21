#!/usr/bin/env bash
# SPDX-License-Identifier: LicenseRef-1Cat-Community-1.0
# This adds user-namespace permission only to an immutable copy of bubblewrap.
set -euo pipefail
if [[ $(id -u) != 0 ]]; then
  exec pkexec bash "$(readlink -f "$0")"
fi
agent_scripts=$(dirname "$(readlink -f "$0")")
agent_bwrap=""
for candidate in "$agent_scripts/../studio/vendor/codex/0.153.4/codex-resources/bwrap" \
                 "$agent_scripts/../vendor/codex/0.153.4/codex-resources/bwrap" /usr/bin/bwrap; do
  if [[ -x "$candidate" ]]; then agent_bwrap="$candidate"; break; fi
done
test -n "$agent_bwrap"
install -d -m 0755 /usr/local/libexec
install -m 0755 -o root -g root "$agent_bwrap" /usr/local/libexec/onecat-agent-bwrap
if [[ -f /proc/sys/kernel/apparmor_restrict_unprivileged_userns ]] &&
   [[ $(cat /proc/sys/kernel/apparmor_restrict_unprivileged_userns) == 1 ]]; then
  cat > /etc/apparmor.d/onecat-studio-agent <<'PROFILE'
abi <abi/4.0>,
include <tunables/global>
profile onecat-studio-agent /usr/local/libexec/onecat-agent-bwrap flags=(unconfined) {
  userns,
}
PROFILE
  chmod 0644 /etc/apparmor.d/onecat-studio-agent
  apparmor_parser -r /etc/apparmor.d/onecat-studio-agent
fi
echo 'Agent sandbox installed. GPU configuration is unchanged.'
