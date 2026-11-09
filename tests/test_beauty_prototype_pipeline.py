"""Supplied-mask experiment only: no detector or production renderer exercised."""
import numpy as np
from lumen.beauty_mask_prototype import FrameObservation, ProtectedPolygons, build_face_mask, precompute_sequence_gate, gated_mask
from lumen.beauty_prototype import apply_beauty_prototype


def test_canonical_sequence_matches_out_of_order_preview():
    frames = np.random.default_rng(107).normal(.5, .012, (12, 40, 48, 3)).astype(np.float32)
    face = ((.1, .1), (.9, .1), (.9, .9), (.1, .9))
    eye = ((.25, .3), (.75, .3), (.75, .45), (.25, .45))
    region = build_face_mask(48, 40, [face], geometry_reviewed=True, exclusions=ProtectedPolygons(eyes=[eye]), feather_px=2)
    rows = [FrameObservation(i, i/24, .99, 'reviewed-face', detected=i!=5, geometry_valid=i!=5, scene_cut=i==9) for i in range(12)]
    plan = precompute_sequence_gate(rows, source_key='fixture:sha:analysis-v1')
    def evaluate(i, key='fixture:sha:analysis-v1'):
        mask = gated_mask(region, plan.decision_for(i, timestamp=i/24, source_key=key))
        return apply_beauty_prototype(frames[i], mask, np.zeros_like(mask), enabled=True, smoothing_strength=.8)
    chronological = [evaluate(i) for i in range(12)]
    for i in [11,4,0,8,5,2,9,3,10,1,7,6]:
        assert np.array_equal(evaluate(i), chronological[i])
        assert np.array_equal(evaluate(i,'replacement-source'),frames[i])
        assert np.array_equal(chronological[i][region.mask==0], frames[i][region.mask==0])
    for i in [0,5,6,9]:
        assert np.array_equal(chronological[i],frames[i])
    assert np.max(np.abs(chronological[4]-frames[4]))>0


def test_unknown_occlusion_never_becomes_nonzero_effect():
    image = np.random.default_rng(4).normal(.5,.01,(24,32,3)).astype(np.float32)
    face = ((0.,0.),(1.,0.),(1.,1.),(0.,1.))
    region = build_face_mask(32,24,[face])
    plan = precompute_sequence_gate([FrameObservation(i,i/30,1.,'face',detected=True) for i in range(8)],source_key='fixture')
    for i in range(8):
        mask=gated_mask(region,plan.decision_for(i,timestamp=i/30,source_key='fixture'))
        output=apply_beauty_prototype(image,mask,np.zeros_like(mask),enabled=True,smoothing_strength=1.,detail_protection=0.)
        assert output is image
