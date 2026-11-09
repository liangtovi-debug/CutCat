"""Independent proposal arithmetic and stale/actual-measurement authority."""
from copy import deepcopy
from dataclasses import FrozenInstanceError, replace
import math
import threading
import wave

import numpy as np
import pytest

from lumen import audio_analysis as aa
from lumen import loudness_proposal as lp
from lumen.model import Clip, Media, Project


def report(lufs=-20., peak=-6.):
    amplitude = 10 ** (peak / 20)
    return aa.AudioAnalysisResult(lufs, 'measured', (amplitude, amplitude), (peak, peak),
                                  (0, 0), 0, None, 96000, 48000, 'f'*64, 'c'*64,
                                  estimated_true_peaks=(amplitude, amplitude),
                                  estimated_true_peak_dbtp=(peak, peak))


@pytest.mark.parametrize('master,target,ceiling,expected,limited', [
    (0, -14, -1, 5., True), (3, -18, -1, 5., False),
    (-3, -18, -1, -1., False), (0, -20.01, -1, -.1, False),
    (-1.25, -20, -1, -1.3, False), (0, -19.7, -1, .3, False),
    (0, -14, -12.01, -6.1, True), (24, -21, -1, 23., False),
])
def test_independent_decimal_math(master, target, ceiling, expected, limited):
    p = lp.propose_master_gain(report(), master, target, ceiling)
    assert p.proposed_master_gain_db == expected
    assert p.delta_db == pytest.approx(expected-master)
    assert p.peak_limited == limited
    assert p.original_lufs == -20 and p.original_peak_dbtp == -6
    with pytest.raises(FrozenInstanceError):
        p.delta_db = 0


@pytest.mark.parametrize('master,target,ceiling', [(24, -19.99, 0), (-96, -20.01, 0),
                                                  (25, -20, 0), (-97, -20, 0), (0, 1e308, 1e308)])
def test_out_of_range_is_rejected_not_clamped(master, target, ceiling):
    with pytest.raises(lp.LoudnessProposalError, match='outside'):
        lp.propose_master_gain(report(), master, target, ceiling)


@pytest.mark.parametrize('value', [float('nan'), float('inf'), -float('inf'), True, None, '1'])
@pytest.mark.parametrize('field', ['current_master_gain_db', 'target_lufs', 'ceiling_dbtp'])
def test_bad_inputs(value, field):
    kwargs = dict(current_master_gain_db=0, target_lufs=-14, ceiling_dbtp=-1)
    kwargs[field] = value
    with pytest.raises(lp.LoudnessProposalError):
        lp.propose_master_gain(report(), **kwargs)


@pytest.mark.parametrize('changes', [
    {'algorithm':'old'}, {'loudness_status':'silence'}, {'loudness_status':'below_gate'},
    {'loudness_status':'too_short'}, {'loudness_status':'unknown'}, {'integrated_lufs':None},
    {'integrated_lufs':float('nan')}, {'sample_count':100}, {'sample_rate':44100},
    {'estimated_true_peak_dbtp':(None,None)}, {'estimated_true_peak_dbtp':(-5.,-6.)},
    {'estimated_true_peaks':(-1.,1.)}, {'estimated_true_peak_dbtp':(float('inf'),-6.)},
])
def test_unusable_measurements(changes):
    with pytest.raises(lp.LoudnessProposalError):
        lp.propose_master_gain(replace(report(), **changes), 0, -14, -1)


def make_project(tmp_path, values=None):
    if values is None:
        values = .1*np.sin(2*np.pi*1000*np.arange(96000)/48000)
    path = tmp_path/'tone.wav'
    with wave.open(str(path), 'wb') as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(48000)
        output.writeframes(np.rint(values*32767).astype('<i2').tobytes())
    seconds = len(values)/48000
    return Project(media=[Media('m',str(path),'tone',seconds,has_video=False,has_audio=True)],
                   clips=[Clip('c','m',2,0,0,round(seconds*30))])


@pytest.fixture
def measured(tmp_path):
    project = make_project(tmp_path)
    return project, aa.analyze_audio(project)


@pytest.mark.parametrize('change', ['fingerprint','algorithm','source_hashes','decoder_version','context_fingerprint'])
def test_forged_identity_even_with_valid_context(measured, monkeypatch, change):
    p, r = measured
    value = (('m','0'*64),) if change == 'source_hashes' else '0'*64
    forged = replace(r, **{change:value})
    monkeypatch.setattr(aa, 'analyze_audio', lambda *a, **k: pytest.fail('must reject before draft'))
    with pytest.raises(lp.LoudnessProposalError):
        lp.verify_loudness_proposal(p, forged, -16, -1)


