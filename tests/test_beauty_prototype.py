"""Synthetic numerical tests; these are not face-detection or footage evidence."""
from __future__ import annotations

import threading
from fractions import Fraction

import numpy as np
import pytest

import lumen.beauty_prototype as beauty


def _run(image, skin=None, protection=None, **kwargs):
    if skin is None:
        skin = np.ones(image.shape[:2], dtype=np.float32)
    if protection is None:
        protection = np.zeros(image.shape[:2], dtype=np.float32)
    options = dict(enabled=True, smoothing_strength=.8, detail_protection=.75)
    options.update(kwargs)
    return beauty.apply_beauty_prototype(image, skin, protection, **options)


def _face_fixture():
    """Explicit face-shaped synthetic regions, with no estimated semantic mask."""
    y, x = np.mgrid[:144, :192].astype(np.float32)
    oval = ((x - 96) / 63) ** 2 + ((y - 74) / 67) ** 2
    skin = np.clip((np.float32(1) - oval) / np.float32(.12), 0, 1)
    lighting = (.00065 * (x - 96) + .0004 * (y - 74)
                + .012 * np.cos((x - 96) / 65))
    clean = np.zeros((144, 192, 3), dtype=np.float32)
    clean[:] = [.58, .39, .31]
    clean += lighting[..., None]
    texture = .0035 * np.sin(.91 * x) * np.cos(1.13 * y)
    textured = clean + texture[..., None]
    blemish = .035 * np.exp(-((x - 73) ** 2 + (y - 85) ** 2) / (2 * 1.1 ** 2))
    image = textured - blemish[..., None] * np.array([.8, 1, .65], dtype=np.float32)

    left_eye = ((x - 72) / 12) ** 2 + ((y - 59) / 5) ** 2 < 1
    right_eye = ((x - 120) / 12) ** 2 + ((y - 59) / 5) ** 2 < 1
    lips = ((x - 96) / 15) ** 2 + ((y - 105) / 5) ** 2 < 1
    hair = (y < 31 + .002 * (x - 96) ** 2) & (oval < 1)
    protection = np.zeros(skin.shape, dtype=np.float32)
    protection[left_eye | right_eye] = .2
    protection[lips] = .01
    protection[hair] = 1
    image[left_eye | right_eye] = [.12, .095, .08]
    image[lips] = [.42, .17, .19]
    image[hair] = [.045, .035, .025]
    # A foreground occluder and an unknown hole must not contribute to smoothing.
    occlusion = (x > 116) & (x < 133) & (y > 77) & (y < 96)
    image[occlusion] = [.1, .2, .8]
    skin[occlusion] = 0
    unknown = (x > 66) & (x < 75) & (y > 112) & (y < 122)
    skin[unknown] = 0
    image[skin == 0] += (.025 * np.sin(2 * x[skin == 0]))[:, None]
    return image, skin, protection, clean, textured, blemish


def test_default_is_disabled_and_identity():
    image, skin, protection, *_ = _face_fixture()
    assert beauty.apply_beauty_prototype(image, skin, protection) is image


@pytest.mark.parametrize("options", [
    {"enabled": False}, {"smoothing_strength": 0}, {"confidence": 0},
])
def test_inactive_options_return_original_exact_object(options):
    image, skin, protection, *_ = _face_fixture()
    before = image.view(np.uint32).copy()
    assert _run(image, skin, protection, **options) is image
    np.testing.assert_array_equal(image.view(np.uint32), before)


@pytest.mark.parametrize("skin_value,protection_value", [(0, 0), (1, 1), (.4, .001)])
def test_no_eligible_pixels_returns_original_object(skin_value, protection_value):
    image = np.random.default_rng(4).random((17, 19, 3), dtype=np.float32)
    skin = np.full(image.shape[:2], skin_value, dtype=np.float32)
    protection = np.full(image.shape[:2], protection_value, dtype=np.float32)
    assert _run(image, skin, protection) is image


def test_protected_eyes_lips_hair_and_soft_overlap_have_zero_delta():
    image, skin, protection, *_ = _face_fixture()
    # This deliberate overlap checks that protection dominates skin confidence.
    skin[protection > 0] = .55
    before = image.view(np.uint32).copy()
    result = _run(image, skin, protection, smoothing_strength=1, detail_protection=0)
    np.testing.assert_array_equal(result[protection > 0].view(np.uint32),
                                  before[protection > 0])
    assert np.max(np.abs(result - image)) > .001
    np.testing.assert_array_equal(image.view(np.uint32), before)


