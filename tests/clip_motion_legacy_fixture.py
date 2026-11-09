"""Explicit historical fixture construction, never production migration."""

from media_bins_legacy_fixture import strip_media_bins_fields

def strip_clip_motion_field(payload):
    """Remove motion and newer project controls from a pre-32 test project."""
    assert type(payload['version']) is int and 1 <= payload['version'] < 32
    # Current top-level controls are absent from every historical fixture.
    strip_media_bins_fields(payload)
    payload.pop('master_gain_db', None)
    for clip in payload.get('clips', []):
        clip.pop('motion_keyframes', None)
    return payload