def test_real_draft_and_preapply_are_detached(measured):
    p, r = measured
    before = deepcopy(p)
    verified = lp.verify_loudness_proposal(p, r, -16, -1)
    assert p == before
    assert verified.draft_report.integrated_lufs == pytest.approx(-16, abs=.15)
    assert verified.draft_report.fingerprint != r.fingerprint
    assert verified.draft_report.source_hashes == r.source_hashes
    lp.validate_verified_proposal(p, verified)
    p.master_gain_db = 1
    with pytest.raises(lp.LoudnessProposalError, match='timeline changed'):
        lp.validate_verified_proposal(p, verified)


@pytest.mark.parametrize('stage', ['baseline_verify','snapshot','decode','verify','proposal_verify'])
def test_cancel_at_each_stage_preserves_project(measured, stage):
    p,r = measured
    before = deepcopy(p)
    cancel = threading.Event()
    def progress(value):
        if stage == value:
            cancel.set()
    with pytest.raises(aa.AudioAnalysisCancelled):
        lp.verify_loudness_proposal(p,r,-16,-1,cancel,progress=progress)
    assert p == before


def test_source_mutation_between_analysis_and_publish_rejected(measured):
    p,r = measured
    def progress(value):
        if value == 'proposal_verify':
            with open(p.media[0].path,'r+b') as stream:
                stream.seek(100)
                stream.write(b'changed!')
    with pytest.raises(aa.AudioAnalysisError):
        lp.verify_loudness_proposal(p,r,-16,-1,progress=progress)


def test_wrong_draft_gain_identity_is_not_accepted(measured,monkeypatch):
    p,r = measured
    monkeypatch.setattr(aa,'analyze_audio',lambda *a,**k:r)
    with pytest.raises(lp.LoudnessProposalError,match='changed'):
        lp.verify_loudness_proposal(p,r,-16,-1)


def test_actual_peak_above_ceiling_is_rejected(measured,monkeypatch):
    p,r = measured
    original = aa.analyze_audio
    def analyze(*args,**kwargs):
        draft = original(*args,**kwargs)
        return replace(draft,estimated_true_peaks=(2.,2.),
                       estimated_true_peak_dbtp=(20*math.log10(2),)*2)
    monkeypatch.setattr(aa,'analyze_audio',analyze)
    with pytest.raises(lp.LoudnessProposalError,match='exceeds'):
        lp.verify_loudness_proposal(p,r,-16,-1)


def test_absolute_gate_requires_actual_remeasurement(tmp_path):
    # Two 3-second regions separated by a 10 dB step. Initially both survive
    # the absolute gate; attenuation removes the quieter region and integrated
    # LUFS is no longer original LUFS + proposed delta.
    phase = np.sin(2*np.pi*1000*np.arange(288000)/48000)
    levels = np.r_[np.full(144000,.0006),np.full(144000,.002)]
    p = make_project(tmp_path, phase*levels)
    r = aa.analyze_audio(p)
    verified = lp.verify_loudness_proposal(p,r,-68,-1)
    linear_prediction = r.integrated_lufs + verified.proposal.delta_db
    assert abs(verified.draft_report.integrated_lufs-linear_prediction) > 1
    assert verified.draft_report.integrated_lufs != pytest.approx(-68,abs=.2)


def test_draft_falling_below_gate_cannot_be_applied(measured):
    p,r = measured
    with pytest.raises(lp.LoudnessProposalError,match='measured loudness'):
        lp.verify_loudness_proposal(p,r,-90,-1)


@pytest.mark.parametrize('changes', [
    {'sample_peaks':[.5,.5]}, {'sample_peak_dbfs':(0.,0.)},
    {'overload_counts':(1,0)}, {'overload_count':1}, {'first_overload_seconds':.1},
    {'sample_peaks':(float('nan'),.5)}, {'estimated_true_peak_dbtp':[-6.,-6.]},
])
def test_mutable_or_inconsistent_measurement_rejected(changes):
    with pytest.raises(lp.LoudnessProposalError):
        lp.propose_master_gain(replace(report(),**changes),0,-14,-1)


def test_owner_timeline_edit_during_draft_is_not_published(measured):
    p,r=measured
    def progress(stage):
        if stage=='snapshot':
            p.master_gain_db=1
    with pytest.raises(lp.LoudnessProposalError,match='changed'):
        lp.verify_loudness_proposal(p,r,-16,-1,progress=progress)
    assert p.master_gain_db==1  # never roll back or overwrite user's change


def test_budget_covers_draft_and_final_hashing(measured,monkeypatch):
    p,r=measured
    original=aa.analyze_audio
    def analyze(*args,**kwargs):
        result=original(*args,**kwargs)
        monkeypatch.setattr(aa.time,'monotonic',lambda:1e100)
        return result
    monkeypatch.setattr(aa,'analyze_audio',analyze)
    with pytest.raises(aa.AudioAnalysisError,match='timed out'):
        lp.verify_loudness_proposal(p,r,-16,-1)


