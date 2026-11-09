"""Strict, bounded sample automation, scalar inspector and cache identity."""
from copy import deepcopy
from dataclasses import FrozenInstanceError, replace
import math
from pathlib import Path

import pytest

from lumen.audio_gain import (AudioGainKeyframe as Key, MAX_AUDIO_GAIN_SAMPLE,
    MAX_AUDIO_GAIN_EXPRESSION_LENGTH, compile_audio_gain_filters,
    evaluate_audio_gain_offset, shift_audio_gain_keyframes,
    validate_audio_gain_keyframes)
from lumen.audio_analysis import audio_settings_fingerprint
from lumen.model import Clip, Editor, Media, Project
from lumen.render import RenderEngine


@pytest.mark.parametrize('sample', [True, False, 1.0, '1', None, [], {},
    -MAX_AUDIO_GAIN_SAMPLE-1, MAX_AUDIO_GAIN_SAMPLE+1])
def test_key_sample_is_strict_signed_bounded_integer(sample):
    with pytest.raises(ValueError, match='sample'):
        Key(sample)


@pytest.mark.parametrize('offset', [True, False, '1', None, [], {},
    math.nan, math.inf, -math.inf, -96.0001, 24.0001, 10**1000],
    ids=['true', 'false', 'text', 'none', 'list', 'dict', 'nan', 'inf',
         'negative-inf', 'below-range', 'above-range', 'huge-int'])
def test_offset_is_finite_bounded_and_never_silently_limited(offset):
    with pytest.raises(ValueError, match='offset'):
        Key(0, offset)


@pytest.mark.parametrize('mode', ['LINEAR', 'bezier', '', None, 1, True])
def test_outgoing_interpolation_is_explicit(mode):
    with pytest.raises(ValueError, match='interpolation'):
        Key(0, 0, mode)


def test_keys_are_immutable_canonical_and_exact_integer_coordinates():
    key = Key(MAX_AUDIO_GAIN_SAMPLE, -0.0)
    assert type(key.sample) is int and type(key.offset_db) is float
    assert math.copysign(1, key.offset_db) == 1
    with pytest.raises(FrozenInstanceError):
        key.sample = 7
    validate_audio_gain_keyframes([Key(-MAX_AUDIO_GAIN_SAMPLE, -96), key])


@pytest.mark.parametrize('keys', [(), {}, None, [Key(1), Key(1)],
    [Key(2), Key(1)], [Key(i) for i in range(129)], [{'sample': 0}]])
def test_validator_rejects_wrong_container_noncanonical_and_overcomplex(keys):
    with pytest.raises(ValueError):
        validate_audio_gain_keyframes(keys)


def test_hidden_handles_endpoint_holds_and_outgoing_linear_db():
    keys = [Key(-7, -12), Key(3, -2, 'hold'), Key(5, 6), Key(10, -4)]
    assert [evaluate_audio_gain_offset(keys, n) for n in (-20, -7, -2, 0, 3, 4, 5, 10, 99)] == [
        -12, -12, -7, -5, -2, -2, 6, -4, -4]
    assert evaluate_audio_gain_offset([], 0) == 0
    assert evaluate_audio_gain_offset(keys, 7) == 2


def test_shift_preserves_hidden_handles_modes_and_full_precision():
    keys = [Key(-7, -12.123456789012345, 'hold'), Key(100, 4)]
    shifted = shift_audio_gain_keyframes(keys, 1601)
    assert shifted == [replace(key, sample=key.sample - 1601) for key in keys]
    assert shifted is not keys and keys[0].sample == -7
    with pytest.raises(ValueError):
        shift_audio_gain_keyframes([Key(-MAX_AUDIO_GAIN_SAMPLE)], 1)
    assert shift_audio_gain_keyframes([], 10**30) == []


