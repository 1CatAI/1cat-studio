# SPDX-License-Identifier: LicenseRef-1Cat-Community-1.0
"""Keep host tuning out of managed inference; retain explicit runtime settings."""

import os


def runtime_environment(record: dict) -> dict[str, str]:
    inherited = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith("VLLM_")
        and key
        not in {
            "PYTHONPATH",
            "ONECAT_VLLM_EXTENSION_DIR",
            "FLASH_QLA_SM70_PREBUILT_EXTENSION_PATH",
        }
    }
    return {**inherited, **record.get("environment", {})}
