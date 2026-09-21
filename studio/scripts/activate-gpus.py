#!/usr/bin/env python3
# SPDX-License-Identifier: LicenseRef-1Cat-Community-1.0
"""Activate this machine: bind every delivered preset to the GPUs present here.

Run by the installer right after GPU authorization, and repeatable from Studio
Settings. A preset that already matches this host is left untouched.
"""

import argparse
import json
import os
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1] / "studio" / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from onecat import activation  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="Report without writing")
    parser.add_argument("--quiet", action="store_true", help="Only print a one-line summary")
    args = parser.parse_args()
    if not os.environ.get("ONECAT_STUDIO_HOME"):
        print("ONECAT_STUDIO_HOME is not set; run this through the installer", file=sys.stderr)
        return 1
    apply = not args.dry_run
    report = {
        "gpus": activation.rebind_profiles(apply=apply),
        "runtimes": activation.rebind_runtimes(apply=apply),
    }
    if args.quiet:
        print(
            "Activated: %d GPU binding(s) and %d runtime binding(s) re-bound; %d and %d already matched"
            % (
                report["gpus"]["rebound"],
                report["runtimes"]["rebound"],
                report["gpus"]["kept"],
                report["runtimes"]["kept"],
            )
        )
    else:
        print(json.dumps(report, ensure_ascii=False, indent=1))
    for warning in report["gpus"]["warnings"] + report["runtimes"]["warnings"]:
        print("warning: " + warning, file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
