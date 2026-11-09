"""Source-time ASR is inverse-mapped once, with exact shared boundaries."""
from fractions import Fraction
import pytest
from lumen.asr import TranscriptSegment
from lumen.timing import ClipTimeMap
from lumen.ui_captions import segments_to_captions


@pytest.mark.parametrize('fps', [Fraction(24), Fraction(30), Fraction(30000, 1001), Fraction(24000, 1001)])
@pytest.mark.parametrize('speed', [Fraction(1, 4), Fraction(1, 2), Fraction(2, 3), Fraction(1), Fraction(3, 2), Fraction(2), Fraction(4)])
def test_inverse_mapping_quantizes_output_only_and_preserves_shared_edges(fps, speed):
    origin = Fraction(5, 2); mapping = ClipTimeMap(origin, origin + 20 * speed, speed)
    boundary = origin + Fraction(5, 2) * speed
    cues = [TranscriptSegment((origin + Fraction(49, 100) * speed) / fps, boundary / fps, 'one'),
            TranscriptSegment(boundary / fps, (origin + Fraction(15, 2) * speed) / fps, 'two')]
    captions = segments_to_captions(cues, fps, time_map=mapping, start=100)
    assert [(cue.start, cue.duration) for cue in captions] == [(100, 3), (103, 5)]


def test_clip_intersection_uses_endpoint_not_output_duration():
    mapping = ClipTimeMap(Fraction(1, 2), 31, 2)
    captions = segments_to_captions([
        TranscriptSegment(0, Fraction(31, 30), 'full range'),
        TranscriptSegment(Fraction(31, 30), 2, 'outside')], 30, time_map=mapping, start=40)
    # Exact endpoint is 15.25 output frames, rounded once to 15.
    assert [(cue.start, cue.duration) for cue in captions] == [(40, 15)]


def test_collapsed_cues_reported_and_outside_cues_ignored():
    dropped = []; mapping = ClipTimeMap(0, 30, 4)
    captions = segments_to_captions([
        TranscriptSegment(0, Fraction(1, 100), 'tiny'),
        TranscriptSegment(Fraction(1, 100), Fraction(1, 2), 'kept'),
        TranscriptSegment(2, 3, 'outside')], 30, time_map=mapping, collapsed=dropped)
    assert [cue.text for cue in dropped] == ['tiny']
    assert [(cue.start, cue.duration) for cue in captions] == [(0, 4)]


def test_source_overlap_rejected_even_when_output_rounding_hides_it():
    mapping = ClipTimeMap(0, 30, 4)
    with pytest.raises(ValueError, match='overlap'):
        segments_to_captions([TranscriptSegment(0, Fraction(1, 50), 'a'),
                              TranscriptSegment(Fraction(1, 100), Fraction(1, 2), 'b')],
                             30, time_map=mapping)


def test_media_bin_keeps_legacy_one_x_quantization():
    cues = segments_to_captions([TranscriptSegment(0, .5, 'a'), TranscriptSegment(.5, 1, 'b')], Fraction(30000, 1001))
    assert [(cue.start, cue.duration) for cue in cues] == [(0, 15), (15, 15)]
