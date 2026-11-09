"""Explicit historical fixture construction, never production migration."""

from clip_motion_legacy_fixture import strip_clip_motion_field


def strip_clip_composite_field(payload):
    """Remove schema-31 fields only when constructing a pre-31 test project."""
    assert type(payload['version']) is int and 1 <= payload['version'] < 31
    strip_clip_motion_field(payload)
    for clip in payload.get('clips', []):
        clip.pop('composite', None)
    return payload
