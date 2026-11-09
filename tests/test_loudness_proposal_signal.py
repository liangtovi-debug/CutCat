"""Real measured proposal -> explicit edit -> render proofs.

The independent frozen mixer and numerical scalar oracle prove that proposals
neither normalize nor clip the mix and that the existing renderer gains once.
AAC is explicitly not the measured pre-encode signal or a peak guarantee.
"""
from copy import deepcopy
import hashlib
import os
from pathlib import Path
import re
from uuid import uuid4

import numpy as np
import pytest

from lumen import audio_analysis as aa
from lumen import loudness_proposal as lp
from lumen.audio_mix import compile_audio_mix
from lumen.model import Clip, Editor, Project
from lumen.project_recovery import RecoveryStore
from lumen.render import RenderEngine
from test_audio_mix import _legacy_graph, _pcm, pcm_source
from test_master_gain_signal import (
    _assert_scaled, _decode_audio, _decode_video, _samples, public_project, real,
)


def _without_master(project):
    result = deepcopy(project.to_dict())
    result.pop('master_gain_db')
    return result


def _same_measurement(actual, expected):
    assert actual.fingerprint == expected.fingerprint
    assert actual.sample_count == expected.sample_count
    assert actual.integrated_lufs == expected.integrated_lufs
    assert actual.overload_count == expected.overload_count
    assert actual.sample_peaks == expected.sample_peaks
    assert actual.estimated_true_peaks == expected.estimated_true_peaks


@real
@pytest.mark.parametrize('case', ['target', 'peak', 'overload'])
def test_measured_proposal_explicit_atomic_commit_and_lossless_signal(pcm_source, tmp_path, case):
    project = Project(width=64, height=48, media=[pcm_source], master_gain_db=-3.25,
                      clips=[Clip('a', pcm_source.id, 2, 0, 0, 60,
                                  gain_db=-4.125, fade_in=3, fade_out=5)])
    if case == 'overload':
        project.master_gain_db = 2.5
        project.clips[0].gain_db = 6
        project.clips.append(Clip('b', pcm_source.id, 3, 0, 0, 60, gain_db=6))
    editor = Editor(project)
    original = deepcopy(project)
    settings = _without_master(project)
    source_bytes = Path(pcm_source.path).read_bytes()
    measured = aa.analyze_audio(project)
    target = measured.integrated_lufs + (2 if case == 'target' else 18)
    ceiling = -1.0
    if case == 'overload':
        target = -24.0
        assert measured.overload_count > 1000
    verified = lp.verify_loudness_proposal(project, measured, target, ceiling)
    proposal = verified.proposal
    assert proposal.peak_limited == (case == 'peak')
    assert proposal.current_master_gain_db == original.master_gain_db
    assert proposal.proposed_master_gain_db == pytest.approx(
        proposal.current_master_gain_db + proposal.delta_db)
    if case == 'overload':
        assert proposal.delta_db < 0
    assert project == original and editor.revision == 0
    assert not editor.can_undo and not editor.can_redo
    assert aa.result_is_current(project, measured)
    assert not aa.result_is_current(project, verified.draft_report)
    lp.validate_verified_proposal(project, verified)
    assert project == original and editor.revision == 0

    # Committing is explicitly separate from computing or verifying a draft.
    editor.set_master_gain(proposal.proposed_master_gain_db)
    committed = deepcopy(editor.project)
    assert editor.revision == 1 and len(editor._undo) == 1
    assert _without_master(committed) == settings
    assert not aa.result_is_current(committed, measured)
    assert aa.result_is_current(committed, verified.draft_report)
    after = aa.analyze_audio(committed)
    _same_measurement(after, verified.draft_report)
    assert max(after.estimated_true_peak_dbtp) <= ceiling
    assert after.overload_count == 0
    assert after.integrated_lufs <= target + .11
    if case == 'target':
        assert after.integrated_lufs == pytest.approx(target, abs=.11)

    inputs, graph = _legacy_graph(original, {pcm_source.id: pcm_source})
    frozen = _samples(_pcm(inputs, graph))
    current_mix = compile_audio_mix(committed, {pcm_source.id: pcm_source})
    actual = _samples(_pcm(current_mix.inputs, current_mix.graph))
    _assert_scaled(actual, frozen, proposal.proposed_master_gain_db)
    assert current_mix.graph.count(':precision=float') == 1
    if case == 'overload':
        overloaded = np.abs(frozen) > 1
        assert overloaded.sum() > 1000
        clipped = np.clip(frozen, -1, 1) * np.float32(10**(proposal.proposed_master_gain_db / 20))
        assert np.max(np.abs(actual[overloaded] - clipped[overloaded])) > .01
        assert len(np.unique(actual[overloaded])) > 100

    path = tmp_path / 'approved.lumen'
    committed.save(path)
    assert Project.load(path) == committed
    with RecoveryStore(tmp_path / 'recovery') as store:
        assert store.load(store.write(committed, uuid4())) == committed
    assert editor.undo() and editor.project == original
    assert not editor.can_undo
    assert editor.redo() and editor.project == committed
    assert Path(pcm_source.path).read_bytes() == source_bytes


