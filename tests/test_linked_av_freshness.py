"""Pure owner-thread plan/apply and bounded worker freshness of source + LUT."""
from copy import deepcopy
from dataclasses import replace
from hashlib import sha256
import json
import os
from pathlib import Path
from threading import Event, Thread
import pytest
from lumen.linked_av import (plan_linked_av_slip, verify_linked_av_slip, apply_linked_av_slip,
    LinkedAVSlipCancelled)
from lumen.model import Project
from test_affine_linked_av_slip_model import affine_editor, expected_project, wire
from test_linked_av_model import sample, state


pytestmark = pytest.mark.parametrize('geometry', ['strict', 'leading', 'trailing'])


def ready(tmp_path, *, origin='10', lut=False, geometry='strict'):
    editor=sample(link=True) if geometry == 'strict' else affine_editor(geometry=geometry)
    path=tmp_path/'source.mov';path.write_bytes(b'fixed source snapshot'*30000)
    editor.project.media[0].path=str(path)
    if lut:
        cube=tmp_path/'look.cube';cube.write_bytes(b'fixture LUT identity, not parsed'*5000)
        editor._clip('v').grade.lut_path=str(cube)
    plan=plan_linked_av_slip(editor,'a',origin)
    preview=verify_linked_av_slip(plan)
    return editor,plan,preview


def verified(plan,preview):return verify_linked_av_slip(plan,preview)


def test_plan_and_apply_no_gui_filesystem_reads_and_source_bytes_unchanged(tmp_path,monkeypatch,geometry):
    editor,plan,preview=ready(tmp_path,lut=True,geometry=geometry);receipt=verified(plan,preview)
    paths=[Path(editor.project.media[0].path),Path(editor._clip('v').grade.lut_path)]
    hashes=[sha256(p.read_bytes()).hexdigest() for p in paths]
    before=state(editor)
    def no_io(*args,**kwargs):raise AssertionError('owner-thread file I/O')
    with monkeypatch.context() as m:
        m.setattr(Path,'open',no_io);m.setattr(Path,'stat',no_io);m.setattr(Path,'lstat',no_io)
        plan_linked_av_slip(editor,'v',10)
        apply_linked_av_slip(editor,plan,receipt)
    assert editor.revision==before[3]+1 and editor._clip('a').source_in==10
    assert hashes==[sha256(p.read_bytes()).hexdigest() for p in paths]
    with pytest.raises(ValueError,match='stale'):apply_linked_av_slip(editor,plan,receipt)


@pytest.mark.parametrize('mutation',['revision','ABA','lock','new_open','unlink','relink','direct_payload','maps','tail','mode'])
def test_every_context_change_rejects_before_commit_without_history_damage(tmp_path,mutation,geometry):
    editor,plan,preview=ready(tmp_path,geometry=geometry);receipt=verified(plan,preview)
    if mutation=='revision':editor.update_clip('other',gain_db=-2)
    elif mutation=='ABA':editor.update_clip('other',gain_db=-2);editor.undo()
    elif mutation=='lock':editor.set_track_edit_lock(0,True)
    elif mutation=='new_open':editor.project=deepcopy(editor.project)
    elif mutation=='unlink':editor.unlink_av_clips(['a'])
    elif mutation=='relink':editor._commit(lambda:setattr(editor.project.media[0],'path','/new/source.mov'))
    elif mutation=='direct_payload':editor._clip('a').gain_db=-9
    elif mutation=='maps':
        from lumen.timing import ClipTimeMap
        for clip in editor.project.clips[:2]:
            mapping=clip.timing
            clip.time_map=ClipTimeMap(mapping.source_in+1,mapping.source_out+1,mapping.speed)
            clip.source_in=clip.time_map.source_in
    elif mutation=='tail':
        from lumen.timing import ClipTimeMap
        clip=editor._clip('a');mapping=clip.timing
        clip.time_map=ClipTimeMap(mapping.source_in,mapping.source_out-mapping.speed/8,mapping.speed)
    elif mutation=='mode':
        from lumen.model import AV_LINK_ALIGNMENT, AV_LINK_AFFINE_ALIGNMENT
        link=editor.project.av_links[0]
        mode=AV_LINK_AFFINE_ALIGNMENT if link.alignment == AV_LINK_ALIGNMENT else AV_LINK_ALIGNMENT
        editor.project.av_links[0]=replace(link,alignment=mode)
    before=state(editor);raw=wire(editor.project)
    project,records=editor.project,tuple(editor.project.clips)
    # An unequal strict map or invalid mode is malformed, not merely stale;
    # detached validation must reject it before attempting any state rebase.
    malformed = ((mutation=='tail' and geometry=='strict')
                 or (mutation=='mode' and geometry!='strict'))
    diagnostic = 'identical start, duration and complete rational source timing' if malformed else 'stale'
    with pytest.raises(ValueError,match=diagnostic):apply_linked_av_slip(editor,plan,receipt)
    assert state(editor)==before and wire(editor.project)==raw
    assert editor.project is project and all(a is b for a,b in zip(records,editor.project.clips))


