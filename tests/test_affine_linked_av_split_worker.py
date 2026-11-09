"""Source-proof branch coverage with explicit synthetic decoder observations."""
from copy import deepcopy
from fractions import Fraction
from types import SimpleNamespace

import pytest

from lumen.linked_av_split import plan_linked_av_split, verify_linked_av_split
from lumen.source_viewer import SourcePreviewBackend, SourceViewerError
from lumen.timing import ClipTimeMap
from test_linked_av_split_worker import direct, proof
from test_affine_linked_av_split_model import affine_editor


@pytest.mark.parametrize('fractional',[False,True])
@pytest.mark.parametrize('role',['v','a'])
def test_source_envelope_retains_partial_tail_and_only_proves_used_legacy_paths(direct,monkeypatch,fractional,role):
    info,calls,identity=direct;editor=affine_editor(fractional=fractional)
    keys=[]
    def frame_keys(session,identity,info,start,end,legacy,cancel,deadline):
        assert not legacy, 'Unused legacy path must not qualify a fractional map'
        keys.append((start,end));return tuple(('frame',i) for i in range(start,end))
    monkeypatch.setattr(SourcePreviewBackend,'_timing_keys',staticmethod(frame_keys))
    backend=SourcePreviewBackend()
    assert backend.verify_linked_av_split_source(editor.project,role,12)==identity
    video=editor._clip('v');start=video.source_in.__floor__();cut=start+7;end=start+16
    assert end==(video.source_in+video.duration-1).__floor__()+1
    if not fractional: assert end>int(video.source_out)
    assert calls[0][3]==[(start,end)]
    assert calls[0][4]==([] if fractional else [(start,cut)])
    assert keys==[(start,end),(start,cut),(cut,end)]
    assert not backend._lock.locked();backend.close()


@pytest.mark.parametrize('defect',['missing-tail','wrong-right','wrong-left'])
def test_incomplete_or_different_global_frame_sequence_refuses_and_releases_lock(direct,monkeypatch,defect):
    info,calls,identity=direct;editor=affine_editor(fractional=True)
    count=0
    def frame_keys(session,identity,info,start,end,legacy,cancel,deadline):
        nonlocal count
        count+=1;value=tuple(range(start,end))
        if (defect=='missing-tail' and count==1):return value[:-1]
        if (defect=='wrong-right' and count==3) or (defect=='wrong-left' and count==2):
            return (999,*value[1:])
        return value
    monkeypatch.setattr(SourcePreviewBackend,'_timing_keys',staticmethod(frame_keys))
    backend=SourcePreviewBackend()
    with pytest.raises(SourceViewerError,match='do not partition'):
        backend.verify_linked_av_split_source(editor.project,'a',12)
    assert not backend._lock.locked();backend.close()


def test_complete_frame_capacity_uses_sampled_envelope_not_floored_endpoint(direct):
    info,calls,identity=direct;editor=affine_editor();video=editor._clip('v')
    info['capacity']=int(video.source_out)
    backend=SourcePreviewBackend()
    with pytest.raises(SourceViewerError,match='complete source-frame'):
        backend.verify_linked_av_split_source(editor.project,'v',12)
    assert not calls and not backend._lock.locked();backend.close()


def test_affine_worker_preview_and_apply_repeat_complete_proof(proof):
    calls,identity=proof;editor=affine_editor();before=deepcopy(editor.project)
    plan=plan_linked_av_split(editor,'a',12)
    preview=verify_linked_av_split(plan);apply=verify_linked_av_split(plan,preview)
    assert preview.phase=='preview' and apply.phase=='apply'
    assert [kind for kind,_ in calls]==['dependencies','source','dependencies']*2
    assert editor.project==before


def test_noninterior_affine_cut_rejects_before_backend_or_source_access(monkeypatch):
    editor=affine_editor(geometry='trailing')
    backend=SimpleNamespace(_begin=lambda *a,**k:pytest.fail('Source I/O before both-role qualification'))
    with pytest.raises(ValueError,match='strictly inside both'):
        SourcePreviewBackend.verify_linked_av_split_source(backend,editor.project,'v',6)


def test_actual_new_legacy_left_half_still_requires_real_equivalence(direct,monkeypatch):
    editor=affine_editor()
    def reject(session,identity,info,intervals,legacy,cancel,deadline):
        assert legacy==[(10,17)]
        raise SourceViewerError('Legacy source trim phase cannot exactly represent this boundary; source inspection remains available.')
    monkeypatch.setattr(SourcePreviewBackend,'_prove_cfr',staticmethod(reject))
    backend=SourcePreviewBackend()
    with pytest.raises(SourceViewerError,match='Legacy source trim phase'):
        backend.verify_linked_av_split_source(editor.project,'a',12)
    assert not backend._lock.locked();backend.close()


@pytest.mark.parametrize('fractional',[False,True])
def test_real_cfr_proof_rejects_used_legacy_mismatch_but_never_consults_unused_path(monkeypatch,fractional):
    """Exercise the actual CFR/hash comparator; only decoder observations fake."""
    import json
    import lumen.source_viewer as source
    from lumen.source_viewer import SourceIdentity
    editor=affine_editor(fractional=fractional)
    identity=SourceIdentity('/synthetic-source',(),'hash','signature')
    info={'origin':Fraction(0),'capacity':24000,
          'video':{'start_time':'0','r_frame_rate':'24/1','avg_frame_rate':'24/1','time_base':'1/24000'},
          'audio':{'start_time':'0'}}
    monkeypatch.setattr(source,'_identity',lambda *a:identity)
    monkeypatch.setattr(SourcePreviewBackend,'_probe_source',lambda *a:info)
    def decoded(arguments,*args):
        interval=arguments[arguments.index('-read_intervals')+1]
        indices=[0] if interval=='%+#1' else range(40)
        return json.dumps({'frames':[{'best_effort_timestamp':i*1000,'duration':1000} for i in indices]}).encode()
    monkeypatch.setattr(source,'_run',decoded)
    observed=[]
    def keys(session,identity,info,start,end,legacy,cancel,deadline):
        observed.append((start,end,legacy))
        # Any legacy decoder phase is deliberately wrong. Fractional-origin
        # rendering must not call it; integral-origin left actually uses it.
        return tuple((i+1000 if legacy else i) for i in range(start,end))
    monkeypatch.setattr(SourcePreviewBackend,'_timing_keys',staticmethod(keys))
    backend=SourcePreviewBackend()
    if fractional:
        assert backend.verify_linked_av_split_source(editor.project,'a',12)==identity
        assert observed==[(9,25,False),(9,16,False),(16,25,False)]
    else:
        with pytest.raises(SourceViewerError,match='Legacy source trim phase'):
            backend.verify_linked_av_split_source(editor.project,'a',12)
        assert observed==[(10,17,False),(10,17,True)]
    assert not backend._lock.locked();backend.close()
