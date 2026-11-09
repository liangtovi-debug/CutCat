#!/usr/bin/env python3
"""Extended real local Kokoro v1.1 acceptance; no download or mock synthesis.

Supply an already audited local pack and runtime. For network-denied evidence,
execute this script inside scripts/run_network_denied.py and retain that
harness's log. Seven additional catalog IDs receive original Mandarin text;
sid 1 also receives English, and sid 30 receives a genuinely longer paragraph.

Real Qt actions generate two consecutive subtitle takes. An occupied audio lane
must preserve the pending WAV, an explicit trim makes the earlier clip touch the
next cue, and retry must reuse that same take without another synthesis. A third
alternate take separately verifies free-other-lane insertion and undo/redo. The
final two-take project is saved, reopened, relocated and rendered using the real
shared preview/export graph, with independently assembled PCM timing evidence.

Only file choosers, message-box answers, settings isolation and transparent
subprocess observation are automated. No inference/render/playback substitute is
used. These checks do not establish spoken accuracy, voice quality, physical
speaker playback, all-103-voice coverage, or native Windows/macOS acceptance.
"""
from __future__ import annotations

import argparse
from contextlib import ExitStack
import copy
from dataclasses import asdict
from fractions import Fraction
import json
import math
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import time
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.verify_tts_setup_integration import (
    AcceptanceError, PACK_VERSION, TTSDialogs, TTSProcessRecorder, compare_pcm,
    decode_pcm, local_pack, media_evidence, pack_evidence, require,
    runtime_evidence, screenshot, sha256, wait_until, waveform_evidence,
)

VOICE_IDS = (1, 10, 30, 57, 59, 80, 102)
SHORT_ZH = '清晨的风吹过街角，我们沿着河岸继续向前。'
SHORT_EN = 'Morning light reaches the river, and our next scene begins.'
LONG_VOICE_ID = 30
LONG_ZH = (
    '今天，我们带着相机来到城外的小河边，准备记录一段平凡却值得记住的旅程。'
    '清晨的阳光穿过树叶，落在石桥和缓缓流动的水面上。'
    '有人停下脚步听鸟鸣，也有人把远处的山影画进随身的小本子。'
    '拍摄时不用急着切换镜头，可以先让环境的声音自然展开，再慢慢靠近想要讲述的细节。'
    '等到傍晚回看这些片段，我们会发现，真正连接画面的，是一路上的观察、等待和彼此分享的心情。'
    '最后，请把完整的声音保留下来，让故事在安静的河岸边从容结束。'
)
TAKE_TEXTS = (
    '请沿着河岸向前走，下一段画面就在桥边开始。',
    '现在把镜头转向远处，让声音陪着画面继续。',
)
TAKE_VOICE_IDS = (10, 59)
ALTERNATE_VOICE_ID = 80
CAPTION_FRAMES = 30
TRIM_SOURCE_IN = 3
SOURCE_FILES = (
    'scripts/verify_tts_extended_workflow.py', 'scripts/verify_tts_setup_integration.py',
    'scripts/verify_asr_setup_workflow.py', 'scripts/run_network_denied.py',
    'lumen/tts.py', 'lumen/tts_worker.py', 'lumen/voice_catalog.py', 'lumen/ui.py',
    'lumen/ui_captions.py', 'lumen/ui_component_setup.py', 'lumen/component_setup.py',
    'lumen/model.py', 'lumen/render.py', 'lumen/audio_timing.py', 'lumen/timing.py',
)


def audition_specs():
    """Exact catalog IDs and original test text; no inferred quality labels."""
    return [
        *[{'name': f'zh-sid{sid}', 'voice_id': sid, 'language': 'zh', 'text': SHORT_ZH,
           'long_passage': False} for sid in VOICE_IDS],
        {'name': 'en-sid1', 'voice_id': 1, 'language': 'en', 'text': SHORT_EN, 'long_passage': False},
        {'name': f'zh-long-sid{LONG_VOICE_ID}', 'voice_id': LONG_VOICE_ID, 'language': 'zh',
         'text': LONG_ZH, 'long_passage': True},
    ]


class ExtendedTTSDialogs(TTSDialogs):
    """Permit only explicitly queued overlap notices for the exact saved WAV."""
    def __init__(self):
        super().__init__()
        self.overlap_paths = []

    def message(self, kind, parent, title, text, *args, **kwargs):
        from lumen.i18n import tr
        if (kind == 'warning' and str(title) == tr('Audio lane overlap')
                and self.overlap_paths and str(self.overlap_paths[0]) in str(text)):
            self.overlap_paths.pop(0)
            return super().message('expected_overlap', parent, title, text, *args, **kwargs)
        return super().message(kind, parent, title, text, *args, **kwargs)