def test_cancelled_preapply_does_not_mutate(measured):
    p,r=measured
    result=lp.verify_loudness_proposal(p,r,-16,-1)
    before=deepcopy(p)
    cancel=threading.Event()
    cancel.set()
    with pytest.raises(aa.AudioAnalysisCancelled):
        lp.validate_verified_proposal(p,result,cancel)
    assert p==before


def test_wrong_draft_fingerprint_rejected_at_preapply(measured):
    p,r=measured
    result=lp.verify_loudness_proposal(p,r,-16,-1)
    altered=replace(result,draft_report=replace(result.draft_report,fingerprint='0'*64))
    with pytest.raises(lp.LoudnessProposalError,match='fingerprint'):
        lp.validate_verified_proposal(p,altered)


def test_gain_tampering_cannot_reuse_measurement(measured):
    p,r=measured
    result=lp.verify_loudness_proposal(p,r,-16,-1)
    altered=replace(result,proposal=replace(result.proposal,proposed_master_gain_db=0))
    with pytest.raises(lp.LoudnessProposalError,match='gain'):
        lp.validate_verified_proposal(p,altered)


@pytest.mark.parametrize('field,limit,message', [
    ('max_source_bytes', 8, 'file-size limit'),
    ('max_total_source_bytes', 8, 'size limit'),
    ('max_sources', 1, 'verification limits'),
])
@pytest.mark.parametrize('phase', ['proposal', 'preapply'])
def test_real_resource_limits_are_enforced(tmp_path,field,limit,message,phase):
    p=make_project(tmp_path)
    # Two active media identities, sharing a file. Source count must count
    # identities, while the bounded content reads remain cancellable.
    p.media.append(replace(p.media[0],id='second'))
    p.clips.append(replace(p.clips[0],id='second_clip',media_id='second',track=3))
    r=aa.analyze_audio(p)
    before=deepcopy(p)
    limits=replace(aa.AnalysisLimits(),**{field:limit})
    if phase=='proposal':
        operation=lambda:lp.verify_loudness_proposal(p,r,-16,-1,limits=limits)
    else:
        result=lp.verify_loudness_proposal(p,r,-16,-1)
        operation=lambda:lp.validate_verified_proposal(p,result,limits=limits)
    with pytest.raises(aa.AudioAnalysisError,match=message):
        operation()
    assert p==before


def test_cancellation_inside_real_snapshot_copy_removes_partial_file(measured,monkeypatch):
    from pathlib import Path
    p,r=measured
    before=deepcopy(p)
    cancel=threading.Event()
    directories=[]
    written=[]
    original_directory=aa.tempfile.TemporaryDirectory
    original_copy=aa._copy_hash
    def directory(*args,**kwargs):
        value=original_directory(*args,**kwargs)
        directories.append(Path(value.name))
        return value
    def copy(source,output,budget,max_bytes,**kwargs):
        if output is None:
            return original_copy(source,output,budget,max_bytes,**kwargs)
        class CancelAfterWrite:
            def write(self,block):
                count=output.write(block)
                output.flush()
                written.append((Path(output.name),count))
                assert Path(output.name).stat().st_size>0
                cancel.set()
                return count
        return original_copy(source,CancelAfterWrite(),budget,max_bytes,**kwargs)
    monkeypatch.setattr(aa.tempfile,'TemporaryDirectory',directory)
    monkeypatch.setattr(aa,'_copy_hash',copy)
    with pytest.raises(aa.AudioAnalysisCancelled):
        lp.verify_loudness_proposal(p,r,-16,-1,cancel)
    assert written and directories
    assert all(not path.exists() for path in directories)
    assert all(not path.exists() for path,count in written)
    assert not aa._SNAPSHOT_LOCK.locked()
    assert p==before


def test_low_snapshot_disk_space_fails_without_mutation(measured,monkeypatch):
    from types import SimpleNamespace
    from pathlib import Path
    p,r=measured
    before=deepcopy(p)
    directories=[]
    original_directory=aa.tempfile.TemporaryDirectory
    def directory(*args,**kwargs):
        value=original_directory(*args,**kwargs)
        directories.append(Path(value.name))
        return value
    monkeypatch.setattr(aa.tempfile,'TemporaryDirectory',directory)
    monkeypatch.setattr(aa.shutil,'disk_usage',lambda _path:SimpleNamespace(free=0))
    with pytest.raises(aa.AudioAnalysisError,match='free disk space'):
        lp.verify_loudness_proposal(p,r,-16,-1)
    assert directories and all(not path.exists() for path in directories)
    assert not aa._SNAPSHOT_LOCK.locked()
    assert p==before
