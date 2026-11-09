"""Explicit pre-40 test construction, never a production migration bypass."""


from audio_gain_legacy_fixture import strip_audio_gain_keyframes_field

def strip_source_audio_state_fields(payload):
    assert type(payload['version']) is int and 1 <= payload['version'] < 40
    strip_audio_gain_keyframes_field(payload)
    for clip in payload.get('clips', []):
        clip.pop('source_audio_enabled', None)
        clip.pop('audio_mix_anchor', None)
    return payload
