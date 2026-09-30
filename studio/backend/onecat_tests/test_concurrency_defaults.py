# SPDX-License-Identifier: LicenseRef-1Cat-Community-1.0
import json
import re
from pathlib import Path

from onecat.schemas import DEFAULT_MAX_NUM_SEQS, Profile

ROOT = Path(__file__).resolve().parents[2]


def _launch_seqs(node):
    if isinstance(node, dict):
        if "max_num_seqs" in node:
            yield node["max_num_seqs"]
        for value in node.values():
            yield from _launch_seqs(value)
    elif isinstance(node, list):
        for value in node:
            yield from _launch_seqs(value)


def test_new_profiles_admit_concurrent_sequences():
    assert DEFAULT_MAX_NUM_SEQS == 4
    assert Profile.model_fields["max_num_seqs"].default == DEFAULT_MAX_NUM_SEQS


def test_verified_catalog_presets_do_not_serialize_agents():
    catalog = json.loads((ROOT / "backend/onecat/data/verified-models-v1.json").read_text())
    seqs = list(_launch_seqs(catalog))
    assert seqs
    assert min(seqs) >= DEFAULT_MAX_NUM_SEQS


def test_frontend_new_profile_matches_backend_default():
    page = (ROOT / "frontend/src/onecat/models-page.tsx").read_text()
    assert re.search(rf"max_num_seqs: {DEFAULT_MAX_NUM_SEQS},\n", page)
