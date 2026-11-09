"""Explicit pre-42 test payloads; no production migration bypass."""


def strip_av_links_field(payload):
    assert type(payload['version']) is int and 1 <= payload['version'] < 42
    payload.pop('av_links', None)
    return payload