def test_compiler_is_sample_not_audio_frame_based_and_preserves_float_boundary():
    filters = compile_audio_gain_filters([Key(-7, -12.123456789012345), Key(1200, 6, 'hold')], 48000)
    assert filters[0] == filters[-1] == 'aformat=sample_fmts=fltp:channel_layouts=stereo'
    assert filters[1].startswith("aeval=exprs='val(ch)*pow(10,")
    assert 'eval=frame' not in filters[1] and '(n-(-7))/(1207)' in filters[1]
    assert '-12.123456789012344' in filters[1]
    assert compile_audio_gain_filters([], 10**30) == ()
    with pytest.raises(ValueError, match='budget'):
        compile_audio_gain_filters([Key(0)], MAX_AUDIO_GAIN_SAMPLE+1)


def test_maximum_complexity_has_logarithmic_selector_depth_and_bounded_text():
    keys = [Key(-MAX_AUDIO_GAIN_SAMPLE + i*(2*MAX_AUDIO_GAIN_SAMPLE//127),
                -95.12345678901234 + i*.9) for i in range(128)]
    filters = compile_audio_gain_filters(keys, MAX_AUDIO_GAIN_SAMPLE)
    expression = filters[1]
    assert len(expression) < MAX_AUDIO_GAIN_EXPRESSION_LENGTH
    # Each threshold occurs exactly once in the balanced tree, not one giant
    # additive sum evaluated for every sample. Nine (or more) nested branches
    # would regress the documented eight-comparison runtime bound.
    depth = high = 0
    stack = []
    for index, char in enumerate(expression):
        if char == '(':
            is_branch = expression[max(0, index-2):index] == 'if'
            stack.append(is_branch)
            depth += is_branch
            high = max(high, depth)
        elif char == ')':
            depth -= stack.pop()
    assert high == 8 and expression.count('if(') == 128


def sample_project(path='/missing.wav'):
    media = Media('m', str(path), 'source', 4, has_video=False, has_audio=True)
    return Project(media=[media], clips=[Clip('c', 'm', 2, 3, 0, 12)])


@pytest.mark.parametrize('mutation', ['sample', 'offset', 'mode', 'hidden', 'empty', 'algorithm'])
def test_audio_analysis_manual_settings_invalidate_every_envelope_dimension(monkeypatch, mutation):
    import lumen.audio_analysis as analysis
    project = sample_project()
    project.clips[0].audio_gain_keyframes = [Key(7, -2), Key(101, 3)]
    before = audio_settings_fingerprint(project)
    keys = project.clips[0].audio_gain_keyframes
    if mutation == 'sample': keys[0] = replace(keys[0], sample=8)
    elif mutation == 'offset': keys[0] = replace(keys[0], offset_db=-1.99999)
    elif mutation == 'mode': keys[0] = replace(keys[0], interpolation='hold')
    elif mutation == 'hidden': keys.append(Key(999999, 1))
    elif mutation == 'empty': project.clips[0].audio_gain_keyframes = []
    else: monkeypatch.setattr(analysis, 'AUDIO_GAIN_ALGORITHM_ID', 'different-algorithm')
    assert audio_settings_fingerprint(project) != before


def test_preview_cache_changes_for_envelope_and_algorithm_and_reuses_exact_undo(tmp_path, monkeypatch):
    import lumen.audio_gain as gain
    source = tmp_path / 'source.wav'
    source.write_bytes(b'cache identity only; rendering mocked')
    editor = Editor(sample_project(source))
    engine = RenderEngine()
    calls = []
    def render(project, target, **kwargs):
        calls.append(deepcopy(project))
        Path(target).write_bytes(b'preview')
    monkeypatch.setattr(engine, 'render', render)
    try:
        original = engine.preview(editor.project)
        assert engine.preview(editor.project) == original and len(calls) == 1
        editor.update_clip('c', audio_gain_keyframes=[Key(0, -6), Key(9600, 0)])
        automated = engine.preview(editor.project)
        assert automated != original and len(calls) == 2
        assert editor.undo() and engine.preview(editor.project) == original
        assert editor.redo() and engine.preview(editor.project) == automated
        monkeypatch.setattr(gain, 'AUDIO_GAIN_ALGORITHM_ID', 'candidate-v2')
        assert engine.preview(editor.project) not in (original, automated)
        assert len(calls) == 3
    finally:
        engine._cache_dir.cleanup()
