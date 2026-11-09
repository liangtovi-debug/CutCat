"""Explicit pre-36 test payload construction, never production migration."""

from track_edit_locks_legacy_fixture import strip_track_edit_locks_field

def strip_clip_groups_field(payload):
    assert type(payload['version']) is int and 1 <= payload['version'] < 36
    strip_track_edit_locks_field(payload)
    payload.pop('clip_groups', None)
    return payload
