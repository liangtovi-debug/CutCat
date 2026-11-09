"""Explicit historical fixture conversion; never a production migration."""
from vignette_legacy_fixture import strip_vignette_shape_fields

def strip_white_balance_fields(payload):
    """Remove schema-27 fields only when constructing a pre-27 test payload."""
    assert type(payload['version']) is int and 1 <= payload['version'] < 27
    strip_vignette_shape_fields(payload)
    for clip in payload.get('clips', []):
        grade = clip.get('grade', {})
        grade.pop('white_balance_enabled', None)
        grade.pop('white_balance_gains', None)
    return payload
