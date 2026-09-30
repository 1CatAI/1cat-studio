# SPDX-License-Identifier: LicenseRef-1Cat-Community-1.0
"""Engine boundary shared by task creation and capability discovery."""

from . import pi_runtime, runtime


class CodexEngineAdapter:
    name = "codex"
    version = runtime.VERSION

    @staticmethod
    def info():
        return runtime.info()

    @staticmethod
    def create_run(record, project, prompt):
        from .tasks import Run

        return Run(record, project, prompt)


class PiEngineAdapter:
    name = "pi"
    version = pi_runtime.VERSION

    @staticmethod
    def info():
        return pi_runtime.info()

    @staticmethod
    def create_run(record, project, prompt):
        from .tasks import PiRun

        return PiRun(record, project, prompt)


def adapter(name="codex"):
    if name == "codex":
        return CodexEngineAdapter
    if name == "pi":
        return PiEngineAdapter
    raise ValueError("Unsupported Agent engine")
