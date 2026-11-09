"""Explicit pre-35 test payload construction, never production migration."""


from clip_groups_legacy_fixture import strip_clip_groups_field

def strip_media_bins_fields(payload):
    assert type(payload['version']) is int and 1 <= payload['version'] < 35
    strip_clip_groups_field(payload)
    payload.pop('media_bins', None)
    payload.pop('media_bin_ids', None)
    return payload