def test_mask_holes_occlusion_and_background_are_exact_not_global_blur():
    image, skin, protection, *_ = _face_fixture()
    result = _run(image, skin, protection)
    np.testing.assert_array_equal(result[skin == 0].view(np.uint32),
                                  image[skin == 0].view(np.uint32))
    assert np.any(result[(skin > .9) & (protection == 0)] !=
                  image[(skin > .9) & (protection == 0)])


@pytest.mark.parametrize("excluded_by", ["skin", "protection"])
def test_excluded_pixels_do_not_contaminate_neighboring_skin(excluded_by):
    # Differences are below the range cutoff: invariance must come from masks.
    image = np.full((13, 17, 3), .5, dtype=np.float32)
    altered = image.copy()
    altered[5:8, 7:10] = [.53, .47, .52]
    skin = np.ones(image.shape[:2], dtype=np.float32)
    protection = np.zeros_like(skin)
    excluded = np.zeros_like(skin, dtype=bool)
    excluded[5:8, 7:10] = True
    if excluded_by == "skin":
        skin[excluded] = 0
    else:
        protection[excluded] = np.nextafter(np.float32(0), np.float32(1))
    first = _run(image, skin, protection, detail_protection=0)
    second = _run(altered, skin, protection, detail_protection=0)
    np.testing.assert_array_equal(first[~excluded], second[~excluded])
    np.testing.assert_array_equal(second[excluded], altered[excluded])


def test_synthetic_small_blemish_reduced_without_changing_lighting_or_protected_regions():
    image, skin, protection, clean, textured, blemish = _face_fixture()
    result = _run(image, skin, protection)
    # Known synthetic spot center, not a detector-derived quality measurement.
    before = abs(float(image[85, 73, 1] - textured[85, 73, 1]))
    after = abs(float(result[85, 73, 1] - textured[85, 73, 1]))
    assert .6 < after / before < .9
    # Lighting-only fixture should remain within float32 rounding everywhere in
    # this fully supported region, with the same settings and explicit masks.
    lighting_result = _run(clean, skin, protection)
    core = (skin > .99) & (protection == 0) & (blemish < .00001)
    assert np.max(np.abs(lighting_result[core] - clean[core])) < 2e-6


def test_detail_protection_retains_weak_texture_but_reduces_blemish_suppression():
    image, skin, protection, _, textured, _ = _face_fixture()
    low = _run(image, skin, protection, detail_protection=0)
    high = _run(image, skin, protection, detail_protection=1)
    # The same known cheek patch is free of protected features and the spot.
    cheek = np.s_[77:91, 91:108]
    low_error = np.mean(np.abs(low[cheek] - image[cheek]))
    high_error = np.mean(np.abs(high[cheek] - image[cheek]))
    assert low_error > .0002
    assert high_error < low_error * .05
    low_spot_error = abs(float(low[85, 73, 1] - textured[85, 73, 1]))
    high_spot_error = abs(float(high[85, 73, 1] - textured[85, 73, 1]))
    assert high_spot_error > low_spot_error


def _synthetic_metrics(strength, detail):
    """Reproducible fixture metrics, not photographic or perceptual quality."""
    image, skin, protection, clean, textured, _ = _face_fixture()
    result = _run(image, skin, protection, smoothing_strength=strength,
                  detail_protection=detail)
    lighting = _run(clean, skin, protection, smoothing_strength=strength,
                    detail_protection=detail)
    allowed = (skin > 0) & (protection == 0)
    cheek = np.s_[77:91, 91:108]

    def rms(value):
        return float(np.sqrt(np.mean(np.square(value, dtype=np.float64))))

    return {
        "allowed_rms_correction": rms((result - image)[allowed]),
        "protected_max_delta": float(np.max(np.abs(result[protection > 0]
                                                      - image[protection > 0]))),
        "background_max_delta": float(np.max(np.abs(result[skin == 0]
                                                       - image[skin == 0]))),
        "cheek_texture_attenuation": 1 - rms(result[cheek] - clean[cheek])
                                     / rms(image[cheek] - clean[cheek]),
        "spot_center_error_reduction": 1 - abs(float(result[85, 73, 1]
                                                     - textured[85, 73, 1]))
                                          / abs(float(image[85, 73, 1]
                                                      - textured[85, 73, 1])),
        "lighting_mean_shift": float(np.mean((lighting - clean)[allowed], dtype=np.float64)),
        "lighting_max_delta": float(np.max(np.abs(lighting - clean))),
    }