@pytest.mark.parametrize('kind',['source','lut'])
def test_worker_detects_same_size_restored_mtime_dependency_replacement(tmp_path,kind,geometry):
    editor,plan,preview=ready(tmp_path,lut=True,geometry=geometry)
    path=Path(editor.project.media[0].path if kind=='source' else editor._clip('v').grade.lut_path)
    metadata=path.stat();data=bytearray(path.read_bytes());data[len(data)//2]^=1;path.write_bytes(data)
    os.utime(path,ns=(metadata.st_atime_ns,metadata.st_mtime_ns))
    before=state(editor)
    with pytest.raises(ValueError,match='changed'):verified(plan,preview)
    assert state(editor)==before


def test_cancel_before_read_and_during_inspection_and_preview_receipt_not_apply(tmp_path,monkeypatch,geometry):
    editor,plan,preview=ready(tmp_path,geometry=geometry);before=state(editor)
    cancel=Event();cancel.set()
    with pytest.raises(LinkedAVSlipCancelled):verify_linked_av_slip(plan,cancel=cancel)
    with pytest.raises(ValueError,match='fresh verified'):apply_linked_av_slip(editor,plan,preview)
    cancel.clear()
    import lumen.clip_copy_paste as copy_module
    original=copy_module._identity
    def inspect(*args,**kwargs):
        result=original(*args,**kwargs);cancel.set();return result
    monkeypatch.setattr(copy_module,'_identity',inspect)
    with pytest.raises(LinkedAVSlipCancelled):verify_linked_av_slip(plan,cancel=cancel)
    assert state(editor)==before


@pytest.mark.parametrize('mutation',['origin','candidate','role','snapshot','session','revision','token'])
def test_plan_tamper_cannot_rebase_or_change_approved_candidate(tmp_path,mutation,geometry):
    editor,plan,preview=ready(tmp_path,geometry=geometry);receipt=verified(plan,preview)
    if mutation=='origin':bad=replace(plan,source_in='11')
    elif mutation=='candidate':bad=replace(plan,candidate_signature='0'*64)
    elif mutation=='role':bad=replace(plan,clip_id='other')
    elif mutation=='snapshot':bad=replace(plan,source_request=replace(plan.source_request,payload='{}'))
    elif mutation=='session':bad=replace(plan,session_id='other')
    elif mutation=='revision':bad=replace(plan,revision=plan.revision+1)
    else:bad=replace(plan,token='')
    before=state(editor)
    with pytest.raises(ValueError):apply_linked_av_slip(editor,bad,replace(receipt,plan=bad))
    assert state(editor)==before


def test_noop_receipt_consumed_once_preserves_revision_history_and_redo(tmp_path,geometry):
    editor,plan,preview=ready(tmp_path,origin='21/4' if geometry == 'strict' else '161/4',geometry=geometry)
    editor.update_clip('other',gain_db=-6);editor.undo()
    plan=plan_linked_av_slip(editor,'v',str(editor._clip('v').source_in));preview=verify_linked_av_slip(plan);receipt=verified(plan,preview)
    editor.project.master_gain_db=-0.0
    editor._clip('a').gain_db=-4
    raw=wire(editor.project)
    # Capture a fresh signed snapshot after the intentionally noncanonical scalar.
    plan=plan_linked_av_slip(editor,'v',str(editor._clip('v').source_in))
    preview=verify_linked_av_slip(plan);receipt=verified(plan,preview)
    assert wire(editor.project)==raw
    before=state(editor);records=tuple(editor.project.clips)
    apply_linked_av_slip(editor,plan,receipt)
    assert wire(editor.project)==raw and all(a is b for a,b in zip(records,editor.project.clips))
    assert state(editor)==before and editor.can_redo
    with pytest.raises(ValueError,match='already applied'):apply_linked_av_slip(editor,plan,receipt)
    assert state(editor)==before


def test_worker_can_verify_but_owner_only_apply(tmp_path,geometry):
    editor,plan,preview=ready(tmp_path,geometry=geometry);receipts=[];errors=[];before=state(editor)
    def worker():
        receipt=verified(plan,preview);receipts.append(receipt)
        try:apply_linked_av_slip(editor,plan,receipt)
        except ValueError as error:errors.append(str(error))
    thread=Thread(target=worker);thread.start();thread.join()
    assert receipts and errors and 'owning thread' in errors[0] and state(editor)==before


@pytest.mark.parametrize('role', ['v', 'a'])
def test_affine_verified_pipeline_matches_old_request_oracle_for_either_anchor(tmp_path, geometry, role):
    if geometry == 'strict':
        editor=sample(link=True)
    else:
        editor=affine_editor(geometry=geometry)
    path=tmp_path/'both-roles.mov';path.write_bytes(b'bounded immutable source'*4096)
    editor.project.media[0].path=str(path)
    old=deepcopy(editor.project)
    requested=editor._clip(role).source_in+1
    plan=plan_linked_av_slip(editor,role,str(requested))
    preview=verify_linked_av_slip(plan)
    receipt=verify_linked_av_slip(plan,preview)
    before=state(editor)
    if geometry != 'strict':
        changed=replace(plan,clip_id='a' if role == 'v' else 'v')
        with pytest.raises(ValueError,match='candidate changed'):
            apply_linked_av_slip(editor,changed,replace(receipt,plan=changed))
        assert state(editor)==before
    apply_linked_av_slip(editor,plan,receipt)
    assert editor.project==expected_project(old,role,requested)
    assert editor.revision==before[3]+1 and len(editor._undo)==len(before[1])+1
    assert editor.undo() and editor.project==old
    assert editor.redo() and editor.project==expected_project(old,role,requested)
