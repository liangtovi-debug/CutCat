"""Numerical transfer and strict metadata contracts for explicit SDR inputs."""
from contrast_legacy_fixture import strip_contrast_pivot_field
from copy import deepcopy
import json
from types import SimpleNamespace

import numpy as np
import pytest

from lumen.color import (apply_grade, linear_to_rec709, linear_to_srgb,
                         normalize_input, srgb_to_linear)
from lumen.color_media import read_source_color, source_rgb_filters
from lumen.color_source import (input_encoding_status, matching_input_encoding,
                                source_color_from_stream, validate_input_assignment)
from lumen.model import Clip, Editor, Grade, Media, Project, VERSION
from lumen.render import RenderError


def stream(**changes):
    return dict(pix_fmt='yuv444p10le', color_space='bt709', color_transfer='bt709',
                color_primaries='bt709', color_range='tv') | changes


def test_srgb_published_values_and_signed_working_extension():
    encoded = np.array([-.5, 0, .04045, .5, 1, 1.5], np.float32)
    expected = [-.21404114048223255, 0, .0031308049535603713,
                .21404114048223255, 1, 2.537155239391517]
    np.testing.assert_allclose(srgb_to_linear(encoded), expected, rtol=3e-7, atol=1e-8)
    np.testing.assert_allclose(linear_to_srgb(srgb_to_linear(encoded)), encoded, atol=2e-7)
    np.testing.assert_allclose(linear_to_srgb(np.array([.0031308, .18], np.float32)),
                               [.040449936, .46135612950044164], atol=1e-7)


@pytest.mark.parametrize('encoding', ['srgb', 'linear_rec709'])
def test_normalization_is_before_creative_controls_and_survives_bypass(encoding):
    rgb = np.array([[.1, .5, .9], [-.2, 1.1, 2]], np.float32)
    normalized = normalize_input(rgb, encoding)
    for grade, bypass in [(Grade(input_encoding=encoding, enabled=False, exposure=2), False),
                          (Grade(input_encoding=encoding, exposure=2), True)]:
        np.testing.assert_array_equal(apply_grade(rgb, grade, bypass), normalized)
    # Creative exposure is applied once, after conversion to working RGB.
    np.testing.assert_allclose(apply_grade(rgb, Grade(input_encoding=encoding, exposure=1)),
                               normalized * 2, atol=1e-7)
    assert normalized.dtype == np.float32 and not np.shares_memory(normalized, rgb)
    assert normalized.min() < 0 and normalized.max() > 1


def test_srgb_reference_mapping_and_neutral_legacy_identity():
    x = np.full((1, 1, 3), .5, np.float32)
    # Independent double-precision evaluation of IEC decode then BT.709 OETF.
    expected = 1.099 * (((.5 + .055) / 1.055) ** 2.4) ** .45 - .099
    np.testing.assert_allclose(apply_grade(x, Grade(input_encoding='srgb')), expected, atol=1e-7)
    assert abs(expected - .4501885294039068) < 1e-12
    np.testing.assert_array_equal(apply_grade(x, Grade()), x)
    np.testing.assert_allclose(normalize_input(x, 'linear_rec709'), linear_to_rec709(x), atol=1e-7)


def test_normalization_rejects_unknown_or_nonfinite_even_when_bypassed():
    for encoding in ['auto', 'slog3', 'pq', '', None]:
        with pytest.raises(ValueError, match='Input encoding'):
            apply_grade(np.ones((1, 3), np.float32), Grade(input_encoding=encoding, enabled=False))
    with pytest.raises(ValueError, match='finite'):
        apply_grade(np.full((1, 3), np.finfo(np.float32).max, np.float32),
                    Grade(input_encoding='srgb', enabled=False))


@pytest.mark.parametrize('encoding', ['rec709', 'srgb', 'linear_rec709'])
def test_model_roundtrip_undo_and_invalid_transaction(tmp_path, encoding):
    media = Media('m', str(tmp_path / 'source.mkv'), 'Source', 1,
                  color_transfer='iec61966-2-1', color_primaries='bt709', color_range='pc')
    editor = Editor(Project(media=[media], clips=[Clip('c', 'm', 0, 0, 0, 1)]))
    original = deepcopy(editor.project)
    editor.update_clip('c', grade=Grade(input_encoding=encoding, exposure=1))
    saved = deepcopy(editor.project)
    assert editor.undo() and editor.project == original
    assert editor.redo() and editor.project == saved
    path = tmp_path / 'input.lumen'
    editor.project.save(path)
    assert json.loads(path.read_text())['version'] == VERSION
    assert Project.load(path) == saved
    for invalid in ['auto', 'log', None, False, [], 2]:
        with pytest.raises(ValueError, match='Input encoding'):
            editor.update_clip('c', grade=Grade(input_encoding=invalid))
        assert editor.project == saved


