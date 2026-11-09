"""Explicit pre-38 test construction, never a production migration bypass."""


from timeline_markers_legacy_fixture import strip_timeline_markers_field

def strip_source_ranges_field(payload):
    assert type(payload['version']) is int and 1 <= payload['version'] < 38
    strip_timeline_markers_field(payload)
    payload.pop('source_ranges', None)
    return payload
