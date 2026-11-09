"""Bounded worker wiring and stale evidence; media oracles live separately."""
from dataclasses import replace
from fractions import Fraction
from threading import Event
import time

import pytest

from lumen.clip_copy_paste import ClipDependency
from lumen.linked_av_split import (LinkedAVSplitCancelled, LinkedAVSplitVerification,
    plan_linked_av_split, verify_linked_av_split)
from lumen.source_viewer import SourceIdentity, SourcePreviewBackend, SourceViewerError
from test_linked_av_split_model import editor_for


@pytest.fixture
def proof(monkeypatch):
    import lumen.clip_copy_paste as copy
    calls=[]; identity=SourceIdentity('/source',(), 'hash','signature')
    def dependencies(request,cancel,*,deadline=None):
        calls.append(('dependencies',deadline));return (ClipDependency('media','m',identity),)
    def verify(self,snapshot,clip_id,frame,cancel=None,*,deadline=None,expected_identity=None):
        assert expected_identity is not None
        calls.append(('source',deadline));return identity
    monkeypatch.setattr(copy,'_dependencies',dependencies)
    monkeypatch.setattr(SourcePreviewBackend,'verify_linked_av_split_source',verify)
    return calls,identity


def test_shared_total_deadline_two_phase_complete_proof(proof):
    calls,identity=proof;plan=plan_linked_av_split(editor_for(),'a',13);began=time.monotonic()
    preview=verify_linked_av_split(plan);applied=verify_linked_av_split(plan,preview)
    assert preview.phase=='preview' and applied.phase=='apply'
    assert preview.source_identity==applied.source_identity==identity
    assert [c[0] for c in calls]==['dependencies','source','dependencies']*2
    assert len({c[1] for c in calls[:3]})==len({c[1] for c in calls[3:]})==1
    assert began+119<calls[0][1]<=began+120.1


@pytest.mark.parametrize('phase',['apply','wrong_plan'])
def test_invalid_previous_receipt_rejects_before_io(proof,phase):
    calls,_=proof;plan=plan_linked_av_split(editor_for(),'v',13)
    receipt=LinkedAVSplitVerification(plan,(),None,'apply')
    if phase=='wrong_plan':receipt=replace(receipt,plan=replace(plan,frame=14),phase='preview')
    with pytest.raises(ValueError,match='stale'):verify_linked_av_split(plan,receipt)
    assert not calls


def test_dependencies_changed_inside_or_between_phases_reject(monkeypatch,proof):
    import lumen.clip_copy_paste as copy
    plan=plan_linked_av_split(editor_for(),'v',13);preview=verify_linked_av_split(plan)
    monkeypatch.setattr(copy,'_dependencies',lambda *a,**k:(ClipDependency('media','m',replace(proof[1],sample_sha256='changed')),))
    with pytest.raises(ValueError,match='dependencies changed|identity changed'):verify_linked_av_split(plan,preview)
    values=iter([(ClipDependency('media','m',proof[1]),),(ClipDependency('media','m',replace(proof[1],sample_sha256='changed')),)])
    monkeypatch.setattr(copy,'_dependencies',lambda *a,**k:next(values))
    with pytest.raises(ValueError,match='dependencies changed'):verify_linked_av_split(plan)


def test_source_identity_changed_between_phases_reject(proof,monkeypatch):
    plan=plan_linked_av_split(editor_for(),'v',13);preview=verify_linked_av_split(plan)
    monkeypatch.setattr(SourcePreviewBackend,'verify_linked_av_split_source',lambda *a,**k:replace(proof[1],sample_sha256='changed'))
    with pytest.raises(ValueError,match='dependencies changed|identity changed'):verify_linked_av_split(plan,preview)


def test_cancelled_worker_cannot_return_receipt(proof):
    plan=plan_linked_av_split(editor_for(),'v',13);cancel=Event();cancel.set()
    with pytest.raises(LinkedAVSplitCancelled):verify_linked_av_split(plan,cancel=cancel)


@pytest.fixture
def direct(monkeypatch):
    import lumen.source_viewer as source
    identity=SourceIdentity('/source',(), 'hash','signature');calls=[]
    monkeypatch.setattr(source,'_identity',lambda *a:identity)
    info={'video':{'start_time':'0'},'audio':{'start_time':'0'},'capacity':24000}
    monkeypatch.setattr(SourcePreviewBackend,'_probe_source',lambda *a:info)
    monkeypatch.setattr(SourcePreviewBackend,'_prove_cfr',staticmethod(lambda *a:calls.append(a)))
    return info,calls,identity