def editing_state(window):
    """Auditions and rejected insertions must preserve complete editor history."""
    return (copy.deepcopy(window.project.to_dict()), window.revision, window.dirty,
            copy.deepcopy(window.editor._undo), copy.deepcopy(window.editor._redo))


def checked_worker(recorder, before, text, voice_id, model_pack):
    records = [item for item in recorder.evidence() if item['is_tts_worker']][before:]
    require(len(records) == 1 and records[0]['returncode'] == 0,
            'The operation did not finish exactly one successful real TTS worker.')
    record = records[0]
    request = record['request']
    require(request['text'] == text and request['voice_id'] == voice_id
            and request['pack_version'] == PACK_VERSION and request['speed'] == 1.0
            and request['threads'] == 2 and Path(request['model_dir']) == model_pack,
            'Worker did not receive the complete original text and exact requested settings.')
    return {'pid': record['pid'], 'returncode': record['returncode'],
            'complete_text_received': True, 'request_character_count': len(text)}


def check_provenance(provenance, text, voice_id, path, report, caption=None):
    require(provenance['text'] == text and provenance['voice_id'] == str(voice_id)
            and provenance['output_sha256'] == sha256(path)
            and provenance['engine'] == 'sherpa-onnx/Kokoro-v1.1'
            and provenance['engine_version'] == report['runtime']['packages']['sherpa-onnx']['version']
            and provenance['model_sha256'] == report['pack']['assets']['model.onnx']
            and provenance['voice_sha256'] == report['pack']['assets']['voices.bin']
            and provenance['assets_sha256'] == report['pack']['assets_sha256']
            and provenance['sample_rate'] == 24000 and provenance['speed'] == 1.0,
            'Provenance differs from the complete text, saved WAV or audited pack/runtime.')
    if caption is not None:
        require(provenance['source_caption_id'] == caption.id
                and provenance['source_start'] == caption.start
                and provenance['source_duration'] == caption.duration,
                'The selected caption snapshot was not preserved in immutable provenance.')


def timeline_reference(project, source_pcm):
    """Independently assemble the selected original PCM at exact timeline edges.

    This is a comparison reference only, never a rendered output or synthesis
    substitute. Deliberately support only the runner's speed-1, zero-offset,
    unmuted/no-fade cases, rejecting unsupported settings rather than guessing.
    The compiler's plan is checked against separate frame/sample arithmetic.
    """
    import numpy as np
    from lumen.audio_timing import compile_audio_timing
    from lumen.timing import sample_boundary

    budget = sample_boundary(project.duration_frames, project.fps, 48000)
    require(project.clips and budget > 0, 'PCM reference needs a nonempty timeline.')
    expected = np.zeros(budget, dtype=np.float64)
    plans = []
    for clip in project.clips:
        media = next(m for m in project.media if m.id == clip.media_id)
        require(clip.speed == 1 and not clip.muted and not clip.fade_in and not clip.fade_out
                and media.audio_offset == 0 and media.has_audio and clip.track in (2, 3),
                'Reference assembly supports only the explicit ordinary audio edits in this runner.')
        source = np.asarray(source_pcm[clip.media_id], dtype=np.float64)
        require(source.ndim == 1 and len(source) and np.isfinite(source).all(),
                'Reference source PCM must be a nonempty finite mono array.')
        start = sample_boundary(clip.start, project.fps, 48000)
        end = sample_boundary(clip.end, project.fps, 48000)
        def source_sample(frame):
            value = Fraction(frame) * 48000 / project.fps
            return (2 * value.numerator + value.denominator) // (2 * value.denominator)
        source_start = source_sample(clip.source_in)
        source_end = source_sample(clip.source_out)
        plan = compile_audio_timing(clip, project.fps, media.audio_offset)
        require((plan.start_sample, plan.sample_count, plan.source_start_sample, plan.source_end_sample,
                 plan.leading_samples, plan.silent) == (start, end - start, source_start, source_end, 0, False),
                'Compiled audio timing differs from independently calculated sample boundaries.')
        selected = source[source_start:source_end]
        require(0 <= start < end <= budget and len(selected) <= end - start and len(selected) > 0,
                'Selected original PCM exceeds its timeline range or contains no samples.')
        gain = 10 ** (clip.gain_db / 20)
        expected[start:start + len(selected)] += selected * gain
        plans.append({'clip_id': clip.id, 'media_id': clip.media_id, 'track': clip.track,
                      'source_pcm_samples': len(source), 'selected_source_samples': len(selected),
                      'source_tail_padding_samples': end - start - len(selected),
                      'gain_db': clip.gain_db, 'plan': asdict(plan)})
    return expected, plans


