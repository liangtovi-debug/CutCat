"""Explicit test-fixture conversion, never a production migration."""


from window_animation_legacy_fixture import strip_window_animation_field


def strip_vignette_shape_fields(payload):
    """Remove schema-28 fields only when constructing a pre-28 payload."""
    assert type(payload['version']) is int and 1 <= payload['version'] < 28
    strip_window_animation_field(payload)
    for clip in payload.get('clips', []):
        for effect in clip.get('effects', []):
            effect.pop('radius', None)
            effect.pop('feather', None)
    return payload