def test_measured_synthetic_tradeoff_is_reproducible():
    # Ranges allow small platform rounding differences, without asserting that
    # these fixture-specific scores transfer to real faces or all skin tones.
    conservative = _synthetic_metrics(.8, .75)
    smoothing = _synthetic_metrics(.8, 0)
    weaker = _synthetic_metrics(.4, .75)
    for metrics in (conservative, smoothing, weaker):
        assert metrics["protected_max_delta"] == 0
        assert metrics["background_max_delta"] == 0
    assert 8.8e-5 < conservative["allowed_rms_correction"] < 9e-5
    assert 9e-4 < smoothing["allowed_rms_correction"] < 9.2e-4
    assert conservative["cheek_texture_attenuation"] == 0
    assert .52 < smoothing["cheek_texture_attenuation"] < .55
    assert .24 < conservative["spot_center_error_reduction"] < .26
    assert .34 < smoothing["spot_center_error_reduction"] < .36
    assert .12 < weaker["spot_center_error_reduction"] < .13
    assert conservative["lighting_mean_shift"] == 0
    assert conservative["lighting_max_delta"] == 0
    assert abs(smoothing["lighting_mean_shift"]) < 1e-6
    assert 3e-4 < smoothing["lighting_max_delta"] < 5e-4


def test_strong_edges_and_isoluminant_color_edges_remain_unchanged():
    image = np.full((49, 61, 3), .3, dtype=np.float32)
    image[:, 20:40] = [.7, .3, .2]
    # Approximate equal-luma red/green jump, deliberately stronger than sigma.
    image[:, 40:] = [.3, .4189, .2]
    result = _run(image, detail_protection=0, smoothing_strength=1)
    np.testing.assert_array_equal(result, image)


def test_affine_lighting_gradient_has_no_interior_bias():
    y, x = np.mgrid[:79, :97].astype(np.float32)
    image = np.repeat((.25 + .001 * x + .0006 * y)[..., None], 3, axis=2)
    result = _run(image, detail_protection=0, smoothing_strength=1)
    assert np.max(np.abs(result[3:-3, 3:-3] - image[3:-3, 3:-3])) < 2e-7


def test_strength_and_confidence_are_continuous_linear_correction_scales():
    image, skin, protection, *_ = _face_fixture()
    strongest = _run(image, skin, protection, smoothing_strength=1)
    half = _run(image, skin, protection, smoothing_strength=.5)
    confidence = _run(image, skin, protection, smoothing_strength=1, confidence=.5)
    np.testing.assert_allclose(half - image, .5 * (strongest - image), atol=4e-8, rtol=0)
    np.testing.assert_array_equal(half, confidence)
    for parameter in ("smoothing_strength", "detail_protection", "confidence"):
        lower = _run(image, skin, protection, **{parameter: .4999})
        upper = _run(image, skin, protection, **{parameter: .5001})
        assert np.max(np.abs(upper - lower)) < 2e-5


def test_soft_skin_confidence_continuity_and_per_pixel_correction_bound():
    image, skin, protection, *_ = _face_fixture()
    result = _run(image, skin, protection, smoothing_strength=.63, confidence=.71,
                  detail_protection=0)
    limit = beauty.MAX_BEAUTY_PROTOTYPE_CORRECTION * .63 * .71 * skin
    assert np.all(np.max(np.abs(result - image), axis=2) <= limit + 6e-8)
    lower = _run(image, skin * np.float32(.4999), protection, detail_protection=0)
    upper = _run(image, skin * np.float32(.5001), protection, detail_protection=0)
    assert np.max(np.abs(upper - lower)) < 2e-5


def test_strided_readonly_images_and_masks_are_not_mutated():
    image, skin, protection, *_ = _face_fixture()
    image, skin, protection = image[::-1, ::-2], skin[::-1, ::-2], protection[::-1, ::-2]
    snapshots = [value.copy() for value in (image, skin, protection)]
    for value in (image, skin, protection):
        value.flags.writeable = False
    result = _run(image, skin, protection)
    expected = _run(*snapshots)
    np.testing.assert_array_equal(result, expected)
    assert result.dtype == np.float32
    assert not np.shares_memory(result, image)
    for value, snapshot in zip((image, skin, protection), snapshots):
        np.testing.assert_array_equal(value, snapshot)


@pytest.mark.parametrize("shape", [(1, 1), (1, 13), (17, 1), (2, 2), (31, 5), (33, 7)])
def test_tiny_and_strip_boundary_shapes(shape):
    image = np.random.default_rng(19).uniform(.46, .54, (*shape, 3)).astype(np.float32)
    result = _run(image, detail_protection=0)
    assert result.shape == image.shape
    assert result.dtype == np.float32
    assert np.all(np.isfinite(result))
    assert np.min(result) >= np.min(image)
    assert np.max(result) <= np.max(image)