def test_public_helper_proves_original_and_both_halves_one_union(direct):
    info,calls,identity=direct;backend=SourcePreviewBackend();editor=editor_for()
    assert backend.verify_linked_av_split_source(editor.project,'a',13)==identity
    assert len(calls)==1
    assert calls[0][3]==[(80,100)] and calls[0][4]==[(80,100),(80,88),(88,100)]
    assert not backend._lock.locked();backend.close()


def test_public_helper_exact_renderer_offset_not_source_viewer_tolerance(direct):
    info,calls,_=direct;info['audio']['start_time']='0.0000001';backend=SourcePreviewBackend()
    with pytest.raises(SourceViewerError,match='audio offset changed'):
        backend.verify_linked_av_split_source(editor_for().project,'v',13)
    assert not calls and not backend._lock.locked();backend.close()


def test_public_helper_revalidates_source_after_pixel_proof(direct,monkeypatch):
    import lumen.source_viewer as source
    info,calls,identity=direct;observations=iter([identity,replace(identity,sample_sha256='changed')])
    monkeypatch.setattr(source,'_identity',lambda *a:next(observations))
    backend=SourcePreviewBackend()
    with pytest.raises(SourceViewerError,match='Source changed'):
        backend.verify_linked_av_split_source(editor_for().project,'v',13)
    assert len(calls)==1 and not backend._lock.locked();backend.close()


@pytest.mark.parametrize('kind',['video','audio','capacity','deadline'])
def test_public_helper_missing_or_expired_evidence_reject(direct,kind):
    info,calls,_=direct;backend=SourcePreviewBackend()
    if kind in ('video','audio'):info[kind]=None
    if kind=='capacity':info['capacity']=99
    with pytest.raises(SourceViewerError):
        backend.verify_linked_av_split_source(editor_for().project,'v',13,
            deadline=time.monotonic()-1 if kind=='deadline' else None)
    assert not calls and not backend._lock.locked();backend.close()


def test_shared_deadline_includes_wait_for_reused_backend_lock():
    backend=SourcePreviewBackend();backend._lock.acquire();began=time.monotonic()
    try:
        with pytest.raises(SourceViewerError):backend._begin(None,deadline=began+.03)
        assert time.monotonic()-began<.3
    finally:
        backend._lock.release();backend.close()


@pytest.mark.parametrize('original,adapted',[
    ('Source overwrite requires actual video FPS to equal project FPS; inspection remains available.','actual video FPS'),
    ('Source range is incomplete or variable-rate; overwrite cannot be proven.','incomplete or variable-rate')])
def test_reused_cfr_errors_are_scoped_to_split(direct,monkeypatch,original,adapted):
    def failure(*args):raise SourceViewerError(original)
    monkeypatch.setattr(SourcePreviewBackend,'_prove_cfr',staticmethod(failure))
    backend=SourcePreviewBackend()
    with pytest.raises(SourceViewerError,match='Linked A/V split.*'+adapted):
        backend.verify_linked_av_split_source(editor_for().project,'v',13)
    assert not backend._lock.locked();backend.close()


def test_dependency_a_proof_b_dependency_a_never_issues_receipt(proof,monkeypatch):
    calls,identity=proof;plan=plan_linked_av_split(editor_for(),'v',13)
    monkeypatch.setattr(SourcePreviewBackend,'verify_linked_av_split_source',
        lambda *a,**k:replace(identity,path='/other-source',sample_sha256='other'))
    with pytest.raises(ValueError,match='source identity changed'):
        verify_linked_av_split(plan)
    assert [call[0] for call in calls]==['dependencies']


def test_public_helper_binds_first_observation_before_decode(direct):
    _,calls,identity=direct;backend=SourcePreviewBackend()
    with pytest.raises(SourceViewerError,match='source identity changed'):
        backend.verify_linked_av_split_source(editor_for().project,'v',13,
            expected_identity=replace(identity,path='/other-source'))
    assert not calls and not backend._lock.locked();backend.close()


def test_source_viewer_signature_may_differ_but_same_observation_binds(direct):
    _,calls,identity=direct;backend=SourcePreviewBackend()
    assert backend.verify_linked_av_split_source(editor_for().project,'v',13,
        expected_identity=replace(identity,signature='dependency-signature'))==identity
    assert len(calls)==1;backend.close()
