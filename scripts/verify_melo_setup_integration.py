#!/usr/bin/env python3
"""Real Qt experimental Melo acceptance with an audited, explicit local pack.

No network requests, downloads, installs, substituted speech, inference mocks or
changes to the existing Kokoro acceptance runner. Execute only after a source
freeze, with the separately provisioned pinned runtime and model:

  python scripts/run_network_denied.py -- python \
    scripts/verify_melo_setup_integration.py --model-pack /local/melo-zh_en \
    --asset-manifest /local/verified-model-manifest.json \
    --output-dir artifacts/melo-setup-new

Exercises actual Qt component setup, pack verification, single-speaker bilingual
auditions, cancel-after-real-PID/retry, explicit over-limit and native OOV error
rejection, saved-take insertion/undo/redo/save/reopen/relocation, and identical
shared-graph preview/export. Native 44.1 kHz sample counts and 48 kHz timeline
budgets are checked independently. Dialog answers and process observation are
the only automation substitutions. Original text is never silently rewritten.
Passing is technical acceptance, not naturalness/intelligibility certification,
physical playback, native desktop acceptance or permission to ship this model.
"""
from __future__ import annotations

import argparse
from contextlib import ExitStack
import copy
from dataclasses import asdict
from fractions import Fraction
import hashlib
import json
import math
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import time
import traceback
from unittest.mock import patch
import wave

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.verify_asr_setup_workflow import (
    AcceptanceError, local_file, media_evidence, require, sha256, wait_until,
)
from scripts.verify_tts_setup_integration import (
    TTSDialogs, TTSProcessRecorder, compare_pcm, decode_pcm, local_pack,
    runtime_evidence, screenshot,
)

PACK_VERSION = 'melo-zh_en-v1'
VOICE_IDS = (0,)
SAMPLE_RATE = 44100
TEXTS = {
    'en': 'A new scene begins here. We can keep the full voice.',
    'zh': '请先整理视频素材，再检查字幕和声音。转场保持自然，画面清晰稳定，确认完成后再导出。',
}
TAKE_TEXT = TEXTS['zh']
INVALID_CASES = (
    ('quoted-punctuation', '他说：“检查字幕……再导出！”', 'audition'),
    ('rare-han', '请读嗧这个字。', 'saved_take'),
)
UNSUPPORTED_ERROR = ('The speech engine reported unsupported characters (possibly punctuation) or sounds. '
                     'No audio was saved. Revise the reading text or choose another model.')


def pack_evidence(path, manifest_path=None):
    """Check catalog pins; optionally audit every extra asset against a manifest."""
    from lumen.tts import _assets
    from lumen.voice_catalog import get_pack
    pack = get_pack(PACK_VERSION)
    require(pack.backend == 'melo' and pack.experimental and pack.sample_rate == SAMPLE_RATE
            and pack.bank_size == 1 and pack.max_text_chars == 300,
            'Experimental Melo descriptor differs from the acceptance contract.')
    entries = _assets(path, None)
    values = dict(entries)
    if manifest_path is not None:
        manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
        require(manifest.get('revision') == pack.source_revision, 'Audit revision does not match the pinned descriptor.')
        files = manifest.get('files', [])
        audited = {entry['file']: entry for entry in files}
        require(len(audited) == len(files) and set(values) == set(audited),
                'Local pack and audited manifest must contain exactly the same unique files.')
        for name, actual in entries:
            require(actual == audited[name]['sha256'] and (path / name).stat().st_size == audited[name]['size'],
                    f'Local asset differs from the separately audited manifest: {name}')
    require(all(values.get(name) == digest for name, digest in pack.expected_hashes.items()),
            'Required asset hashes do not match the pinned Melo catalog.')
    return {'path': str(path), 'pack_version': PACK_VERSION, 'backend': pack.backend,
            'source_revision': pack.source_revision,
            'manifest_path': str(manifest_path) if manifest_path is not None else None,
            'manifest_sha256': sha256(manifest_path) if manifest_path is not None else None,
            'all_audited_files_verified': manifest_path is not None, 'required_catalog_pins_verified': True,
            'assets': values, 'required_files': list(pack.required_files),
            'assets_sha256': hashlib.sha256(json.dumps(entries, ensure_ascii=False, separators=(',', ':')).encode()).hexdigest()}