def test_tiling_does_not_add_seams_and_pads_only_bounded_strips(monkeypatch):
    image, skin, protection, *_ = _face_fixture()
    observed_shapes = []
    real_pad = np.pad

    def tracked_pad(array, *args, **kwargs):
        observed_shapes.append(array.shape)
        return real_pad(array, *args, **kwargs)

    monkeypatch.setattr(beauty.np, "pad", tracked_pad)
    usual = _run(image, skin, protection, detail_protection=0)
    assert observed_shapes
    assert max(shape[0] for shape in observed_shapes) <= beauty.BEAUTY_PROTOTYPE_TILE_ROWS + 4
    assert max(shape[0] for shape in observed_shapes) < image.shape[0]
    monkeypatch.setattr(beauty, "BEAUTY_PROTOTYPE_TILE_ROWS", 1)
    single_row = _run(image, skin, protection, detail_protection=0)
    monkeypatch.setattr(beauty, "BEAUTY_PROTOTYPE_TILE_ROWS", 200)
    one_tile = _run(image, skin, protection, detail_protection=0)
    np.testing.assert_array_equal(single_row, usual)
    np.testing.assert_array_equal(one_tile, usual)


def test_signed_zero_constant_and_out_of_range_samples_are_preserved():
    image = np.zeros((19, 23, 3), dtype=np.float32)
    image[:5] = [-.5, 1.5, 2.5]
    image[5:10] = [-0., -0., -0.]
    image[10:] = [-3., 1.1, -.4]
    result = _run(image, detail_protection=0, smoothing_strength=1)
    np.testing.assert_array_equal(result.view(np.uint32), image.view(np.uint32))


def test_active_out_of_range_processing_is_finite_without_domain_clipping():
    image = np.full((11, 13, 3), [-.4, 1.2, 2], dtype=np.float32)
    image[5, 6] += np.float32(.02)
    result = _run(image, detail_protection=0)
    assert np.any(result != image)
    assert np.all(result[..., 0] < 0)
    assert np.all(result[..., 1:] > 1)
    assert np.all(np.isfinite(result))


def test_large_baseline_final_delta_allows_float32_addition_rounding():
    image = np.full((7, 9, 3), 10000, dtype=np.float32)
    image[3, 4] += np.float32(.05)
    result = _run(image, detail_protection=0, smoothing_strength=1)
    maximum_delta = np.max(np.abs(result - image))
    assert maximum_delta > beauty.MAX_BEAUTY_PROTOTYPE_CORRECTION
    # The correction bound applies before adding to a large float32 sample.
    half_ulp = np.max(np.spacing(image)) / np.float32(2)
    assert maximum_delta <= beauty.MAX_BEAUTY_PROTOTYPE_CORRECTION + half_ulp
    assert np.all(np.isfinite(result))
    assert np.min(result) >= np.min(image)
    assert np.max(result) <= np.max(image)


def test_subnormal_rgb_and_masks_work_under_strict_caller_error_settings():
    tiny = np.nextafter(np.float32(0), np.float32(1))
    image = np.zeros((7, 9, 3), dtype=np.float32)
    image[3, 4] = tiny
    skin = np.full(image.shape[:2], tiny, dtype=np.float32)
    with np.errstate(all="raise"):
        before = np.geterr().copy()
        result = _run(image, detail_protection=0, smoothing_strength=1)
        masked = _run(image, skin, detail_protection=0, smoothing_strength=1)
        assert np.geterr() == before
    assert np.all(np.isfinite(result))
    assert np.all(np.isfinite(masked))
    np.testing.assert_array_equal(image[3, 4], np.full(3, tiny, dtype=np.float32))


def test_extreme_finite_float32_values_never_create_nonfinite_or_mutate_input():
    maximum = np.finfo(np.float32).max
    image = np.zeros((9, 11, 3), dtype=np.float32)
    image[::2] = maximum
    image[1::2] = -maximum
    image[4, 4] = [maximum, -maximum, -0.]
    before = image.view(np.uint32).copy()
    with np.errstate(all="raise"):
        result = _run(image, detail_protection=0, smoothing_strength=1)
    assert np.all(np.isfinite(result))
    np.testing.assert_array_equal(result.view(np.uint32), before)
    np.testing.assert_array_equal(image.view(np.uint32), before)


def test_bool_masks_supported():
    image = np.full((7, 9, 3), .5, dtype=np.float32)
    image[3, 4] = .52
    skin = np.ones(image.shape[:2], dtype=bool)
    protection = np.zeros_like(skin)
    result = _run(image, skin, protection, detail_protection=0)
    assert result[3, 4, 0] < image[3, 4, 0]