@pytest.mark.parametrize('version', [1, 2, 3, 4, 5])
def test_legacy_input_and_metadata_migrate_without_pixel_change(version):
    p = Project(media=[Media('m', '/tmp/source', 'Source', 1)],
                clips=[Clip('c', 'm', 0, 0, 0, 1, grade=Grade(exposure=1))])
    payload = p.to_dict()
    payload['version'] = version; strip_contrast_pivot_field(payload)
    if payload['version'] < 17:
        for old_clip in payload.get('clips', []):
            old_clip.get('grade', {}).pop('hue_hue_curve', None)
            old_clip.get('grade', {}).pop('hue_sat_curve', None)
            old_clip.get('grade', {}).pop('qualifier', None)
    if payload['version'] < 16:
        for old_clip in payload.get('clips', []): old_clip.pop('transform', None)
    payload['clips'][0].pop('restoration')
    payload['clips'][0].pop('stabilization')
    mapping = payload['clips'][0].pop('time_map')
    payload['clips'][0]['source_in'] = mapping['source_in_num']
    del payload['clips'][0]['grade']['input_encoding']
    for name in ('color_transfer', 'color_primaries', 'color_range'):
        del payload['media'][0][name]
    old = deepcopy(payload)
    restored = Project.from_dict(payload)
    assert restored == p and payload == old
    rgb = np.array([[-.1, .18, 1.1]], np.float32)
    np.testing.assert_array_equal(apply_grade(rgb, restored.clips[0].grade), rgb * 2)


@pytest.mark.parametrize('transfer,encoding', [('bt709', 'rec709'),
    ('iec61966-2-1', 'srgb'), ('linear', 'linear_rec709')])
def test_known_transfer_must_match_explicit_assignment(transfer, encoding):
    source = source_color_from_stream(stream(color_transfer=transfer))
    assert matching_input_encoding(transfer) == encoding
    validate_input_assignment(source, encoding)
    for alternative in {'rec709', 'srgb', 'linear_rec709'} - {encoding}:
        with pytest.raises(ValueError, match='Select .*Input encoding'):
            validate_input_assignment(source, alternative)


def test_untagged_assumptions_are_explicit_and_range_is_not_guessed_from_size():
    source = source_color_from_stream({'pix_fmt': 'yuv420p'})
    assert source.matrix == '709' and source.range == 'limited'
    rgb = source_color_from_stream({'pix_fmt': 'gbrp16le'})
    assert rgb.matrix == 'gbr' and rgb.range == 'full'
    for encoding in ('rec709', 'srgb', 'linear_rec709'):
        validate_input_assignment(source, encoding)
    status = input_encoding_status(Media('m', '/tmp/a', 'a', 1), 'srgb')
    assert 'untagged; assigned sRGB' in status and 'primaries assumed' in status


@pytest.mark.parametrize('metadata', [
    {'color_transfer': 'smpte2084'}, {'color_transfer': 'arib-std-b67'},
    {'color_transfer': 'log100'}, {'color_primaries': 'bt2020'},
    {'color_primaries': 'smpte432'}, {'color_space': 'bt2020nc'},
    {'color_space': 'gbr'}, {'color_range': 'invalid'}, {'color_transfer': None},
    {'color_transfer': ['bt709']}, {'pix_fmt': 'unknown'},
    {'color_space': 'smpte170m', 'color_primaries': 'unknown'},
    {'color_space': 'smpte170m', 'color_primaries': 'smpte170m'},
    {'pix_fmt': 'yuvj420p', 'color_range': 'tv'},
    {'side_data_list': None}, {'side_data_list': ['not an object']},
    {'side_data_list': [{'side_data_type': 'Mastering display metadata'}]},
    {'side_data_list': [{'side_data_type': 'Content light level metadata'}]},
    {'side_data_list': [{'side_data_type': 'DOVI configuration record'}]},
    {'side_data_list': [{'side_data_type': 'HDR Dynamic Metadata SMPTE2094-40'}]},
])
def test_unsupported_or_malformed_sources_rejected(metadata):
    with pytest.raises(ValueError):
        source_color_from_stream(stream(**metadata))


def test_601_matrix_only_when_709_primaries_verified():
    for matrix, expected in [('bt470bg', '470bg'), ('smpte170m', '170m')]:
        source = source_color_from_stream(stream(color_space=matrix))
        filters = source_rgb_filters('unused', 30, RenderError, source)
        assert f'matrixin={expected}:matrix=gbr:rangein=limited:range=full' in filters[0]


def test_limited_rgb_is_rejected_instead_of_silently_compressed():
    with pytest.raises(ValueError, match='Limited-range RGB input is unsupported'):
        source_color_from_stream(stream(pix_fmt='gbrp16le', color_space='gbr', color_range='tv'))


@pytest.mark.parametrize('payload', ['not json', '{}', '{"streams": []}',
    '{"streams": null}', '{"streams": [{}]}', '{"streams": [{}, {}]}'])
def test_malformed_probe_output_has_actionable_error(monkeypatch, payload):
    monkeypatch.setattr('lumen.color_media.subprocess.run', lambda *a, **kw:
                        SimpleNamespace(returncode=0, stdout=payload))
    with pytest.raises(RenderError, match='Cannot interpret source color metadata'):
        read_source_color('unused', 30, RenderError)