def compare_timeline_pcm(project, source_pcm, output):
    expected, plans = timeline_reference(project, source_pcm)
    start = min(item['plan']['start_sample'] for item in plans)
    result = compare_pcm(expected[start:], output, start, len(expected))
    result['reference_method'] = 'independent original-PCM source selection, gain and integer-sample placement'
    result['clips'] = plans
    return result


def source_evidence():
    root = Path(__file__).resolve().parents[1]
    return {name: sha256(root / name) for name in SOURCE_FILES}


def run_workflow(args, report):
    os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
    from PySide6 import __version__ as qt_version
    from PySide6.QtCore import QSettings, QStandardPaths, QUrl
    from PySide6.QtMultimedia import QMediaDevices
    from PySide6.QtWidgets import QApplication, QFileDialog, QMessageBox
    from lumen.component_setup import load_preferences
    from lumen.model import Editor, Project
    from lumen.ui import MainWindow
    from lumen.ui_component_setup import ComponentSetupDialog, operational_settings
    from lumen.voice_catalog import get_voice
    import numpy as np

    folder = args.output_dir
    app = QApplication.instance() or QApplication([])
    QSettings.setDefaultFormat(QSettings.Format.IniFormat)
    for scope in (QSettings.Scope.UserScope, QSettings.Scope.SystemScope):
        QSettings.setPath(QSettings.Format.IniFormat, scope, str(folder / 'settings'))
    workspace = QSettings(str(folder / 'settings' / 'workspace.ini'), QSettings.Format.IniFormat)
    shortcuts = QSettings(str(folder / 'settings' / 'shortcuts.ini'), QSettings.Format.IniFormat)
    dialogs, recorder = ExtendedTTSDialogs(), TTSProcessRecorder(folder)
    report.update(qt_binding=qt_version, qt_platform=app.platformName(), ui_language=args.ui_language,
                  audio_output_devices=[device.description() for device in QMediaDevices.audioOutputs()],
                  automation_boundaries=['explicit local file chooser answers', 'explicit overflow No/Yes answers',
                                         'exact-path expected overlap notices', 'isolated settings',
                                         'transparent subprocess observation', 'observation of real Qt result signals'],
                  screenshots=[], cases=[], saved_takes=[])
    original_location = QStandardPaths.writableLocation
    def location(kind):
        return str(folder / 'settings') if kind == QStandardPaths.StandardLocation.AppConfigLocation else original_location(kind)
    window = None
    with ExitStack() as stack:
        stack.enter_context(patch.object(QStandardPaths, 'writableLocation', location))
        stack.enter_context(patch.object(QFileDialog, 'getOpenFileName', dialogs.open))
        stack.enter_context(patch.object(QFileDialog, 'getSaveFileName', dialogs.save))
        stack.enter_context(patch.object(QMessageBox, 'warning', lambda *a, **k: dialogs.message('warning', *a, **k)))
        stack.enter_context(patch.object(QMessageBox, 'information', lambda *a, **k: dialogs.message('information', *a, **k)))
        stack.enter_context(patch.object(QMessageBox, 'question', dialogs.question))
        stack.enter_context(patch.object(subprocess, 'Popen', recorder))
        try:
            window = MainWindow(workspace_settings=workspace, shortcut_settings=shortcuts, ui_language=args.ui_language)
            window.editor = Editor(Project(name='Extended real local speech acceptance', width=640, height=360))
            cues = [window.editor.add_caption(args.timeline_start + i * CAPTION_FRAMES, CAPTION_FRAMES, text)
                    for i, text in enumerate(TAKE_TEXTS)]
            window.project.caption_style.font_family = args.font_family
            window.project.caption_style.font_size = 22
            window.changed(); window.select_caption(cues[0].id)
            window.resize(1440, 1000); window.show(); app.processEvents()
            baseline = editing_state(window)
            def settle(description):
                wait_until(app, lambda: not window.jobs, args.timeout, description)
                dialogs.require_no_warnings()
            def unchanged(expected=baseline):
                require(editing_state(window) == expected, 'An audition/rejected insertion mutated the project or editor history.')
            def choose_voice(sid):
                index = window.tts_voice.findData(sid)
                require(index >= 0, f'Documented voice {sid} is not in the verified browser.')
                window.tts_voice.setCurrentIndex(index)
                window.tts_speed.setValue(1.0)

            report['stage'] = 'actual Qt component setup and full-pack verification'
            setup = ComponentSetupDialog(window, path=folder / 'settings' / 'component-setup.json')
            setup.fields['tts_model_dir'].setText(str(args.model_pack))
            setup.pack.setCurrentIndex(setup.pack.findData(PACK_VERSION)); setup.hashes.setChecked(True)
            setup.show(); setup.check_button.click(); settle(report['stage'])
            require(setup._report is not None, 'Component metadata diagnostics returned no report.')
            diagnostics = asdict(setup._report)
            checks = {item['key']: item for item in diagnostics['checks'] if item['component'] == 'tts'}
            require(not diagnostics['inference_tested'] and not recorder.workers()
                    and checks['model.onnx']['status'] == checks['voices.bin']['status'] == 'hash_verified'
                    and all(item['status'] in ('metadata_available', 'hash_verified') for item in checks.values()),
                    'Component metadata verification failed or unexpectedly ran inference.')
            setup.apply_button.click()
            require(load_preferences(setup.path)['tts_model_dir'] == str(args.model_pack)
                    and operational_settings(window)['tts_pack_version'] == PACK_VERSION
                    and operational_settings(window)['tts_model_dir'] == str(args.model_pack),
                    'The exact existing pack was not saved and applied.')
            report['diagnostics'] = diagnostics
            report['screenshots'].append(screenshot(app, setup, folder, '01-component-setup.png')); setup.reject()
            window.tts_verify_button.click(); settle(report['stage'])
            require(window._tts_verified_pack == window._tts_pack_key()
                    and window.tts_preview_button.isEnabled() and not recorder.workers(),
                    'Full pack verification failed or unexpectedly synthesized speech.')
            unchanged()
            window.side_tabs.setCurrentIndex(1)
            window.side_tabs.widget(1).ensureWidgetVisible(window.tts_preview_status)

            for spec in audition_specs():
                report['stage'] = 'real Qt audition ' + spec['name']
                choose_voice(spec['voice_id']); window.tts_preview_text.setPlainText(spec['text'])
                results, count, began = [], len(recorder.workers()), time.monotonic()
                window.tts_preview_button.click()
                require(window.tts_job is not None, 'The Qt audition did not start a real task.')
                window.tts_job.result.connect(results.append)
                settle(report['stage'])
                worker = checked_worker(recorder, count, spec['text'], spec['voice_id'], args.model_pack)
                require(len(results) == 1 and window.tts_preview_path is not None,
                        'The real audition did not return one complete WAV result.')
                result = asdict(results[0]); source = Path(window.tts_preview_path)
                check_provenance(result['provenance'], spec['text'], spec['voice_id'], source, report)
                destination = folder / (spec['name'] + '.wav'); shutil.copyfile(source, destination)
                waveform = waveform_evidence(destination, folder / (spec['name'] + '-waveform.json'))
                require(result['duration'] == waveform['duration_seconds'], 'Audition duration differs from actual WAV samples.')
                record = {**spec, 'voice_name': get_voice(PACK_VERSION, spec['voice_id']).name,
                          'original_text': spec['text'], 'elapsed_seconds': time.monotonic() - began,
                          'worker': worker, 'provenance': result['provenance'], 'waveform': waveform,
                          'physical_audio_output_verified': False}
                report['cases'].append(record)
                window.tts_preview_play_button.click(); app.processEvents(); window.tts_preview_stop_button.click()
                unchanged(); record['audition_replay_stop_preserved_project_history'] = True
                if spec['long_passage']:
                    short = next(case for case in report['cases'] if case['name'] == f'zh-sid{LONG_VOICE_ID}')
                    ratio = waveform['sample_count'] / short['waveform']['sample_count']
                    require(ratio > 3, 'The long passage did not produce substantially longer actual audio.')
                    record['duration_ratio_to_same_voice_short_case'] = ratio
                    report['screenshots'].append(screenshot(app, window, folder, '02-long-passage-audition.png'))
            require(len(report['cases']) == len(audition_specs()), 'Some requested real audition cases did not run.')
            window.close_tts_preview()

            original = folder / 'project-original'; (original / 'assets').mkdir(parents=True)
            take_paths = [original / 'assets' / f'take-{i + 1}.wav' for i in range(2)]
            def generate_take(cue, sid, path, *, overlap=False):
                window.select_caption(cue.id); choose_voice(sid)
                window.tts_track.setCurrentIndex(window.tts_track.findData(args.track))
                dialogs.save_paths.append(path)
                if overlap: dialogs.overlap_paths.append(path)
                else: dialogs.overflow_answers.append(True)
                results, count, began = [], len(recorder.workers()), time.monotonic()
                window.tts_button.click()
                require(window.tts_job is not None, 'Saving the take did not start a real task.')
                window.tts_job.result.connect(results.append)
                settle(report['stage'])
                worker = checked_worker(recorder, count, cue.text, sid, args.model_pack)
                require(len(results) == 1 and path.is_file(), 'No actual durable saved take was returned.')
                media = results[0]
                provenance = asdict(media.speech_provenance)
                check_provenance(provenance, cue.text, sid, path, report, cue)
                waveform = waveform_evidence(path, folder / (path.stem + '-waveform.json'))
                require(media.duration == waveform['duration_seconds'], 'Saved take duration differs from complete WAV samples.')
                record = {'name': path.stem, 'voice_id': sid, 'voice_name': get_voice(PACK_VERSION, sid).name,
                          'original_text': cue.text, 'elapsed_seconds': time.monotonic() - began,
                          'worker': worker, 'provenance': provenance, 'waveform': waveform}
                report['saved_takes'].append(record)
                return media

            report['stage'] = 'first full caption take insertion'
            first_media = generate_take(cues[0], TAKE_VOICE_IDS[0], take_paths[0])
            first = window.selected_clip()
            require(first is not None and first.start == cues[0].start and first.track == args.track
                    and first.duration == window.project.media_frames(first_media)
                    and first.source_in == 0 and first.speed == 1 and first.end > cues[1].start
                    and first.duration > CAPTION_FRAMES + TRIM_SOURCE_IN,
                    'First take was not inserted whole or cannot exercise a genuine overlap and explicit source trim.')
            require(window.revision == baseline[1] + 1 and window.pending_speech is None,
                    'The first full-take insertion was not exactly one transaction.')
            first_id = first.id
            full_first_project = copy.deepcopy(window.project.to_dict())
            full_first = asdict(first)

            report['stage'] = 'second consecutive take rejects same-lane overlap without mutation'
            before_overlap = editing_state(window)
            second_media = generate_take(cues[1], TAKE_VOICE_IDS[1], take_paths[1], overlap=True)
            unchanged(before_overlap)
            require(window.pending_speech is not None and window.pending_speech[0] == second_media
                    and not dialogs.overlap_paths and len(window.project.clips) == 1,
                    'Overlap did not preserve the exact pending second take without insertion.')
            pending = window.pending_speech
            pending_provenance = asdict(second_media.speech_provenance)
            take_hashes = {str(path): sha256(path) for path in take_paths}
            pending_count = len(recorder.workers())
            dialogs.overlap_paths.append(take_paths[1]); window.tts_insert_button.click(); app.processEvents()
            require(window.pending_speech is pending and not dialogs.overlap_paths,
                    'A repeated occupied-lane retry lost/replaced the pending take.')
            unchanged(before_overlap)
            report['screenshots'].append(screenshot(app, window, folder, '03-overlap-pending-take.png'))

            report['stage'] = 'explicit trim of blocker then same saved-take retry'
            window.select_clip(first_id)
            window.in_spin.setValue(TRIM_SOURCE_IN); window.duration_spin.setValue(CAPTION_FRAMES)
            trim_revision = window.revision; window.apply_timing(); dialogs.require_no_warnings()
            first = window.selected_clip()
            require((first.start, first.source_in, first.duration, first.end, first.speed)
                    == (cues[0].start, TRIM_SOURCE_IN, CAPTION_FRAMES, cues[1].start, 1)
                    and window.revision == trim_revision + 1,
                    'Explicit Apply timing did not trim the blocker to the exact touching boundary.')
            trimmed_first_project = copy.deepcopy(window.project.to_dict())
            require(trimmed_first_project['media'] == full_first_project['media']
                    and trimmed_first_project['captions'] == full_first_project['captions']
                    and all(sha256(path) == digest for path, digest in take_hashes.items())
                    and asdict(second_media.speech_provenance) == pending_provenance,
                    'Explicit trim altered original WAV bytes, caption text or immutable provenance.')
            window.undo(); require(window.project.to_dict() == full_first_project, 'Trim undo was not exact.')
            window.redo(); require(window.project.to_dict() == trimmed_first_project, 'Trim redo was not exact.')
            require(window.pending_speech is pending, 'An unrelated blocker edit lost the pending second take.')
            # No new generation: the pending second take must remain usable after
            # the explicit edit while its original caption/media snapshot is valid.
            dialogs.overflow_answers.append(True); retry_revision = window.revision
            window.tts_insert_button.click(); app.processEvents(); dialogs.require_no_warnings()
            require(window.pending_speech is None and len(window.project.clips) == 2
                    and len(recorder.workers()) == pending_count and window.revision == retry_revision + 1,
                    'Retry after trimming did not insert the original pending take as one transaction without synthesis.')
            second = next(c for c in window.project.clips if c.media_id == second_media.id)
            require((second.start, second.track, second.source_in, second.duration, second.speed)
                    == (cues[1].start, args.track, 0, window.project.media_frames(second_media), 1)
                    and second.duration > cues[1].duration,
                    'The retried second take was silently shortened, moved or retimed.')
            pair = copy.deepcopy(window.project.to_dict())
            window.undo(); require(window.project.to_dict() == trimmed_first_project, 'Second insertion undo was not atomic.')
            window.redo(); require(window.project.to_dict() == pair, 'Second insertion redo changed IDs, timing or provenance.')
            report['consecutive_takes'] = {'caption_frames': CAPTION_FRAMES, 'captions_touch_exactly': cues[0].end == cues[1].start,
                'same_lane_overlap_rejected': True, 'repeated_overlap_preserved_pending_take': True,
                'first_take_inserted_full_before_explicit_trim': full_first,
                'explicit_trim': {'clip_id': first_id, 'source_in_frames': TRIM_SOURCE_IN, 'duration_frames': CAPTION_FRAMES,
                                  'timeline_end_frame': cues[1].start, 'undo_redo_exact': True},
                'retry_reused_same_saved_take': True, 'retry_started_additional_workers': 0,
                'second_take_inserted_full': asdict(second), 'insertion_undo_redo_exact': True,
                'original_wavs_and_provenance_unchanged': True}

            report['stage'] = 'separate alternate take retry on free other lane'
            alternate_path = original / 'assets' / 'take-2-alternate.wav'
            before_alternate = editing_state(window)
            alternate = generate_take(cues[1], ALTERNATE_VOICE_ID, alternate_path, overlap=True)
            unchanged(before_alternate)
            require(window.pending_speech is not None and window.pending_speech[0] == alternate,
                    'Alternate take was not preserved after occupied-lane rejection.')
            alternate_hash = sha256(alternate_path); alternate_workers = len(recorder.workers())
            free_track = 5 - args.track
            window.tts_track.setCurrentIndex(window.tts_track.findData(free_track))
            dialogs.overflow_answers.append(False); window.tts_insert_button.click(); app.processEvents()
            unchanged(before_alternate)
            require(window.pending_speech is not None and sha256(alternate_path) == alternate_hash,
                    'Declining full-take insertion on the free lane lost the saved take.')
            dialogs.overflow_answers.append(True); window.tts_insert_button.click(); app.processEvents()
            dialogs.require_no_warnings()
            alternate_clip = window.selected_clip()
            require(window.pending_speech is None and alternate_clip.media_id == alternate.id
                    and alternate_clip.track == free_track and alternate_clip.start == cues[1].start
                    and alternate_clip.duration == window.project.media_frames(alternate)
                    and len(recorder.workers()) == alternate_workers,
                    'Free-lane retry did not insert the entire same saved alternate take without synthesis.')
            with_alternate = copy.deepcopy(window.project.to_dict())
            window.undo(); require(window.project.to_dict() == pair, 'Alternate insertion undo was not exact.')
            window.redo(); require(window.project.to_dict() == with_alternate, 'Alternate insertion redo was not exact.')
            report['screenshots'].append(screenshot(app, window, folder, '04-free-other-lane-inserted.png'))
            window.undo(); require(window.project.to_dict() == pair, 'Final project must retain only the two primary takes.')
            require(sha256(alternate_path) == alternate_hash, 'Undo deleted or changed the saved alternate WAV.')
            report['free_other_lane'] = {'occupied_lane': args.track, 'chosen_free_lane': free_track,
                'decline_preserved_pending_take': True, 'inserted_full_without_resynthesis': True,
                'clip': asdict(alternate_clip), 'undo_redo_exact': True,
                'alternate_insertion_undone_for_final_two_take_project': True, 'durable_wav_preserved': True}

            report['stage'] = 'real Qt save reopen and relative-path relocation'
            dialogs.save_paths.append(original / 'project.lumen'); require(window.save_project(), 'Project save failed.')
            saved = copy.deepcopy(window.project.to_dict())
            disk = json.loads((original / 'project.lumen').read_text(encoding='utf-8'))
            require([m['path'] for m in disk['media']] == ['assets/take-1.wav', 'assets/take-2.wav'],
                    'Saved speech paths are not relative to the project package.')
            window.editor = Editor(Project()); window.refresh()
            dialogs.open_paths.append(original / 'project.lumen'); window.open_project()
            require(window.project.to_dict() == saved, 'UI reopen changed clip timing, IDs or provenance.')
            window.player.stop(); window.player.setSource(QUrl())
            window.editor = Editor(Project()); window.project_path = None; window.dirty = False; window.refresh()
            moved = folder / 'project-relocated'; original.rename(moved)
            dialogs.open_paths.append(moved / 'project.lumen'); window.open_project()
            expected = copy.deepcopy(saved)
            for media in expected['media']:
                media['path'] = str(moved / 'assets' / Path(media['path']).name)
            require(window.project.to_dict() == expected and not window.project.missing_media(),
                    'Relocation lost exact project state or required relinking.')
            for media in window.project.media:
                require(sha256(media.path) == media.speech_provenance.output_sha256,
                        'Save/reopen/relocation changed original speech bytes.')
            require(sha256(moved / 'assets' / alternate_path.name) == alternate_hash,
                    'Relocation changed the retained alternate WAV.')
            dialogs.require_no_warnings()
            report['project'] = {'file': 'project-relocated/project.lumen', 'sha256': sha256(moved / 'project.lumen'),
                                 'relative_paths': [m['path'] for m in disk['media']], 'reopen_exact': True,
                                 'relocated_without_relink': True, 'provenance_unchanged': True,
                                 'final_clip_count': len(window.project.clips)}

            report['stage'] = 'real shared-graph preview export and two-take PCM timing'
            window.build_preview(); require(window.preview_job is not None, 'Preview task did not start.')
            settle('real two-take preview')
            preview = window.preview_paths.get((window.revision, window.preview_mode))
            require(preview is not None and Path(preview).is_file(), 'The current real preview was not produced.')
            preview_path = folder / 'preview.mp4'; shutil.copyfile(preview, preview_path)
            window.seek_frame(args.timeline_start); window.player.play()
            wait_until(app, lambda: not window.video.image.isNull(), min(args.timeout, 20), 'actual Qt video decode')
            window.player.pause(); report['qt_preview_decoded'] = True
            report['screenshots'].append(screenshot(app, window, folder, '05-relocated-two-take-preview.png'))
            export_path = folder / 'export.mp4'; dialogs.save_paths.append(export_path); window.export_dialog()
            require(window.export_job is not None, 'Export task did not start.'); settle('real two-take export')
            require(len(recorder.graphs) == 2 and recorder.graphs[0]['sha256'] == recorder.graphs[1]['sha256'],
                    'Preview/export did not execute the same normalized audio/video graph.')
            source_pcm = {media.id: decode_pcm(media.path) for media in window.project.media}
            reference, plans = timeline_reference(window.project, source_pcm)
            graph = (folder / recorder.graphs[0]['filename']).read_text(encoding='utf-8')
            for item in plans:
                plan = item['plan']
                require(','.join(plan['filters']) in graph and f'adelay={plan["start_sample"]}S:all=1' in graph,
                        'Executed graph does not contain the exact selected-source and timeline sample plan.')
            require(f'amix=inputs=3:normalize=0:duration=first,atrim=end_sample={len(reference)}[aout]' in graph,
                    'Executed graph does not mix both takes with the exact final sample budget.')
            rendered = []; report['outputs'] = []
            for path in (preview_path, export_path):
                output = media_evidence(path); samples = decode_pcm(path); rendered.append(samples)
                output['pcm_timing'] = compare_timeline_pcm(window.project, source_pcm, samples)
                video = next(item for item in output['streams'] if item['codec_type'] == 'video')
                require((video['width'], video['height'], int(video['nb_read_frames']))
                        == (640, 360, window.project.duration_frames), 'Rendered geometry or frame count differs from the project.')
                report['outputs'].append(output)
            require(np.array_equal(rendered[0], rendered[1]), 'Preview/export decoded PCM samples are not identical.')
            require(window.project.to_dict() == expected, 'Rendering mutated the saved project.')
            require(not dialogs.open_paths and not dialogs.save_paths and not dialogs.overflow_answers and not dialogs.overlap_paths,
                    'An explicitly queued UI action was not consumed.')
            require(len(recorder.workers()) == len(audition_specs()) + 3,
                    'An unexpected synthesis worker ran during pending-take reuse or rendering.')
            report.update(shared_graph=recorder.graphs, preview_export_decoded_pcm_identical=True,
                          audio_timing_plans=plans, frame_count=window.project.duration_frames,
                          actual_synthesis_worker_count=len(recorder.workers()), stage='complete')
        finally:
            if window is not None:
                window.cancel_jobs()
                for job in list(window.jobs):
                    require(job.wait(30000), 'A real background job did not stop during cleanup.')
                app.processEvents(); window.close_tts_preview(); window.player.stop(); window.player.setSource(QUrl())
                window.dirty = False; window.closing = True; window.close(); app.processEvents()
            report['messages'] = dialogs.messages
            report['processes'] = recorder.evidence()


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--model-pack', required=True, type=local_pack, help='Existing separately audited official v1.1 pack.')
    parser.add_argument('--output-dir', required=True, help='A NEW output directory outside the model pack.')
    parser.add_argument('--ui-language', choices=('en', 'zh-CN'), default='zh-CN')
    parser.add_argument('--font-family', default='Noto Sans CJK SC', help='Already installed font covering the Mandarin cues.')
    parser.add_argument('--track', type=int, choices=(2, 3), default=2)
    parser.add_argument('--timeline-start', type=int, default=15)
    parser.add_argument('--timeout', type=float, default=600, help='Finite positive seconds per real operation.')
    args = parser.parse_args(argv)
    if not math.isfinite(args.timeout) or args.timeout <= 0 or args.timeline_start < 3:
        parser.error('timeout must be finite and positive; timeline-start must be at least 3 frames.')
    raw_output = str(args.output_dir)
    if '://' in raw_output or raw_output.startswith(('//', '\\\\')):
        parser.error('output-dir must be a local directory.')
    args.output_dir = Path(args.output_dir).expanduser().resolve()
    if args.output_dir.exists() or args.output_dir.is_relative_to(args.model_pack):
        parser.error('output-dir must be a new directory outside the model pack.')
    return args


