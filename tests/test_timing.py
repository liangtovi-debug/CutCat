"""Exact timing, strict interchange, and bounded parsing without Qt/FFmpeg."""
from dataclasses import FrozenInstanceError
from fractions import Fraction
from functools import reduce
from operator import mul

import pytest

from lumen.timing import (
    MAX_SOURCE_DENOMINATOR, MAX_SOURCE_NUMERATOR, ClipTimeMap, atempo_factors,
    duration_frames, parse_source_position, parse_speed, round_half_up,
    sample_boundary, seconds_at_source, source_interval, source_position,
    source_to_local, validate_time_map,
)


@pytest.mark.parametrize('speed', [Fraction(1, 4), Fraction(1, 2), Fraction(2, 3),
                                    Fraction(1), Fraction(3, 2), Fraction(2), Fraction(4)])
def test_exact_forward_inverse_duration_and_tail(speed):
    mapping = ClipTimeMap(Fraction(5, 7), Fraction(31), speed)
    assert source_interval(mapping) == (Fraction(5, 7), Fraction(31))
    frames = duration_frames(mapping)
    assert source_position(mapping, frames - 1) < mapping.source_out
    assert source_position(mapping, frames) >= mapping.source_out
    for frame in [0, 1, frames - 1, Fraction(3, 7)]:
        assert source_to_local(mapping, source_position(mapping, frame)) == frame
    assert ClipTimeMap.from_dict(mapping.to_dict()) == mapping


def test_partial_endpoint_and_immutability():
    mapping = ClipTimeMap(0, 31, 2)
    assert duration_frames(mapping) == 16
    assert mapping.source_out - source_position(mapping, 15) == 1
    with pytest.raises(FrozenInstanceError):
        mapping.speed = Fraction(1)


@pytest.mark.parametrize('fps', [Fraction(24), Fraction(30), Fraction(30000, 1001), Fraction(24000, 1001)])
def test_exact_seconds_and_global_sample_budgets(fps):
    mapping = ClipTimeMap(Fraction(5, 3), 200, Fraction(2, 3))
    assert seconds_at_source(mapping, 7, fps) == Fraction(19, 3) / fps
    boundaries = [0, 1, 7, 13, 59, 3600, 108000]
    budgets = [sample_boundary(b, fps) - sample_boundary(a, fps)
               for a, b in zip(boundaries, boundaries[1:])]
    assert sum(budgets) == round_half_up(Fraction(108000 * 48000) / fps)


@pytest.mark.parametrize('value,result', [('0.25', Fraction(1, 4)), ('2/3', Fraction(2, 3)),
                                         ('1.50', Fraction(3, 2)), ('4', Fraction(4)),
                                         ('.5', Fraction(1, 2)), ('+1.0', Fraction(1)),
                                         (Fraction(3, 2), Fraction(3, 2)), (2, Fraction(2))])
def test_ui_speed_parse_is_exact(value, result):
    assert parse_speed(value) == result


@pytest.mark.parametrize('value', [True, False, 1.0, float('nan'), float('inf'),
                                  0, -1, '0.249999', '4.000001', 'NaN', 'Infinity',
                                  '1/0', '1e999999999', '1e-999999999', '', '1_000',
                                  '1' * 129, '1.0000001', Fraction(1000001, 1000000)])
def test_invalid_and_excessive_speed_input_fails(value):
    with pytest.raises(ValueError):
        parse_speed(value)


@pytest.mark.parametrize('value', [True, 1.0, -1, '1e999999999',
                                  Fraction(1, MAX_SOURCE_DENOMINATOR + 1), MAX_SOURCE_NUMERATOR + 1])
def test_invalid_and_excessive_source_input_fails(value):
    with pytest.raises(ValueError):
        parse_source_position(value)


def test_exact_source_decimal_and_limit():
    assert parse_source_position('15.5') == Fraction(31, 2)
    assert parse_source_position('31/2') == Fraction(31, 2)
    assert parse_source_position(MAX_SOURCE_NUMERATOR) == MAX_SOURCE_NUMERATOR
    assert parse_source_position('0.000001') == Fraction(1, 1000000)


@pytest.mark.parametrize('field,value', [('source_in_num', True), ('source_in_den', False),
    ('source_out_num', 31.0), ('source_out_num', float('nan')), ('source_out_num', float('inf')),
    ('source_in_num', -1), ('source_in_num', MAX_SOURCE_NUMERATOR + 1),
    ('source_in_den', 0), ('source_in_den', -1), ('source_in_den', 1000001),
    ('source_out_den', 1000001), ('speed_num', 1000001), ('speed_den', 1000001),
    ('speed_num', 0), ('speed_num', -1), ('speed_den', 0), ('speed_den', -1)])
def test_serialized_pairs_reject_invalid_values(field, value):
    payload = ClipTimeMap(0, 31, 2).to_dict()
    payload[field] = value
    with pytest.raises(ValueError):
        ClipTimeMap.from_dict(payload)


@pytest.mark.parametrize('bad', [None, [], {}, {'extra': 1},
    dict(source_in_num=0, source_in_den=2, source_out_num=31, source_out_den=1, speed_num=2, speed_den=1),
    dict(source_in_num=0, source_in_den=1, source_out_num=31, source_out_den=1, speed_num=4, speed_den=2)])
def test_serialized_map_requires_complete_canonical_pairs(bad):
    with pytest.raises(ValueError):
        ClipTimeMap.from_dict(bad)


@pytest.mark.parametrize('args', [(0, 0), (5, 4), (-1, 4), (0.0, 4), (True, 4),
                                 (0, 4, 1.0), (0, 4, '1.5'), (0, 4, Fraction(1, 8)), (0, 4, 8)])
def test_canonical_constructor_rejects_invalid_map(args):
    with pytest.raises(ValueError):
        ClipTimeMap(*args)


@pytest.mark.parametrize('rate', [Fraction(1, 4), Fraction(1, 3), Fraction(1, 2), Fraction(2, 3),
                                  Fraction(1), Fraction(3, 2), Fraction(2), Fraction(3), Fraction(4)])
def test_atempo_factors_multiply_exactly_and_avoid_skipping(rate):
    factors = atempo_factors(rate)
    assert all(Fraction(1, 2) <= factor <= 2 for factor in factors)
    assert reduce(mul, factors, Fraction(1)) == rate
    if rate == 1:
        assert factors == ()


@pytest.mark.parametrize('value,result', [(Fraction(1, 2), 1), (Fraction(3, 2), 2),
                                         (Fraction(-1, 2), -1), (Fraction(1, 3), 0), (2, 2)])
def test_round_half_up(value, result):
    assert round_half_up(value) == result


@pytest.mark.parametrize('call', [lambda: sample_boundary(True, 30), lambda: sample_boundary(-1, 30),
    lambda: sample_boundary(1, 0), lambda: sample_boundary(1, 30.0), lambda: sample_boundary(1, 30, True),
    lambda: sample_boundary(1, 30, 0), lambda: seconds_at_source(ClipTimeMap(0, 1), 0, -1),
    lambda: source_position(ClipTimeMap(0, 1), 1.5), lambda: round_half_up(0.5),
    lambda: validate_time_map({})])
def test_helper_invalid_inputs_fail_clearly(call):
    with pytest.raises(ValueError):
        call()
