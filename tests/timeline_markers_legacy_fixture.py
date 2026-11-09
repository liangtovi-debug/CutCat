"""Explicit pre-39 test construction, never a production migration bypass."""

from source_audio_legacy_fixture import strip_source_audio_state_fields


def strip_timeline_markers_field(payload):
    assert type(payload['version']) is int and 1 <= payload['version'] < 39
    strip_source_audio_state_fields(payload)
    payload.pop('timeline_markers', None)
    return payload
