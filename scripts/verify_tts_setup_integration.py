#!/usr/bin/env python3
"""Real Qt Kokoro v1.1 acceptance using an explicitly supplied local pack.

No downloads, installation, generated speech substitutes, or mocked inference.
Run only after separately auditing the existing pack and runtime. For Linux
network-denied evidence, invoke this runner *inside* the separate harness:

  python scripts/run_network_denied.py -- python \
    scripts/verify_tts_setup_integration.py --model-pack /local/kokoro-v1_1 \
    --output-dir artifacts/tts-setup-new

All four sampled IDs (0, 2, 3, 58) synthesize original English AND Chinese text
through the real Qt audition action. Cancellation waits for a real worker PID;
its retry, a separately saved take, explicit overflow decline/accept, undo/redo,
save/reopen/relocate, real shared-graph preview/export and decoded PCM are checked.
The runner automates file chooser answers and records message boxes only. It
observes real jobs/processes without changing inference, render or playback.
Screenshots are actual Qt widgets. Passing does not establish voice quality,
all-103-voice coverage, physical speaker playback or native desktop acceptance.
"""
from __future__ import annotations

import argparse
from contextlib import ExitStack
import copy
from dataclasses import asdict
from importlib import metadata
import json
import math
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import threading
import time
from unittest.mock import patch
import wave

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.verify_asr_setup_workflow import (
    AcceptanceError, DialogAutomation, media_evidence, normalized_graph,
    require, sha256, wait_until,
)

PACK_VERSION = 'v1.1'
VOICE_IDS = (0, 2, 3, 58)
TEXTS = {
    'en': 'A new scene begins here. We can keep the full voice.',
    'zh': '新的画面从这里开始。我们可以保留完整的声音。',
}
TAKE_TEXT = 'A new scene begins here.'


def local_pack(value):
    """Reject remote paths and non-directories without importing Qt/runtime."""
    raw = str(value)
    if '://' in raw or raw.startswith(('//', '\\\\')):
        raise argparse.ArgumentTypeError('Only an existing local model-pack directory is accepted.')
    try:
        original = Path(value).expanduser()
        if original.is_symlink():
            raise ValueError('symbolic link')
        path = original.resolve(strict=True)
        if not path.is_dir():
            raise ValueError('not a directory')
    except (OSError, ValueError) as exc:
        raise argparse.ArgumentTypeError('Select an existing regular local model-pack directory.') from exc
    return path


def pack_evidence(path):
    # The production asset walker bounds size/count, refuses symlinks/special
    # files, and hashes all assets without loading a model or starting a worker.
    from lumen.tts import _assets
    from lumen.voice_catalog import get_pack
    pack = get_pack(PACK_VERSION)
    entries = _assets(path, None)
    values = dict(entries)
    require(all(name in values for name in pack.required_files), 'The explicit v1.1 pack is incomplete.')
    require(values['model.onnx'] == pack.model_sha256 and values['voices.bin'] == pack.voices_sha256,
            'Model/voice hashes do not match the pinned v1.1 catalog.')
    import hashlib
    return {'path': str(path), 'pack_version': PACK_VERSION,
            'official_archive_url': pack.download_url,
            'expected_archive_sha256': pack.archive_sha256,
            'archive_verified_by_this_runner': False,
            'assets': dict(entries),
            'assets_sha256': hashlib.sha256(json.dumps(entries, ensure_ascii=False, separators=(',', ':')).encode()).hexdigest()}


