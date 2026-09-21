# SPDX-License-Identifier: LicenseRef-1Cat-Community-1.0
from __future__ import annotations

import os
import signal
import sys
import traceback

import psutil

from .config import initialize_paths
from .jobs import Cancelled, Job


def run(id: str):
    initialize_paths()
    from . import db

    # Survive the manager exiting between Popen and recording the child's PID.
    db.patch("jobs", id, {"pid": os.getpid(), "process_created": psutil.Process().create_time()})
    job = Job(id)

    def interrupted(signum, frame):
        raise Cancelled("Operation cancelled")

    signal.signal(signal.SIGTERM, interrupted)
    try:
        job.update("starting")
        kind = job.record["kind"]
        if kind == "creative_prepare_generate":
            from .creative.generations import execute

            result = execute(job)
        elif kind == "creative_generate":
            from .creative.runs import execute

            result = execute(job)
        elif kind in {"creative_load", "creative_stop"}:
            from .creative.services import lifecycle

            result = lifecycle(job)
        elif kind == "creative_download":
            from .creative.components import download

            result = download(job)
        elif kind == "configure_gpu_helper":
            from .gpu_setup import configure

            result = configure(job)
        elif kind == "download_model":
            from .models import download

            result = download(job)
        elif kind == "import_runtime":
            from .runtimes import inspect_runtime

            result = {"runtime_id": inspect_runtime(job.payload)["id"]}
        elif kind == "install_runtime":
            from .runtimes import install

            result = install(job)
        elif kind == "import_offline":
            from .runtimes import import_offline

            result = import_offline(job)
        elif kind == "export_runtime":
            from .runtimes import export_runtime

            result = export_runtime(job)
        elif kind == "apply_gpu_settings":
            from .gpu_control import execute

            result = execute(job)
        elif kind in {"start_model", "stop_model", "apply_hardware", "benchmark", "adopt_service"}:
            from . import db, engine

            with engine.maintenance(job):
                if kind == "adopt_service":
                    from .adopt import adopt_service

                    job.update("inspecting_service")
                    result = adopt_service(job.payload["unit"])
                elif kind == "start_model":
                    result = engine.start(job, job.payload["profile_id"])
                elif kind == "stop_model":
                    engine.stop(job, force=job.payload.get("force", False))
                    result = {"state": "stopped"}
                elif kind == "apply_hardware":
                    engine.drain(job)
                    state = engine.private_state()
                    if job.payload.get("profile_id", state.get("profile_id")) != state.get(
                        "profile_id"
                    ):
                        raise ValueError("The active model changed; select the power mode again")
                    uuids = state.get("profile", {}).get("gpu_uuids", [])
                    from .gpu_control import apply_settings

                    result = apply_settings({u: job.payload["setting"] for u in uuids}, persist=True)
                else:
                    from .efficiency import benchmark

                    result = benchmark(job)
        else:
            raise ValueError(f"Unknown job kind: {kind}")
        job.finish(result)
    except BaseException as error:
        traceback.print_exc()
        job.fail(error)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(run(sys.argv[1]))