@pytest.mark.parametrize("parameter", ["smoothing_strength", "detail_protection", "confidence"])
@pytest.mark.parametrize("value", [-.1, 1.1, np.nan, np.inf, True, "0.5", None])
def test_rejects_invalid_parameters(parameter, value):
    image = np.full((3, 4, 3), .5, dtype=np.float32)
    with pytest.raises(ValueError, match=parameter):
        _run(image, **{parameter: value})


@pytest.mark.parametrize("parameter", ["smoothing_strength", "detail_protection", "confidence"],
                         ids=["strength", "detail", "confidence"])
@pytest.mark.parametrize("value", [10 ** 1000, -(10 ** 1000)],
                         ids=["huge-positive-int", "huge-negative-int"])
def test_huge_integer_parameters_raise_documented_value_error(parameter, value):
    image = np.full((3, 4, 3), .5, dtype=np.float32)
    with pytest.raises(ValueError, match=parameter):
        _run(image, **{parameter: value})


@pytest.mark.parametrize("value", [Fraction(10 ** 100 + 1, 10 ** 100),
                                  Fraction(-1, 10 ** 1000)],
                         ids=["just-above-one", "tiny-negative"])
def test_parameter_range_validation_precedes_lossy_float_conversion(value):
    image = np.full((3, 4, 3), .5, dtype=np.float32)
    with pytest.raises(ValueError, match="confidence"):
        _run(image, confidence=value)


@pytest.mark.parametrize("image", [
    np.zeros((0, 1, 3), dtype=np.float32),
    np.zeros((1, 0, 3), dtype=np.float32),
    np.zeros((2, 3), dtype=np.float32),
    np.zeros((2, 3, 4), dtype=np.float32),
    np.zeros((2, 3, 3), dtype=np.float64),
    np.zeros((1, 16385, 3), dtype=np.float32),
])
def test_rejects_invalid_image_shape_dtype_or_dimensions(image):
    with pytest.raises(ValueError):
        _run(image)


@pytest.mark.parametrize("nonfinite", [np.nan, np.inf, -np.inf])
def test_nonfinite_image_is_rejected_even_when_disabled(nonfinite):
    image = np.zeros((3, 4, 3), dtype=np.float32)
    image[1, 2, 1] = nonfinite
    with pytest.raises(ValueError, match="finite"):
        _run(image, enabled=False)


@pytest.mark.parametrize("mask_name", ["skin", "protection"])
@pytest.mark.parametrize("bad", [
    np.zeros((2, 3), dtype=np.float32),
    np.zeros((3, 4), dtype=np.float64),
    np.full((3, 4), np.nan, dtype=np.float32),
    np.full((3, 4), -.001, dtype=np.float32),
    np.full((3, 4), 1.001, dtype=np.float32),
])
def test_invalid_mask_rejected_even_when_zero_strength(mask_name, bad):
    image = np.zeros((3, 4, 3), dtype=np.float32)
    with pytest.raises(ValueError, match="mask"):
        _run(image, **{mask_name: bad}, smoothing_strength=0)


def test_invalid_enabled_or_cancellation_type_rejected():
    image = np.zeros((3, 4, 3), dtype=np.float32)
    with pytest.raises(ValueError, match="boolean"):
        _run(image, enabled="yes")
    with pytest.raises(ValueError, match="cancel"):
        _run(image, cancel=object())


def test_event_and_processing_cancellation_preserve_all_inputs():
    image, skin, protection, *_ = _face_fixture()
    originals = [value.copy() for value in (image, skin, protection)]
    event = threading.Event()
    event.set()
    with pytest.raises(beauty.BeautyPrototypeCancelled):
        _run(image, skin, protection, cancel=event)
    calls = 0
    validation_tiles = int(np.ceil(image.shape[0] / beauty.BEAUTY_PROTOTYPE_TILE_ROWS))

    def cancelled_during_processing():
        nonlocal calls
        calls += 1
        return calls == validation_tiles + 2

    with pytest.raises(beauty.BeautyPrototypeCancelled):
        _run(image, skin, protection, cancel=cancelled_during_processing)
    assert calls == validation_tiles + 2
    for value, original in zip((image, skin, protection), originals):
        np.testing.assert_array_equal(value, original)


def test_callback_error_is_not_swallowed():
    image = np.zeros((3, 4, 3), dtype=np.float32)

    def bad_callback():
        raise LookupError("caller error")

    with pytest.raises(LookupError, match="caller error"):
        _run(image, cancel=bad_callback)
