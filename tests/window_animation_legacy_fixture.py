"""Explicit historical fixture construction, never production migration."""

from clip_composite_legacy_fixture import strip_clip_composite_field


def strip_window_animation_field(payload):
    """Remove schema-30 fields only when constructing a pre-30 test project."""
    assert type(payload['version']) is int and 1 <= payload['version'] < 30
    strip_clip_composite_field(payload)
    for clip in payload.get('clips', []):
        clip.get('grade', {}).get('qualifier', {}).pop('window_keyframes', None)
    return payload