class MeloDialogs(TTSDialogs):
    """Fail closed, with only specifically queued pronunciation/overflow choices."""
    def __init__(self):
        super().__init__()
        self.pronunciation_answers = []
        self.expected_warning_indexes = set()

    def question(self, parent, title, text, *args, **kwargs):
        from PySide6.QtWidgets import QMessageBox
        from lumen.i18n import tr
        if str(title) == tr('Review speech pronunciation') and self.pronunciation_answers:
            keep = self.pronunciation_answers.pop(0)
            self.messages.append({'kind': 'pronunciation', 'title': str(title), 'text': str(text),
                                  'continue_original_text': keep})
            return QMessageBox.StandardButton.Yes if keep else QMessageBox.StandardButton.No
        return super().question(parent, title, text, *args, **kwargs)

    def accept_expected_warning(self, since, *, title=None, text=None):
        warnings = [(i, value) for i, value in enumerate(self.messages) if i >= since and value['kind'] == 'warning']
        require(len(warnings) == 1, f'Expected exactly one reported failure: {self.messages[since:]}')
        index, warning = warnings[0]
        require((title is None or warning['title'] == title) and (text is None or warning['text'] == text),
                f'The expected validation error was not reported: {warning}')
        self.expected_warning_indexes.add(index)
        self.require_no_warnings(since)
        return warning

    def require_no_warnings(self, since=0):
        unexpected = [item for index, item in enumerate(self.messages)
                      if index >= since and index not in self.expected_warning_indexes
                      and item['kind'] in ('warning', 'unexpected_question')]
        require(not unexpected, f'Unexpected UI diagnostics: {unexpected}')


def waveform_evidence(path, destination):
    """Measure actual 44.1 kHz PCM, never infer pronunciation from signal energy."""
    import numpy as np
    with wave.open(str(path), 'rb') as wav:
        require((wav.getnchannels(), wav.getsampwidth(), wav.getframerate()) == (1, 2, SAMPLE_RATE),
                'Generated voice is not mono 44.1 kHz signed PCM16.')
        count = wav.getnframes()
        values = np.frombuffer(wav.readframes(count), dtype='<i2').astype(np.float64) / 32768
    require(count > SAMPLE_RATE // 10 and len(values) == count and np.isfinite(values).all(),
            'Generated speech is empty, truncated, too short or nonfinite.')
    peak = float(np.max(np.abs(values)))
    rms = float(np.sqrt(np.mean(values ** 2)))
    require(peak > .01 and rms > .001, 'Actual generated PCM is silent or effectively empty.')
    envelope, offset = [], 0
    for chunk in np.array_split(values, min(200, len(values))):
        envelope.append({'start_sample': offset, 'sample_count': len(chunk), 'minimum': float(chunk.min()),
                         'maximum': float(chunk.max()), 'rms': float(np.sqrt(np.mean(chunk ** 2)))})
        offset += len(chunk)
    Path(destination).write_text(json.dumps({'source_sha256': sha256(path), 'sample_rate': SAMPLE_RATE,
                                            'sample_count': count, 'envelope': envelope}, indent=2) + '\n')
    return {'file': Path(path).name, 'sha256': sha256(path), 'sample_rate': SAMPLE_RATE,
            'sample_count': count, 'duration_seconds': count / SAMPLE_RATE, 'peak': peak, 'rms': rms,
            'nonzero_samples': int(np.count_nonzero(values)), 'waveform_file': Path(destination).name}


def source_evidence(root):
    paths = sorted([*root.rglob('*.py'), root / 'pyproject.toml'])
    return {path.relative_to(root).as_posix(): sha256(path) for path in paths if path.is_file()}


