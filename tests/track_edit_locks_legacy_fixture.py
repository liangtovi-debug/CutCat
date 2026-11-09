"""Explicit pre-37 fixture construction, not a production schema escape hatch."""


from source_ranges_legacy_fixture import strip_source_ranges_field


def strip_track_edit_locks_field(payload):
    assert type(payload['version']) is int and 1 <= payload['version'] < 37
    strip_source_ranges_field(payload)
    payload.pop('track_edit_locks', None)
    return payload