@real
@pytest.mark.parametrize('mode', ['preview', 'h264', 'prores'])
def test_measured_proposal_public_render_once_video_unchanged_and_aac_distinct(
        public_project, tmp_path, monkeypatch, mode):
    project = public_project
    project.master_gain_db = -5.25
    project.clips[0].gain_db = -1.125
    project.clips[0].fade_in = 2
    project.clips[0].fade_out = 3
    editor = Editor(project)
    original = deepcopy(project)
    source_hash = hashlib.sha256(Path(project.media[0].path).read_bytes()).digest()
    report = aa.analyze_audio(project)
    verified = lp.verify_loudness_proposal(project, report, report.integrated_lufs + 3, -1)
    assert not verified.proposal.peak_limited
    assert editor.revision == 0 and project == original
    engine = RenderEngine(timeout=60)
    compiled = []
    original_compile = engine._compile
    def capture(*args, **kwargs):
        result = original_compile(*args, **kwargs)
        compiled.append(result)
        return result
    monkeypatch.setattr(engine, '_compile', capture)
    def render(label):
        if mode == 'preview':
            return engine.preview(editor.project)
        return engine.render(editor.project,
                             tmp_path / (label + ('.mov' if mode == 'prores' else '.mp4')),
                             export_profile='prores_422_hq_10bit' if mode == 'prores' else 'h264_8bit')
    try:
        baseline = render('baseline')
        baseline_video = _decode_video(baseline)
        lp.validate_verified_proposal(editor.project, verified)
        editor.set_master_gain(verified.proposal.proposed_master_gain_db)
        output = render('approved')
        assert len(compiled) == 2
        assert _decode_video(output) == baseline_video
        old_inputs, old_graph, old_duration = compiled[0]
        new_inputs, new_graph, new_duration = compiled[1]
        assert old_inputs == new_inputs and old_duration == new_duration
        assert old_graph.count(':precision=float') == new_graph.count(':precision=float') == 1
        assert re.sub(r',volume=[-+0-9.e]+dB:precision=float', '', new_graph) == re.sub(
            r',volume=[-+0-9.e]+dB:precision=float', '', old_graph)
        old_pcm = _samples(_pcm(old_inputs, old_graph, video_output=os.devnull))
        new_pcm = _samples(_pcm(new_inputs, new_graph, video_output=os.devnull))
        _assert_scaled(new_pcm, old_pcm, verified.proposal.delta_db)
        assert len(new_pcm) == verified.draft_report.sample_count == 48048
        _same_measurement(aa.analyze_audio(editor.project), verified.draft_report)
        decoded_aac = _decode_audio(output)
        assert len(decoded_aac) >= len(new_pcm)
        body = slice(4096, len(new_pcm) - 4096)
        actual, reference = decoded_aac[body], new_pcm[body]
        # This measured ceiling describes pre-encode PCM only. No encoded AAC
        # sample or true-peak ceiling assertion is appropriate here.
        assert np.mean(np.abs(actual - reference)) > 1e-6
        relative_error = np.sqrt(np.mean((actual-reference)**2, axis=0)) / np.sqrt(
            np.mean(reference**2, axis=0))
        assert np.max(relative_error) < .04
        assert _without_master(editor.project) == _without_master(original)
        assert len(editor._undo) == 1
        assert editor.undo() and editor.project == original
        if mode == 'preview':
            assert engine.preview(editor.project) == baseline
            assert len(compiled) == 2
    finally:
        engine.close()
    assert hashlib.sha256(Path(project.media[0].path).read_bytes()).digest() == source_hash
