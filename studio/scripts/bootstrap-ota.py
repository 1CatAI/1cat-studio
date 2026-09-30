#!/usr/bin/env python3
# SPDX-License-Identifier: LicenseRef-1Cat-Community-1.0
"""One-time migration of an existing Studio service to signed, managed OTA.

Run with the existing Studio Python and a trusted copy of this source tree.
The source checkout is retained; subsequent updates use Settings > Studio updates.
"""

import argparse
import json
import os
import sys
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state", type=Path, required=True)
    parser.add_argument("--prefix", type=Path, required=True)
    parser.add_argument("--channel", required=True)
    parser.add_argument("--service", default="onecat-studio.service")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8888)
    parser.add_argument(
        "--install", action="store_true", help="Explicitly switch to the signed release"
    )
    args = parser.parse_args()
    os.environ.update(
        ONECAT_STUDIO_HOME=str(args.state.expanduser().resolve()),
        ONECAT_STUDIO_PREFIX=str(args.prefix.expanduser().resolve()),
        ONECAT_UPDATE_CHANNEL=args.channel,
        ONECAT_STUDIO_SERVICE=args.service,
        ONECAT_STUDIO_LISTEN_HOST=args.host,
        ONECAT_STUDIO_LISTEN_PORT=str(args.port),
    )
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
    from onecat import updates

    release = updates.fetch(force=True)
    print(
        json.dumps(
            {
                "version": release["version"],
                "sha256": release["sha256"],
                "signed": True,
                "state": str(args.state),
            },
            ensure_ascii=False,
        )
    )
    if args.install:
        print(json.dumps(updates.start(release, port=args.port, switch_to_release=True)))


if __name__ == "__main__":
    main()