def main(argv=None):
    args = parse_args(argv)
    args.output_dir.mkdir(parents=True, exist_ok=False)
    report = {'status': 'running', 'stage': 'input verification', 'platform': platform.platform(),
              'python': platform.python_version(), 'pack_version': PACK_VERSION,
              'sampled_voice_ids': list(VOICE_IDS), 'sampled_voice_count': len(VOICE_IDS), 'catalog_voice_count': 103,
              'planned_audition_count': len(audition_specs()), 'planned_saved_take_count': 3,
              'all_catalog_voices_synthesized': False, 'speech_quality_evaluated': False,
              'spoken_accuracy_evaluated': False, 'physical_speaker_playback_verified': False,
              'native_windows_macos_validated': False, 'downloads_performed': False, 'installations_performed': False,
              'network_denial': 'Not asserted here. Retain the external run_network_denied.py harness log.'}
    began = time.monotonic()
    try:
        report['pack'] = pack_evidence(args.model_pack)
        report['runtime'] = runtime_evidence()
        report['source_sha256'] = source_evidence()
        run_workflow(args, report)
        report['status'] = 'passed'
    except Exception as exc:
        report.update(status='failed', error=f'{type(exc).__name__}: {exc}')
    try:
        report['pack_unchanged'] = 'pack' in report and pack_evidence(args.model_pack) == report['pack']
        report['source_unchanged'] = 'source_sha256' in report and source_evidence() == report['source_sha256']
        if not report['pack_unchanged'] or not report['source_unchanged']:
            report.update(status='failed', input_verification_error='Pack/source identity was not established or changed during the run.')
    except Exception as exc:
        report.update(status='failed', input_verification_error=str(exc))
    report['elapsed_seconds'] = time.monotonic() - began
    evidence = args.output_dir / 'acceptance.json'
    evidence.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str) + '\n', encoding='utf-8')
    print(json.dumps({'status': report['status'], 'stage': report['stage'], 'evidence': str(evidence),
                      'error': report.get('error')}, ensure_ascii=False, indent=2))
    return 0 if report['status'] == 'passed' else 1


if __name__ == '__main__':
    raise SystemExit(main())
