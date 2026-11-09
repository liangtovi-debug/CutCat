"""Explicit Log metadata is separate from default strict SDR ingress."""
from dataclasses import replace
import numpy as np
import pytest
from lumen.color_source import (source_color_from_stream, validate_input_assignment,
                                input_encoding_status, APPLE_LOG_ENCODING)
from lumen.color import normalize_input, apply_grade
from lumen.model import Media, Grade, Clip, Project, Editor
from lumen.grade_transfer import copy_grade, paste_grade


def log_stream(**changes):
    return dict(pix_fmt='yuv444p10le', color_space='bt2020nc',
                color_transfer='unknown', color_primaries='bt2020', color_range='tv') | changes


@pytest.mark.parametrize('pixel_format', ['yuv444p10le', 'yuv422p10le', 'yuv420p10le'])
@pytest.mark.parametrize('range_tag', ['pc', 'tv'])
def test_explicit_yuv_profile_without_default_relaxation(pixel_format, range_tag):
    stream = log_stream(pix_fmt=pixel_format, color_range=range_tag)
    with pytest.raises(ValueError):
        source_color_from_stream(stream)
    source = source_color_from_stream(stream, APPLE_LOG_ENCODING)
    assert source.matrix == '2020_ncl'
    assert source.range == ('full' if range_tag == 'pc' else 'limited')
    validate_input_assignment(source, APPLE_LOG_ENCODING)
    with pytest.raises(ValueError, match='keep its Apple Log'):
        validate_input_assignment(source, 'rec709')


@pytest.mark.parametrize('pixel_format', ['rgb48le', 'rgb48be', 'gbrp16le', 'gbrp16be'])
def test_rgb16_explicit_full_profile(pixel_format):
    source = source_color_from_stream(log_stream(pix_fmt=pixel_format, color_space='gbr',
                                      color_range='pc'), APPLE_LOG_ENCODING)
    assert source.is_rgb and source.matrix == 'gbr' and source.range == 'full'


@pytest.mark.parametrize('changes', [
    {'pix_fmt': 'yuv420p'}, {'pix_fmt': 'gbrpf32le'}, {'pix_fmt': 'rgba64le'},
    {'pix_fmt': 'yuv444p12le'}, {'pix_fmt': 'p010le'}, {'color_space': 'unknown'},
    {'color_space': 'bt709'}, {'color_space': 'bt2020c'}, {'color_range': 'unknown'},
    {'color_range': 'invalid'}, {'color_transfer': 'bt709'}, {'color_transfer': 'linear'},
    {'color_transfer': 'iec61966-2-1'}, {'color_transfer': 'smpte2084'},
    {'color_transfer': 'arib-std-b67'}, {'color_transfer': 'log100'},
    {'color_transfer': 'com.apple.apple-wide-gamut.apple-log'},
    {'color_transfer': 'com.apple.rec2020.apple-log'},
    {'color_primaries': 'unknown'}, {'color_primaries': 'bt709'}, {'color_primaries': 'smpte432'},
    {'side_data_list': [{'side_data_type': 'Mastering display metadata'}]},
    {'side_data_list': [{'side_data_type': 'DOVI configuration record'}]},
    {'side_data_list': [{'side_data_type': 'HDR Dynamic Metadata SMPTE2094-40'}]},
    {'side_data_list': None}, {'side_data_list': ['invalid']}, {'color_transfer': None},
    {'pix_fmt': 'gbrp16le', 'color_space': 'gbr', 'color_range': 'tv'},
    {'pix_fmt': 'gbrp16le', 'color_space': 'bt2020nc', 'color_range': 'pc'},
])
def test_strict_log_profile_rejects_conflicting_unknown_or_unsupported_metadata(changes):
    with pytest.raises(ValueError):
        source_color_from_stream(log_stream(**changes), APPLE_LOG_ENCODING)


def test_import_marker_required_no_guess_from_tags_or_filename():
    media = Media('m', '/tmp/AppleLog-iPhone.mov', 'Apple Log', 1,
                  color_primaries='bt2020')
    with pytest.raises(ValueError, match='Import this source explicitly'):
        validate_input_assignment(media, APPLE_LOG_ENCODING)
    media.input_encoding = APPLE_LOG_ENCODING
    validate_input_assignment(media, APPLE_LOG_ENCODING)
    assert 'Synthetic decoder validation only' in input_encoding_status(media, APPLE_LOG_ENCODING)
    for changed in [replace(media, color_transfer='bt709'), replace(media, color_primaries='unknown')]:
        with pytest.raises(ValueError):
            validate_input_assignment(changed, APPLE_LOG_ENCODING)


def test_normalization_shared_and_bypass_only_disables_creative_grade():
    rgb = np.array([[.15047645, .48827246, .99999998]], dtype=np.float32)
    expected = normalize_input(rgb, APPLE_LOG_ENCODING)
    for grade, bypass in [(Grade(input_encoding=APPLE_LOG_ENCODING, enabled=False, exposure=1), False),
                          (Grade(input_encoding=APPLE_LOG_ENCODING, exposure=1), True)]:
        np.testing.assert_array_equal(apply_grade(rgb, grade, bypass), expected)
    np.testing.assert_allclose(apply_grade(rgb, Grade(input_encoding=APPLE_LOG_ENCODING, exposure=1)), expected*2)
    assert np.all(expected >= 0) and np.all(expected <= 1)
    np.testing.assert_array_equal(normalize_input(rgb), rgb)


def test_copy_grade_never_copies_log_source_interpretation():
    media = [Media('a', '/tmp/apple', 'Apple', 1, input_encoding=APPLE_LOG_ENCODING,
                   color_primaries='bt2020'), Media('b', '/tmp/sdr', 'SDR', 1)]
    clips = [Clip('a', 'a', 0, 0, 0, 1, grade=Grade(input_encoding=APPLE_LOG_ENCODING, exposure=1)),
             Clip('b', 'b', 0, 1, 0, 1)]
    editor = Editor(Project(media=media, clips=clips))
    assert paste_grade(editor, 'b', copy_grade(editor.project, 'a'))
    assert editor.project.clips[1].grade.input_encoding == 'rec709'
    editor.project.clips[1].grade.exposure = 2
    assert paste_grade(editor, 'a', copy_grade(editor.project, 'b'))
    assert editor.project.clips[0].grade.input_encoding == APPLE_LOG_ENCODING
    assert editor.project.clips[0].grade.exposure == 2
