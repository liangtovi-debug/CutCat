"""Real muxing under a simulated no-edit-list default, not Windows evidence."""
from fractions import Fraction
from pathlib import Path

import pytest

import test_linked_av_split_media as fixture


@pytest.mark.parametrize('offset', [.3, -.1])
@pytest.mark.parametrize('fps,short_audio', [(Fraction(24), False), (Fraction(24000, 1001), True)])
def test_explicit_editlist_preserves_track_offset_when_default_disables_it(tmp_path, monkeypatch, offset, fps, short_audio):
    original = fixture.ff
    mux_commands = []

    def alternate_default(*args, data=None):
        args = list(args)
        if Path(args[-1]).name == 'source.mov':
            mux_commands.append(args[:])
            # Model a different muxer default without changing explicit requests.
            # FFmpeg still performs the actual mux and all real decode checks.
            if '-use_editlist' not in args:
                args[-1:-1] = ['-use_editlist', '0']
        return original(*args, data=data)

    monkeypatch.setattr(fixture, 'ff', alternate_default)
    media, _, _ = fixture.split_source(tmp_path, fps, offset, short_audio=short_audio)
    assert media.audio_offset == offset
    assert len(mux_commands) == 1
    command = mux_commands[0]
    assert command[command.index('-use_editlist') + 1] == '1'
