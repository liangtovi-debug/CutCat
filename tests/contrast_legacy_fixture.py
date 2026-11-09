"""Explicit historical fixture conversion, not production migration."""

from white_balance_legacy_fixture import strip_white_balance_fields

def strip_contrast_pivot_field(payload):
    """Current serialized grades contain a field unavailable before schema 26."""
    assert type(payload['version']) is int and payload['version'] < 26
    strip_white_balance_fields(payload)
    for clip in payload.get('clips', []):
        clip.get('grade', {}).pop('contrast_pivot', None)
    return payload
