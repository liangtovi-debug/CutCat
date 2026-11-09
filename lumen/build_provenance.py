"""Strict, path-free source identity embedded by the native build only.

This is build provenance, not a signature or a claim that native tests passed.
Runtime validation needs only the standard library and never invokes Git or
imports developer scripts. Unfrozen source runs cannot claim a built identity.
"""
from __future__ import annotations

import json
from pathlib import Path
import re
import sys

_FIELDS = {'schema', 'commit', 'tree', 'source_sha256', 'tracked_files', 'clean'}
_BUNDLED_PROVENANCE = Path(__file__).resolve().parent / 'assets' / 'build-provenance.json'
_MAX_BYTES = 8192


class BuildProvenanceError(ValueError):
    """Missing or invalid native source identity must fail smoke acceptance."""


def validate_source_provenance(value):
    """Return an owned plain dict containing exactly the version-1 schema."""
    if type(value) is not dict or set(value) != _FIELDS:
        raise BuildProvenanceError('Build provenance requires exactly its documented fields')
    if type(value['schema']) is not int or value['schema'] != 1:
        raise BuildProvenanceError('Unsupported build provenance schema')
    for field, length in (('commit', 40), ('tree', 40), ('source_sha256', 64)):
        if type(value[field]) is not str or not re.fullmatch(r'[0-9a-f]{' + str(length) + '}', value[field]):
            raise BuildProvenanceError(f'Build provenance {field} must be a lowercase hexadecimal identity')
    if type(value['tracked_files']) is not int or value['tracked_files'] <= 0:
        raise BuildProvenanceError('Build provenance tracked_files must be a positive integer')
    if value['clean'] is not True:
        raise BuildProvenanceError('Native builds require clean tracked source provenance')
    return dict(value)


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise BuildProvenanceError('Duplicate build provenance fields are not allowed')
        result[key] = value
    return result


def read_build_provenance(path):
    try:
        with Path(path).open('rb') as stream:
            raw = stream.read(_MAX_BYTES + 1)
    except OSError as error:
        raise BuildProvenanceError('Bundled build provenance is missing or unreadable') from error
    if len(raw) > _MAX_BYTES:
        raise BuildProvenanceError('Bundled build provenance exceeds its size limit')
    try:
        parsed = json.loads(raw.decode('utf-8'), object_pairs_hook=_unique_object)
    except (UnicodeError, json.JSONDecodeError) as error:
        raise BuildProvenanceError('Bundled build provenance is not valid UTF-8 JSON') from error
    return validate_source_provenance(parsed)


def built_source():
    """Frozen apps must expose their bundled identity; source runs report None."""
    if not getattr(sys, 'frozen', False):
        return None
    return read_build_provenance(_BUNDLED_PROVENANCE)