def runtime_evidence():
    """Inspect distribution identities; no import of the native speech engine."""
    result = {'python_executable': str(Path(sys.executable).resolve()),
              'python_executable_sha256': sha256(Path(sys.executable).resolve()), 'packages': {}}
    for name in ('sherpa-onnx', 'sherpa-onnx-core', 'PySide6', 'numpy'):
        distribution = metadata.distribution(name)
        binaries = {}
        for item in distribution.files or ():
            path = Path(distribution.locate_file(item))
            if path.is_file() and (path.suffix.lower() in ('.so', '.pyd', '.dll', '.dylib') or '.so.' in path.name):
                binaries[str(item)] = sha256(path)
        result['packages'][name] = {'version': distribution.version, 'native_binary_sha256': binaries}
    from lumen.media import ffmpeg_binary, ffprobe_binary
    for name, binary in (('ffmpeg', ffmpeg_binary()), ('ffprobe', ffprobe_binary())):
        located = shutil.which(binary)
        require(located, f'{name} is not available; install it separately before acceptance.')
        result[name] = {'path': str(Path(located).resolve()), 'sha256': sha256(located),
                        'version': subprocess.run([located, '-version'], check=True, capture_output=True,
                                                  text=True, timeout=30).stdout.splitlines()[0]}
    return result


class TTSProcessRecorder:
    """Observe only real TTS worker and renderer processes, forwarding unchanged."""
    def __init__(self, folder):
        self.folder = Path(folder)
        self.original = subprocess.Popen
        self.records, self.processes, self.graphs = [], [], []
        self.lock = threading.Lock()

    def __call__(self, command, *args, **kwargs):
        argv = [os.fsdecode(value) for value in command] if isinstance(command, (list, tuple)) else []
        worker = (len(argv) >= 3 and argv[1:3] == ['-m', 'lumen.tts_worker']) or '--lumen-tts-worker' in argv
        request = None
        if worker:
            request = json.loads(Path(argv[-2]).read_text(encoding='utf-8'))
        graph = None
        if '-/filter_complex' in argv:
            path = Path(argv[argv.index('-/filter_complex') + 1])
            graph = normalized_graph(path.read_text(encoding='utf-8'), path.parent)
        elif '-filter_complex' in argv and '[aout]' in argv[argv.index('-filter_complex') + 1]:
            graph = argv[argv.index('-filter_complex') + 1]
        process = self.original(command, *args, **kwargs)
        if worker or graph is not None:
            with self.lock:
                self.records.append({'pid': process.pid, 'argv': argv, 'is_tts_worker': worker,
                                     'request': request, 'cwd': os.fsdecode(kwargs.get('cwd') or os.getcwd())})
                self.processes.append(process)
                if graph is not None:
                    import hashlib
                    filename = f'render-graph-{len(self.graphs) + 1}.txt'
                    (self.folder / filename).write_text(graph, encoding='utf-8')
                    self.graphs.append({'filename': filename, 'pid': process.pid,
                                        'sha256': hashlib.sha256(graph.encode()).hexdigest()})
        return process

    def workers(self):
        with self.lock:
            return [p for record, p in zip(self.records, self.processes) if record['is_tts_worker']]

    def evidence(self):
        with self.lock:
            return [{**record, 'returncode': p.poll()} for record, p in zip(self.records, self.processes)]


class TTSDialogs(DialogAutomation):
    """Only the two deliberate overflow choices are authorized in this runner."""
    def __init__(self):
        super().__init__()
        self.overflow_answers = []

    def question(self, parent, title, text, *args, **kwargs):
        from PySide6.QtWidgets import QMessageBox
        # Exact production strings for the supported runner locales. Fail closed
        # on an unrelated dialog instead of spending a queued answer on it.
        from lumen.i18n import tr
        if str(title) == tr('Keep the full voice?') and self.overflow_answers:
            keep = self.overflow_answers.pop(0)
            self.messages.append({'kind': 'overflow', 'title': str(title), 'text': str(text), 'keep_full_voice': keep})
            return QMessageBox.StandardButton.Yes if keep else QMessageBox.StandardButton.No
        return super().question(parent, title, text, *args, **kwargs)