def checkpoint(args, report, stage):
    report['stage'] = stage
    (args.output_dir / 'acceptance.json').write_text(
        json.dumps(report, ensure_ascii=False, indent=2, default=str) + '\n', encoding='utf-8')
    print(json.dumps({'stage': stage}, ensure_ascii=False), flush=True)


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
    from lumen.audio_timing import compile_audio_timing
    from lumen.timing import sample_boundary
    import numpy as np
    from lumen.i18n import tr, tr_error
    from lumen.voice_catalog import get_pack
    from scripts.qt_test_cleanup import cleanup_test_windows

    folder = args.output_dir
    app = QApplication.instance() or QApplication([])
    QSettings.setDefaultFormat(QSettings.Format.IniFormat)
    for scope in (QSettings.Scope.UserScope, QSettings.Scope.SystemScope):
        QSettings.setPath(QSettings.Format.IniFormat, scope, str(folder / 'settings'))
    workspace = QSettings(str(folder / 'settings' / 'workspace.ini'), QSettings.Format.IniFormat)
    shortcuts = QSettings(str(folder / 'settings' / 'shortcuts.ini'), QSettings.Format.IniFormat)
    existing_windows = list(app.topLevelWidgets())
    dialogs, recorder = MeloDialogs(), TTSProcessRecorder(folder)
    deadline = time.monotonic() + args.deadline
    def wait(condition, description, limit=None):
        remaining = min(args.timeout if limit is None else limit, deadline - time.monotonic())
        require(remaining > 0, 'Overall acceptance deadline exceeded.')
        wait_until(app, condition, remaining, description)
    report.update(qt_binding=qt_version, qt_platform=app.platformName(), ui_language=args.ui_language,
                  audio_output_devices=[device.description() for device in QMediaDevices.audioOutputs()],
                  automation_boundaries=['explicit local file chooser answers', 'recorded message boxes with explicit overflow/pronunciation choices',
                                         'isolated settings', 'transparent subprocess observation', 'observation of real Qt result signals'],
                  screenshots=[], cases=[])
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
            window.editor = Editor(Project(name='Real experimental Melo setup acceptance', width=640, height=360))
            cue = window.editor.add_caption(args.timeline_start, 1, TAKE_TEXT)
            window.project.caption_style.font_family = args.font_family
            window.project.caption_style.font_size = 22
            window.changed(); window.select_caption(cue.id)
            window.resize(1440, 1000); window.show(); app.processEvents()
            baseline = copy.deepcopy(window.project.to_dict())
            revision, dirty = window.revision, window.dirty
            def unchanged():
                require(window.project.to_dict() == baseline and window.revision == revision and window.dirty == dirty,
                        'Setup/audition/cancellation changed the project, undo revision or dirty state.')
            def settle(description):
                wait(lambda: not window.jobs, description)
                dialogs.require_no_warnings()
            checkpoint(args, report, 'actual Qt component setup metadata diagnostics')
            setup = ComponentSetupDialog(window, path=folder / 'settings' / 'component-setup.json')
            setup.fields['tts_model_dir'].setText(str(args.model_pack))
            require(setup.pack.findData(PACK_VERSION) >= 0, 'Melo is absent from actual component setup.')
            setup.pack.setCurrentIndex(setup.pack.findData(PACK_VERSION)); setup.hashes.setChecked(True)
            require('300' in setup.pack_info.text(), 'Experimental setup limit notice is absent.')
            setup.show(); setup.check_button.click(); settle(report['stage'])
            require(setup._report is not None, 'Component metadata diagnostics produced no report.')
            diagnostics = asdict(setup._report)
            require(not diagnostics['inference_tested'] and not recorder.workers(), 'Metadata diagnostics ran inference.')
            checks = {item['key']: item for item in diagnostics['checks'] if item['component'] == 'tts'}
            require(all(checks[name]['status'] == 'hash_verified' for name in get_pack(PACK_VERSION).required_files),
                    'Component setup did not verify all five pinned Melo assets.')
            require(all(item['status'] in ('metadata_available', 'hash_verified') for item in checks.values()),
                    f'Component setup TTS checks failed: {checks}')
            setup.apply_button.click()
            require(load_preferences(setup.path)['tts_model_dir'] == str(args.model_pack), 'Setup path was not saved.')
            require(operational_settings(window)['tts_pack_version'] == PACK_VERSION
                    and operational_settings(window)['tts_model_dir'] == str(args.model_pack), 'Setup was not applied.')
            unchanged()
            report['diagnostics'] = diagnostics
            report['screenshots'].append(screenshot(app, setup, folder, '01-component-setup.png')); setup.reject()
            checkpoint(args, report, 'actual Qt full-pack and pinned runtime verification')
            window.tts_verify_button.click(); settle(report['stage'])
            require(window._tts_verified_pack == window._tts_pack_key() and window.tts_preview_button.isEnabled(),
                    'Real pack verification did not enable audition controls.')
            require(not recorder.workers(), 'Pack verification unexpectedly synthesized speech.')
            require(window.tts_voice.count() == 1 and window.tts_voice.currentData() == 0
                    and '300' in window.tts_pack_info.text(), 'Melo single-speaker controls or experimental notice are wrong.')
            window.tts_pause.setValue(100)
            unchanged(); report['setup_applied_without_project_edit'] = True
            window.side_tabs.setCurrentIndex(1)
            window.side_tabs.widget(1).ensureWidgetVisible(window.tts_preview_status)
            report['screenshots'].append(screenshot(app, window, folder, '02-verified-voice-browser.png'))

            checkpoint(args, report, 'cancel real audition worker and retry')
            window.tts_voice.setCurrentIndex(window.tts_voice.findData(0))
            window.tts_preview_text.setPlainText(TEXTS['zh'] * 3)
            window.tts_preview_button.click()
            job = window.tts_job
            require(job is not None and job is window._tts_preview_task, 'Qt audition task did not start.')
            window.preview_tts_voice()
            require(window.tts_job is job and len(window.jobs) == 1, 'Duplicate audition created another task.')
            wait(lambda: recorder.workers() or not window.jobs, 'real TTS worker PID')
            active = recorder.workers()
            require(len(active) == 1 and active[0].poll() is None,
                    'No live worker remained to cancel; cancellation cannot be claimed.')
            began = time.monotonic(); window.tts_preview_stop_button.click()
            wait(lambda: not window.jobs and window._tts_preview_task is None, 'canceled TTS cleanup', 30)
            require(active[0].poll() is not None and window.tts_preview_path is None, 'Canceled worker/output was retained.')
            require(not list((window.cache / 'voice-auditions').glob('*.wav')), 'Cancellation left a partial audition.')
            dialogs.require_no_warnings(); unchanged()
            require(window.tts_preview_button.isEnabled(), 'Audition cannot retry after cancellation.')
            report['cancellation'] = {'passed': True, 'runtime_pid': active[0].pid,
                                      'returncode': active[0].returncode, 'cleanup_seconds': time.monotonic() - began,
                                      'duplicate_start_ignored': True, 'partial_wav_absent': True}

            for voice_id in VOICE_IDS:
                for language, text in TEXTS.items():
                    name = f'{language}-sid{voice_id}'
                    checkpoint(args, report, 'real Qt audition ' + name)
                    window.tts_voice.setCurrentIndex(window.tts_voice.findData(voice_id))
                    window.tts_preview_text.setPlainText(text)
                    results, count, began = [], len(recorder.workers()), time.monotonic()
                    window.tts_preview_button.click()
                    require(window.tts_job is not None, 'Generate audition did not start a real task.')
                    window.tts_job.result.connect(results.append)
                    settle(report['stage'])
                    workers = recorder.workers()[count:]
                    require(len(workers) == 1 and workers[0].poll() == 0 and len(results) == 1,
                            'Audition did not return one successful real worker result.')
                    require(window.tts_preview_path is not None and Path(window.tts_preview_path).is_file(),
                            'Actual audition WAV is missing.')
                    result = asdict(results[0]); source = Path(window.tts_preview_path)
                    require(result['provenance']['text'] == text and result['provenance']['voice_id'] == str(voice_id)
                            and result['provenance']['output_sha256'] == sha256(source), 'Audition provenance is not exact.')
                    provenance = result['provenance']
                    require(provenance['engine'] == get_pack(PACK_VERSION).engine
                            and provenance['engine_version'] == report['runtime']['packages']['sherpa-onnx']['version']
                            and provenance['model_sha256'] == report['pack']['assets']['model.onnx']
                            and provenance['voice_sha256'] == report['pack']['assets']['model.onnx']
                            and provenance['assets_sha256'] == report['pack']['assets_sha256']
                            and provenance['sample_rate'] == SAMPLE_RATE and provenance['speed'] == 1.0
                            and provenance['silence_scale'] == 1.0,
                            'Audition provenance differs from the exact audited pack/runtime/settings.')
                    destination = folder / (name + '.wav'); shutil.copyfile(source, destination)
                    record = {'name': name, 'voice_id': voice_id, 'voice_name': get_voice(PACK_VERSION, voice_id).name,
                              'language': language, 'original_text': text, 'elapsed_seconds': time.monotonic() - began,
                              'provenance': result['provenance'],
                              'waveform': waveform_evidence(destination, folder / (name + '-waveform.json'))}
                    require(provenance['sample_count'] == record['waveform']['sample_count']
                            and result['duration'] == provenance['sample_count'] / SAMPLE_RATE,
                            'Audition did not preserve exact native PCM sample count and duration.')
                    report['cases'].append(record)
                    window.tts_preview_play_button.click(); app.processEvents(); window.tts_preview_stop_button.click()
                    unchanged()
                    record['replay_stop_preserved_project'] = True
                    record['physical_audio_output_verified'] = False
                    if language == 'zh':
                        report['screenshots'].append(screenshot(app, window, folder, name + '-audition.png'))
            require(len(report['cases']) == 2, 'Both bilingual single-speaker auditions must run.')
            report['cancellation']['retry_succeeded'] = True
            report['auditions_preserved_project_revision_dirty_state'] = True

            checkpoint(args, report, 'explicit 300-character limit and reported native OOV rejection')
            window.close_tts_preview()
            too_long = '好' * 301
            before_messages, before_workers = len(dialogs.messages), len(recorder.workers())
            window.tts_preview_text.setPlainText(too_long); window.tts_preview_button.click(); app.processEvents()
            warning = dialogs.accept_expected_warning(before_messages, title=tr('Text exceeds this pack’s limit'))
            require(not window.jobs and len(recorder.workers()) == before_workers and window.tts_preview_path is None
                    and window.tts_preview_text.toPlainText() == too_long,
                    'Over-limit input was altered or started inference.')
            unchanged()
            report['text_limit'] = {'submitted_chars': len(too_long), 'max_chars': 300,
                                    'rejected_before_worker': True, 'text_unchanged': True, 'warning': warning}
            report['invalid_text_cases'] = []
            for name, original_text, operation in INVALID_CASES:
                checkpoint(args, report, 'native unsupported text rejection: ' + name)
                window.close_tts_preview()
                before_messages, before_workers = len(dialogs.messages), len(recorder.workers())
                target = folder / (name + '-must-not-exist.wav')
                if name == 'quoted-punctuation': dialogs.pronunciation_answers.append(True)
                if operation == 'audition':
                    window.tts_preview_text.setPlainText(original_text); window.tts_preview_button.click()
                else:
                    window.select_caption(cue.id)
                    window.tts_track.setCurrentIndex(window.tts_track.findData(args.track))
                    window.caption_text.setPlainText(original_text)
                    dialogs.save_paths.append(target); window.tts_button.click()
                require(window.tts_job is not None, 'Invalid-text test did not reach a real inference task.')
                errors = []; window.tts_job.failed.connect(errors.append)
                wait(lambda: not window.jobs, 'real native unsupported text failure')
                warning = dialogs.accept_expected_warning(before_messages, text=tr_error(UNSUPPORTED_ERROR))
                require(len(recorder.workers()) == before_workers + 1 and recorder.workers()[-1].poll() is not None,
                        'Invalid-text test did not finish exactly one real worker.')
                require(errors == [UNSUPPORTED_ERROR] and not target.exists() and window.tts_preview_path is None
                        and not list((window.cache / 'voice-auditions').glob('*.wav'))
                        and not list(folder.glob('.lumen-tts-*')) and window.pending_speech is None,
                        'Known unsupported text was published or left partial output.')
                require(not dialogs.pronunciation_answers, 'Original-text review choice was not consumed.')
                field = window.tts_preview_text if operation == 'audition' else window.caption_text
                require(field.toPlainText() == original_text, 'Unsupported reading text was silently rewritten.')
                unchanged()
                report['invalid_text_cases'].append({'name': name, 'original_text': original_text,
                    'operation': operation, 'worker_pid': recorder.workers()[-1].pid,
                    'worker_returncode': recorder.workers()[-1].returncode, 'error': errors[0],
                    'warning': warning, 'no_output_published': True, 'project_unchanged': True,
                    'original_reading_text_preserved': True})
            window.select_caption(cue.id)
            require(window.caption_text.toPlainText() == TAKE_TEXT, 'Original caption changed during invalid-text trials.')

            checkpoint(args, report, 'saved take and explicit overflow decline then accept')
            window.close_tts_preview(); window.select_caption(cue.id)
            window.tts_voice.setCurrentIndex(window.tts_voice.findData(0))
            window.tts_track.setCurrentIndex(window.tts_track.findData(args.track))
            original = folder / 'project-original'; (original / 'assets').mkdir(parents=True)
            take = original / 'assets' / 'full-voice.wav'
            dialogs.save_paths.append(take); dialogs.overflow_answers.append(False)
            count, began = len(recorder.workers()), time.monotonic()
            window.tts_button.click(); settle(report['stage'])
            require(len(recorder.workers()) == count + 1 and recorder.workers()[-1].poll() == 0,
                    'Saved take did not use a successful real worker.')
            require(take.is_file() and window.pending_speech is not None and not dialogs.overflow_answers and not dialogs.pronunciation_answers,
                    'Explicit No did not preserve the full saved pending take.')
            unchanged()
            media = window.pending_speech[0]
            require(media.speech_provenance.text == TAKE_TEXT
                    and media.speech_provenance.source_caption_id == cue.id
                    and media.speech_provenance.source_start == args.timeline_start
                    and media.speech_provenance.source_duration == 1,
                    'Saved take does not preserve the selected caption provenance.')
            report['saved_take'] = {'elapsed_seconds': time.monotonic() - began, 'provenance': asdict(media.speech_provenance),
                                    'waveform': waveform_evidence(take, folder / 'saved-take-waveform.json')}
            require(media.speech_provenance.sample_rate == SAMPLE_RATE
                    and media.speech_provenance.sample_count == report['saved_take']['waveform']['sample_count']
                    and media.duration == media.speech_provenance.sample_count / SAMPLE_RATE,
                    'Saved media lost exact 44.1 kHz PCM sample count or duration.')
            dialogs.overflow_answers.append(True); window.tts_insert_button.click(); app.processEvents()
            require(not dialogs.overflow_answers and window.pending_speech is None, 'Explicit full-take acceptance failed.')
            require(len(window.project.media) == len(window.project.clips) == 1, 'Insertion did not create exactly one take.')
            clip = window.selected_clip(); inserted = copy.deepcopy(window.project.to_dict())
            require(clip.track == args.track and clip.start == args.timeline_start and clip.duration == window.project.media_frames(media)
                    and clip.duration > cue.duration and window.project.captions[0].duration == 1,
                    'Full voice/caption timing was unexpectedly shortened or changed.')
            exact_frames = math.ceil(Fraction(media.speech_provenance.sample_count, SAMPLE_RATE) * window.project.fps)
            require(clip.duration == exact_frames, 'Inserted clip did not use the exact native-sample ceiling frame count.')
            require(window.revision == revision + 1, 'Saved-take insertion is not one editor transaction.')
            window.undo(); require(window.project.to_dict() == baseline, 'Undo did not remove the entire insertion.')
            require(take.is_file(), 'Undo removed the durable saved WAV.')
            window.redo(); require(window.project.to_dict() == inserted, 'Redo changed speech IDs, timing or provenance.')
            report['overflow'] = {'declined_without_mutation': True, 'accepted_full_take': True, 'caption_unchanged': True,
                                  'clip': asdict(clip), 'undo_redo_exact': True}
            report['screenshots'].append(screenshot(app, window, folder, '05-inserted-full-take.png'))

            checkpoint(args, report, 'actual Qt save reopen and package relocation')
            dialogs.save_paths.append(original / 'project.lumen'); require(window.save_project(), 'Project save failed.')
            saved = copy.deepcopy(window.project.to_dict())
            disk = json.loads((original / 'project.lumen').read_text(encoding='utf-8'))
            require(disk['media'][0]['path'] == 'assets/full-voice.wav', 'Saved voice path is not package-relative.')
            window.editor = Editor(Project()); window.refresh()
            dialogs.open_paths.append(original / 'project.lumen'); window.open_project()
            require(window.project.to_dict() == saved, 'UI reopen lost exact project data.')
            window.player.stop(); window.player.setSource(QUrl())
            window.editor = Editor(Project()); window.project_path = None; window.dirty = False; window.refresh()
            moved = folder / 'project-relocated'; original.rename(moved)
            dialogs.open_paths.append(moved / 'project.lumen'); window.open_project()
            expected = copy.deepcopy(saved); expected['media'][0]['path'] = str(moved / 'assets' / 'full-voice.wav')
            require(window.project.to_dict() == expected and not window.project.missing_media(),
                    'Relocation did not preserve paths, IDs, clip and immutable voice provenance.')
            take = moved / 'assets' / 'full-voice.wav'
            require(sha256(take) == media.speech_provenance.output_sha256, 'Relocation changed saved speech bytes.')
            dialogs.require_no_warnings()
            report['project'] = {'file': 'project-relocated/project.lumen', 'sha256': sha256(moved / 'project.lumen'),
                                 'take_file': 'project-relocated/assets/full-voice.wav', 'reopen_exact': True,
                                 'relocated_without_relink': True, 'provenance_unchanged': True}

            checkpoint(args, report, 'real shared-graph preview and export')
            window.build_preview(); require(window.preview_job is not None, 'Preview task did not start.')
            settle('real timeline preview')
            preview = window.preview_paths.get((window.revision, window.preview_mode))
            require(preview is not None and Path(preview).is_file(), 'No current preview was produced.')
            preview_path = folder / 'preview.mp4'; shutil.copyfile(preview, preview_path)
            window.seek_frame(args.timeline_start); window.player.play()
            wait(lambda: not window.video.image.isNull(), 'actual Qt video decode', 20)
            window.player.pause()
            report['qt_preview_decoded'] = True
            report['screenshots'].append(screenshot(app, window, folder, '06-relocated-preview.png'))
            export_path = folder / 'export.mp4'; dialogs.save_paths.append(export_path); window.export_dialog()
            require(window.export_job is not None, 'Export task did not start.'); settle('real timeline export')
            require(len(recorder.graphs) == 2 and recorder.graphs[0]['sha256'] == recorder.graphs[1]['sha256'],
                    'Preview and export did not execute the identical normalized filter graph.')
            report['shared_graph'] = recorder.graphs
            current_clip = window.project.clips[0]
            plan = compile_audio_timing(current_clip, window.project.fps, window.project.media[0].audio_offset)
            budget = sample_boundary(window.project.duration_frames, window.project.fps, 48000)
            graph = (folder / recorder.graphs[0]['filename']).read_text(encoding='utf-8')
            require(f'adelay={plan.start_sample}S:all=1' in graph and f'atrim=end_sample={budget}[aout]' in graph,
                    'Actual renderer graph does not carry exact sample placement/budget.')
            reference = decode_pcm(take); rendered = []
            report['outputs'] = []
            for path in (preview_path, export_path):
                output = media_evidence(path); samples = decode_pcm(path); rendered.append(samples)
                output['pcm_timing'] = compare_pcm(reference, samples, plan.start_sample, budget)
                video = next(item for item in output['streams'] if item['codec_type'] == 'video')
                require((video['width'], video['height'], int(video['nb_read_frames'])) == (640, 360, window.project.duration_frames),
                        'Unexpected rendered geometry/frame count.')
                report['outputs'].append(output)
            require(np.array_equal(rendered[0], rendered[1]), 'Preview/export decoded audio samples differ.')
            report['preview_export_decoded_pcm_identical'] = True
            report['audio_timing_plan'] = asdict(plan)
            report['frame_count'] = window.project.duration_frames
            report['render_duration_seconds'] = float(Fraction(window.project.duration_frames, 1) / window.project.fps)
            report['native_pcm_timing'] = {'sample_rate': SAMPLE_RATE,
                'sample_count': media.speech_provenance.sample_count, 'duration_seconds': media.duration,
                'expected_clip_frames': exact_frames, 'clip_frames': current_clip.duration,
                'render_sample_rate': 48000, 'render_sample_budget': budget,
                'exact_sample_count_survived_undo_save_reopen_relocation': True}
            require(window.project.to_dict() == expected, 'Rendering changed the project.')
            require(not dialogs.open_paths and not dialogs.save_paths and not dialogs.overflow_answers and not dialogs.pronunciation_answers,
                    'An explicitly requested UI operation was not consumed.')
            checkpoint(args, report, 'complete')
        finally:
            cleanup_errors = []
            try:
                if window is not None:
                    window.closing = True
                    window.cancel_jobs()
                    cleanup_deadline = time.monotonic() + 30
                    for job in list(window.jobs):
                        require(job.wait(max(0, int((cleanup_deadline - time.monotonic()) * 1000))),
                                'A real background task did not stop during bounded cleanup.')
                    app.processEvents()
                    window.close_tts_preview(); window.player.stop(); window.player.setSource(QUrl())
                    window.dirty = False
                cleanup_test_windows(app, existing_windows, timeout_ms=5000)
            except Exception as exc:
                cleanup_errors.append(f'{type(exc).__name__}: {exc}')
            report['messages'] = dialogs.messages
            report['processes'] = recorder.evidence()
            report['cleanup'] = {'all_recorded_processes_exited': all(p.poll() is not None for p in recorder.processes),
                                 'qt_roots_destroyed': not cleanup_errors, 'errors': cleanup_errors}
            require(not cleanup_errors and report['cleanup']['all_recorded_processes_exited'],
                    'Bounded Qt/process cleanup failed: ' + '; '.join(cleanup_errors))


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--model-pack', type=local_pack, required=True, help='Existing, separately audited official Melo Chinese-English pack only.')
    parser.add_argument('--asset-manifest', '--audit-manifest', dest='asset_manifest', type=local_file,
                        help='Optional existing audited manifest; catalog pins are always verified without it.')
    parser.add_argument('--deadline', type=float, default=900, help='Overall Qt workflow deadline in seconds; cleanup gets at most 35 extra seconds.')
    parser.add_argument('--output-dir', required=True, help='A fresh output directory outside the pack.')
    parser.add_argument('--ui-language', choices=('en', 'zh-CN'), default='en')
    parser.add_argument('--font-family', default='Noto Sans CJK SC', help='Already-installed local Chinese font for the original timeline cue.')
    parser.add_argument('--track', type=int, choices=(2, 3), default=2)
    parser.add_argument('--timeline-start', type=int, default=15)
    parser.add_argument('--timeout', type=float, default=180, help='Positive seconds per actual operation.')
    args = parser.parse_args(argv)
    if (not math.isfinite(args.timeout) or args.timeout <= 0 or not math.isfinite(args.deadline)
            or args.deadline <= 0 or args.timeline_start < 3):
        parser.error('timeout and deadline must be finite and positive; timeline-start must be at least 3 frames to test leading silence.')
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
              'python': platform.python_version(), 'downloads_performed': False, 'installations_performed': False,
              'pack_version': PACK_VERSION, 'sampled_voice_ids': list(VOICE_IDS), 'sampled_voice_count': len(VOICE_IDS),
              'catalog_voice_count': 1, 'all_catalog_voices_synthesized': False, 'experimental': True,
              'speech_quality_evaluated': False, 'physical_speaker_playback_verified': False,
              'native_windows_macos_validated': False,
              'network_denial': 'Not asserted by this script. Use the external run_network_denied.py harness and retain its log.'}
    began = time.monotonic()
    try:
        report['pack'] = pack_evidence(args.model_pack, args.asset_manifest)
        report['runtime'] = runtime_evidence()
        require(all(report['runtime']['packages'][name]['version'] == '1.12.26'
                    for name in ('sherpa-onnx', 'sherpa-onnx-core')), 'Runtime is not pinned to 1.12.26.')
        root = Path(__file__).resolve().parents[1]
        report['source_sha256'] = source_evidence(root)
        run_workflow(args, report)
        report['status'] = 'passed'
        report['all_catalog_voices_synthesized'] = True
    except Exception as exc:
        report.update(status='failed', error=f'{type(exc).__name__}: {exc}', traceback=traceback.format_exc())
    try:
        report['pack_unchanged'] = 'pack' in report and pack_evidence(args.model_pack, args.asset_manifest) == report['pack']
        if not report['pack_unchanged']:
            report.update(status='failed', input_verification_error='Model pack identity was not established or changed.')
    except Exception as exc:
        report.update(status='failed', pack_unchanged=False, input_verification_error=str(exc))
    if 'source_sha256' in report:
        root = Path(__file__).resolve().parents[1]
        report['source_sha256_after'] = source_evidence(root)
        report['source_unchanged'] = report['source_sha256_after'] == report['source_sha256']
        if not report['source_unchanged']:
            report.update(status='failed', source_verification_error='Source changed during acceptance; rerun after a freeze.')
    report['elapsed_seconds'] = time.monotonic() - began
    evidence = args.output_dir / 'acceptance.json'
    evidence.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str) + '\n', encoding='utf-8')
    print(json.dumps({'status': report['status'], 'stage': report['stage'], 'evidence': str(evidence),
                      'error': report.get('error')}, ensure_ascii=False, indent=2))
    return 0 if report['status'] == 'passed' else 1


if __name__ == '__main__':
    raise SystemExit(main())
