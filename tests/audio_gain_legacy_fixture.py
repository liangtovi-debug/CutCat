"""Explicit pre-41 fixture construction; never production migration bypass."""


from av_links_legacy_fixture import strip_av_links_field

def strip_audio_gain_keyframes_field(payload):
    assert type(payload['version']) is int and 1 <= payload['version'] < 41
    strip_av_links_field(payload)
    for clip in payload.get('clips', []):
        clip.pop('audio_gain_keyframes', None)
    return payload
