"""Fail collection before payload-derived test IDs reach OS environment limits."""
from __future__ import annotations

import pytest

MAX_NODE_ID_UTF16_UNITS = 1024


def pytest_collection_modifyitems(items):
    """Require short explicit IDs; never shorten input data or skip a test."""
    oversized = []
    for item in items:
        units = len(item.nodeid.encode('utf-16-le', errors='surrogatepass')) // 2
        if units > MAX_NODE_ID_UTF16_UNITS:
            # Do not echo a potentially enormous or sensitive fixture payload.
            oversized.append(f'{item.nodeid.split("[", 1)[0]} ({units} UTF-16 units)')
    if oversized:
        names = '\n'.join(oversized[:10])
        raise pytest.UsageError(
            'Test node IDs exceed the portable 1024 UTF-16-unit budget. '
            'Add short descriptive ids= to parametrization; keep full payloads and assertions.\n' + names)