def waveform_evidence(path, destination):
    """Measure actual generated PCM; never infer spoken accuracy from energy."""
    import numpy as np
    with wave.open(str(path), 'rb') as wav:
        require((wav.getnchannels(), wav.getsampwidth(), wav.getframerate()) == (1, 2, 24000),
                'Generated voice is not mono 24 kHz signed PCM16.')
        count = wav.getnframes()
        values = np.frombuffer(wav.readframes(count), dtype='<i2').astype(np.float64) / 32768
    require(count > 2400 and np.isfinite(values).all(), 'Generated speech is empty, too short or nonfinite.')
    peak = float(np.max(np.abs(values)))
    rms = float(np.sqrt(np.mean(values ** 2)))
    require(peak > .01 and rms > .001, 'Actual generated PCM is silent or effectively empty.')
    blocks = [chunk for chunk in np.array_split(values, min(200, len(values))) if len(chunk)]
    envelope, offset = [], 0
    for chunk in blocks:
        envelope.append({'start_sample': offset, 'sample_count': len(chunk), 'minimum': float(chunk.min()),
                         'maximum': float(chunk.max()), 'rms': float(np.sqrt(np.mean(chunk ** 2)))})
        offset += len(chunk)
    Path(destination).write_text(json.dumps({'source_sha256': sha256(path), 'sample_rate': 24000,
                                            'sample_count': count, 'envelope': envelope}, indent=2) + '\n')
    return {'file': Path(path).name, 'sha256': sha256(path), 'sample_rate': 24000,
            'sample_count': count, 'duration_seconds': count / 24000, 'peak': peak, 'rms': rms,
            'nonzero_samples': int(np.count_nonzero(values)), 'waveform_file': Path(destination).name}


def decode_pcm(path):
    import numpy as np
    from lumen.media import ffmpeg_binary
    command = [ffmpeg_binary(), '-v', 'error', '-nostdin', '-protocol_whitelist', 'file,pipe',
               '-i', str(path), '-map', '0:a:0', '-vn', '-ac', '1', '-ar', '48000', '-f', 'f32le', 'pipe:1']
    result = subprocess.run(command, check=True, capture_output=True, timeout=120)
    return np.frombuffer(result.stdout, dtype='<f4').astype(np.float64)


def compare_pcm(reference, output, start_sample, budget):
    """AAC-aware real signal comparison at the exact compiled timeline offset."""
    import numpy as np
    require(start_sample >= 0 and 0 < len(reference) <= budget - start_sample,
            'Reference voice exceeds the timeline sample budget.')
    require(np.isfinite(reference).all() and float(np.linalg.norm(reference)) > .001,
            'Reference voice must contain finite measurable PCM.')
    require(budget <= len(output) <= budget + 2048,
            f'Decoded AAC sample count {len(output)} is inconsistent with budget {budget}.')
    require(np.isfinite(output).all(), 'Decoded render contains nonfinite PCM.')
    expected = output[start_sample:start_sample + len(reference)]
    denominator = float(np.linalg.norm(reference) * np.linalg.norm(expected))
    correlation = float(np.dot(reference, expected) / denominator) if denominator else 0.0
    gain_ratio = float(np.linalg.norm(expected) / np.linalg.norm(reference))
    # Do not fit out a shift and hide it: comparison at the prescribed exact
    # offset must pass; independently report the best local lag as a diagnostic.
    scores = {}
    for lag in range(-48, 49):
        begin = start_sample + lag
        if begin < 0 or begin + len(reference) > len(output):
            continue
        segment = output[begin:begin + len(reference)]
        norm = float(np.linalg.norm(reference) * np.linalg.norm(segment))
        scores[lag] = float(np.dot(reference, segment) / norm) if norm else 0.0
    best_lag = max(scores, key=scores.get)
    lead = output[:max(0, start_sample - 1024)]  # allow AAC pre-echo at the edge
    lead_peak = float(np.max(np.abs(lead), initial=0))
    require(correlation >= .97 and .9 <= gain_ratio <= 1.1,
            f'Rendered speech does not match the original at its timeline position: r={correlation}, gain={gain_ratio}.')
    require(abs(best_lag) <= 24, f'Rendered waveform shifted {best_lag} samples from the expected offset.')
    require(lead_peak < .001, 'Unexpected sound occurs before the inserted take.')
    return {'prescribed_start_sample': start_sample, 'timeline_sample_budget': budget,
            'reference_sample_count': len(reference), 'decoded_sample_count': len(output),
            'aac_decoded_padding_samples': len(output) - budget, 'exact_offset_correlation': correlation,
            'rms_gain_ratio': gain_ratio, 'best_local_lag_samples': best_lag,
            'leading_silence_peak': lead_peak}


def screenshot(app, widget, folder, filename):
    app.processEvents()
    path = folder / filename
    require(widget.grab().save(str(path)), f'Cannot save actual Qt screenshot: {filename}')
    return {'file': filename, 'sha256': sha256(path), 'source': 'actual Qt widget grab'}


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

    folder = args.output_dir
    app = QApplication.instance() or QApplication([])
    QSettings.setDefaultFormat(QSettings.Format.IniFormat)
    for scope in (QSettings.Scope.UserScope, QSettings.Scope.SystemScope):
        QSettings.setPath(QSettings.Format.IniFormat, scope, str(folder / 'settings'))
    workspace = QSettings(str(folder / 'settings' / 'workspace.ini'), QSettings.Format.IniFormat)
    shortcuts = QSettings(str(folder / 'settings' / 'shortcuts.ini'), QSettings.Format.IniFormat)
    dialogs, recorder = TTSDialogs(), TTSProcessRecorder(folder)
    report.update(qt_binding=qt_version, qt_platform=app.platformName(), ui_language=args.ui_language,
                  audio_output_devices=[device.description() for device in QMediaDevices.audioOutputs()],
                  automation_boundaries=['explicit local file chooser answers', 'recorded message boxes with explicit overflow No/Yes',
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
            window.editor = Editor(Project(name='Real local TTS setup acceptance', width=640, height=360))
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
                wait_until(app, lambda: not window.jobs, args.timeout, description)
                dialogs.require_no_warnings()
            report['stage'] = 'actual Qt component setup metadata diagnostics'
            setup = ComponentSetupDialog(window, path=folder / 'settings' / 'component-setup.json')
            setup.fields['tts_model_dir'].setText(str(args.model_pack))
            setup.pack.setCurrentIndex(setup.pack.findData(PACK_VERSION)); setup.hashes.setChecked(True)
            setup.show(); setup.check_button.click(); settle(report['stage'])
            require(setup._report is not None, 'Component metadata diagnostics produced no report.')
            diagnostics = asdict(setup._report)
            require(not diagnostics['inference_tested'] and not recorder.workers(), 'Metadata diagnostics ran inference.')
            checks = {item['key']: item for item in diagnostics['checks'] if item['component'] == 'tts'}
            require(checks['model.onnx']['status'] == checks['voices.bin']['status'] == 'hash_verified',
                    'Component setup did not verify the pinned model and voice bank.')
            require(all(item['status'] in ('metadata_available', 'hash_verified') for item in checks.values()),
                    f'Component setup TTS checks failed: {checks}')
            setup.apply_button.click()
            require(load_preferences(setup.path)['tts_model_dir'] == str(args.model_pack), 'Setup path was not saved.')
            require(operational_settings(window)['tts_pack_version'] == PACK_VERSION
                    and operational_settings(window)['tts_model_dir'] == str(args.model_pack), 'Setup was not applied.')
            unchanged()
            report['diagnostics'] = diagnostics
            report['screenshots'].append(screenshot(app, setup, folder, '01-component-setup.png')); setup.reject()
            report['stage'] = 'actual Qt full-pack and pinned runtime verification'
            window.tts_verify_button.click(); settle(report['stage'])
            require(window._tts_verified_pack == window._tts_pack_key() and window.tts_preview_button.isEnabled(),
                    'Real pack verification did not enable audition controls.')
            require(not recorder.workers(), 'Pack verification unexpectedly synthesized speech.')
            unchanged(); report['setup_applied_without_project_edit'] = True
            window.side_tabs.setCurrentIndex(1)
            window.side_tabs.widget(1).ensureWidgetVisible(window.tts_preview_status)
            report['screenshots'].append(screenshot(app, window, folder, '02-verified-voice-browser.png'))

            report['stage'] = 'cancel real audition worker and retry'
            window.tts_voice.setCurrentIndex(window.tts_voice.findData(0))
            window.tts_preview_text.setPlainText((TEXTS['en'] + ' ') * 30)
            window.tts_preview_button.click()
            job = window.tts_job
            require(job is not None and job is window._tts_preview_task, 'Qt audition task did not start.')
            window.preview_tts_voice()
            require(window.tts_job is job and len(window.jobs) == 1, 'Duplicate audition created another task.')
            wait_until(app, lambda: recorder.workers() or not window.jobs, args.timeout, 'real TTS worker PID')
            active = recorder.workers()
            require(len(active) == 1 and active[0].poll() is None,
                    'No live worker remained to cancel; cancellation cannot be claimed.')
            began = time.monotonic(); window.tts_preview_stop_button.click()
            wait_until(app, lambda: not window.jobs and window._tts_preview_task is None,
                       min(args.timeout, 30), 'canceled TTS cleanup')
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
                    report['stage'] = 'real Qt audition ' + name
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
                    require(provenance['engine'] == 'sherpa-onnx/Kokoro-v1.1'
                            and provenance['engine_version'] == report['runtime']['packages']['sherpa-onnx']['version']
                            and provenance['model_sha256'] == report['pack']['assets']['model.onnx']
                            and provenance['voice_sha256'] == report['pack']['assets']['voices.bin']
                            and provenance['assets_sha256'] == report['pack']['assets_sha256']
                            and provenance['sample_rate'] == 24000 and provenance['speed'] == 1.0,
                            'Audition provenance differs from the exact audited pack/runtime/settings.')
                    destination = folder / (name + '.wav'); shutil.copyfile(source, destination)
                    record = {'name': name, 'voice_id': voice_id, 'voice_name': get_voice(PACK_VERSION, voice_id).name,
                              'language': language, 'original_text': text, 'elapsed_seconds': time.monotonic() - began,
                              'provenance': result['provenance'],
                              'waveform': waveform_evidence(destination, folder / (name + '-waveform.json'))}
                    report['cases'].append(record)
                    window.tts_preview_play_button.click(); app.processEvents(); window.tts_preview_stop_button.click()
                    unchanged()
                    record['replay_stop_preserved_project'] = True
                    record['physical_audio_output_verified'] = False
                    if voice_id in (0, 58) and language == 'zh':
                        report['screenshots'].append(screenshot(app, window, folder, name + '-audition.png'))
            require(len(report['cases']) == 8, 'Not every requested bilingual voice case ran.')
            report['cancellation']['retry_succeeded'] = True
            report['auditions_preserved_project_revision_dirty_state'] = True

            report['stage'] = 'saved take and explicit overflow decline then accept'
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
            require(take.is_file() and window.pending_speech is not None and not dialogs.overflow_answers,
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
            dialogs.overflow_answers.append(True); window.tts_insert_button.click(); app.processEvents()
            require(not dialogs.overflow_answers and window.pending_speech is None, 'Explicit full-take acceptance failed.')
            require(len(window.project.media) == len(window.project.clips) == 1, 'Insertion did not create exactly one take.')
            clip = window.selected_clip(); inserted = copy.deepcopy(window.project.to_dict())
            require(clip.track == args.track and clip.start == args.timeline_start and clip.duration == window.project.media_frames(media)
                    and clip.duration > cue.duration and window.project.captions[0].duration == 1,
                    'Full voice/caption timing was unexpectedly shortened or changed.')
            require(window.revision == revision + 1, 'Saved-take insertion is not one editor transaction.')
            window.undo(); require(window.project.to_dict() == baseline, 'Undo did not remove the entire insertion.')
            require(take.is_file(), 'Undo removed the durable saved WAV.')
            window.redo(); require(window.project.to_dict() == inserted, 'Redo changed speech IDs, timing or provenance.')
            report['overflow'] = {'declined_without_mutation': True, 'accepted_full_take': True, 'caption_unchanged': True,
                                  'clip': asdict(clip), 'undo_redo_exact': True}
            report['screenshots'].append(screenshot(app, window, folder, '05-inserted-full-take.png'))

            report['stage'] = 'actual Qt save reopen and package relocation'
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

            report['stage'] = 'real shared-graph preview and export'
            window.build_preview(); require(window.preview_job is not None, 'Preview task did not start.')
            settle('real timeline preview')
            preview = window.preview_paths.get((window.revision, window.preview_mode))
            require(preview is not None and Path(preview).is_file(), 'No current preview was produced.')
            preview_path = folder / 'preview.mp4'; shutil.copyfile(preview, preview_path)
            window.seek_frame(args.timeline_start); window.player.play()
            wait_until(app, lambda: not window.video.image.isNull(), min(args.timeout, 20), 'actual Qt video decode')
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
            require(window.project.to_dict() == expected, 'Rendering changed the project.')
            require(not dialogs.open_paths and not dialogs.save_paths and not dialogs.overflow_answers,
                    'An explicitly requested UI operation was not consumed.')
            report['stage'] = 'complete'
        finally:
            if window is not None:
                window.cancel_jobs()
                for job in list(window.jobs):
                    require(job.wait(30000), 'A real background task did not stop during cleanup.')
                app.processEvents(); window.close_tts_preview(); window.player.stop(); window.player.setSource(QUrl())
                window.dirty = False; window.closing = True; window.close(); app.processEvents()
            report['messages'] = dialogs.messages
            report['processes'] = recorder.evidence()


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--model-pack', type=local_pack, required=True, help='Existing, separately audited official v1.1 pack only.')
    parser.add_argument('--output-dir', required=True, help='A fresh output directory outside the pack.')
    parser.add_argument('--ui-language', choices=('en', 'zh-CN'), default='en')
    parser.add_argument('--font-family', default='DejaVu Sans', help='Already-installed local face for the English timeline cue.')
    parser.add_argument('--track', type=int, choices=(2, 3), default=2)
    parser.add_argument('--timeline-start', type=int, default=15)
    parser.add_argument('--timeout', type=float, default=600, help='Positive seconds per actual operation.')
    args = parser.parse_args(argv)
    if not math.isfinite(args.timeout) or args.timeout <= 0 or args.timeline_start < 3:
        parser.error('timeout must be finite and positive; timeline-start must be at least 3 frames to test leading silence.')
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
              'catalog_voice_count': 103, 'all_catalog_voices_synthesized': False,
              'speech_quality_evaluated': False, 'physical_speaker_playback_verified': False,
              'native_windows_macos_validated': False,
              'network_denial': 'Not asserted by this script. Use the external run_network_denied.py harness and retain its log.'}
    began = time.monotonic()
    try:
        report['pack'] = pack_evidence(args.model_pack)
        report['runtime'] = runtime_evidence()
        root = Path(__file__).resolve().parents[1]
        report['source_sha256'] = {name: sha256(root / name) for name in (
            'scripts/verify_tts_setup_integration.py', 'scripts/verify_asr_setup_workflow.py',
            'scripts/run_network_denied.py', 'lumen/tts.py', 'lumen/tts_worker.py', 'lumen/voice_catalog.py',
            'lumen/ui.py', 'lumen/ui_captions.py', 'lumen/ui_component_setup.py', 'lumen/component_setup.py',
            'lumen/model.py', 'lumen/render.py', 'lumen/audio_timing.py')}
        run_workflow(args, report)
        report['status'] = 'passed'
    except Exception as exc:
        report.update(status='failed', error=f'{type(exc).__name__}: {exc}')
    try:
        report['pack_unchanged'] = 'pack' in report and pack_evidence(args.model_pack) == report['pack']
        if not report['pack_unchanged']:
            report.update(status='failed', input_verification_error='Model pack identity was not established or changed.')
    except Exception as exc:
        report.update(status='failed', pack_unchanged=False, input_verification_error=str(exc))
    report['elapsed_seconds'] = time.monotonic() - began
    evidence = args.output_dir / 'acceptance.json'
    evidence.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str) + '\n', encoding='utf-8')
    print(json.dumps({'status': report['status'], 'stage': report['stage'], 'evidence': str(evidence),
                      'error': report.get('error')}, ensure_ascii=False, indent=2))
    return 0 if report['status'] == 'passed' else 1


if __name__ == '__main__':
    raise SystemExit(main())
