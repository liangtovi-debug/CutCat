#!/usr/bin/env python3
"""Actual Qt setup -> whisper.cpp -> editable captions -> preview/export.

Development acceptance runner, not a downloader or runtime installer. Supply
trusted, separately verified local runtime/model files and a synthetic speech
fixture. It never substitutes a transcript, inference adapter, render engine,
or worker task. Native file choosers/message boxes are automated; application
settings are isolated in a NEW output directory. Real subprocess launches are
observed without changing their arguments, environment, result, or lifetime.

Example (all inputs must already exist):
  python scripts/verify_asr_setup_workflow.py --runtime /local/whisper-cli \
    --model /local/ggml-tiny.bin --fixture /local/synthetic-en.wav \
    --language en --font-family "DejaVu Sans" --exercise-interruptions \
    --output-dir artifacts/asr-setup-en

Exit 0 means every requested check passed; failures retain partial evidence
with status=failed. Recognition accuracy is NOT inferred from nonempty output.
An offscreen run does not establish macOS/Windows desktop acceptance.
"""
from __future__ import annotations

import argparse
from contextlib import ExitStack
import copy
from dataclasses import asdict
import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import threading
import textwrap
import time
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


class AcceptanceError(RuntimeError):
    pass


def require(condition, message):
    if not condition:
        raise AcceptanceError(message)


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def reviewed_caption_text(text):
    """An explicit UI edit; the production renderer does not auto-wrap cues."""
    width = 42 if text.isascii() else 22
    return textwrap.fill(text, width=width, break_on_hyphens=False) + '\n[UI edit test]'


def local_file(value):
    """Fail before creating output or importing Qt for invalid explicit inputs."""
    raw = str(value)
    if '://' in raw or raw.startswith(('//', '\\\\')):
        raise argparse.ArgumentTypeError('Only existing local files are accepted.')
    try:
        path = Path(value).expanduser().resolve(strict=True)
        if not path.is_file() or path.stat().st_size == 0:
            raise ValueError('not a nonempty regular file')
    except (OSError, ValueError) as exc:
        raise argparse.ArgumentTypeError(f'Not a nonempty local file: {value}') from exc
    return path


def wait_until(app, condition, timeout, description):
    deadline = time.monotonic() + timeout
    while not condition():
        app.processEvents()
        if time.monotonic() >= deadline:
            raise AcceptanceError(f'Timed out waiting for {description}.')
        time.sleep(.01)
    # QThread finished and result notifications are queued independently.
    app.processEvents()


def normalized_graph(graph, cache_directory):
    """Remove only the generated cache root, preserving cue/font content IDs."""
    from lumen.caption_render import _filter_path
    marker = '__acceptance_placeholder__'
    escaped = _filter_path(Path(cache_directory) / marker)
    prefix = escaped[1:-len(marker + "'")]
    return graph.replace(prefix, '{render-cache}/')


class ProcessRecorder:
    """Transparent observation of real native processes; no synthetic outputs."""
    def __init__(self, runtime, folder):
        self.runtime = Path(runtime).resolve()
        self.folder = Path(folder)
        self.original = subprocess.Popen
        self.records = []
        self.processes = []
        self.graphs = []
        self.lock = threading.Lock()

    def __call__(self, command, *args, **kwargs):
        argv = [os.fsdecode(value) for value in command] if isinstance(command, (list, tuple)) else []
        is_runtime = bool(argv and Path(argv[0]).resolve() == self.runtime)
        graph = None
        if '-/filter_complex' in argv:
            path = Path(argv[argv.index('-/filter_complex') + 1])
            raw = path.read_text(encoding='utf-8')
            normalized = normalized_graph(raw, path.parent)
            graph = {'sha256': hashlib.sha256(normalized.encode()).hexdigest(),
                     'caption_drawtext_count': normalized.count('drawtext='),
                     'normalized': normalized}
        process = self.original(command, *args, **kwargs)
        if is_runtime or graph is not None:
            with self.lock:
                record = {'pid': process.pid, 'argv': argv, 'is_runtime': is_runtime,
                          'cwd': os.fsdecode(kwargs.get('cwd') or os.getcwd())}
                self.records.append(record)
                self.processes.append(process)
                if graph is not None:
                    index = len(self.graphs) + 1
                    filename = f'render-graph-{index}.txt'
                    (self.folder / filename).write_text(graph.pop('normalized'), encoding='utf-8')
                    graph.update(filename=filename, pid=process.pid)
                    self.graphs.append(graph)
        return process

    def runtime_processes(self):
        with self.lock:
            return [process for record, process in zip(self.records, self.processes)
                    if record['is_runtime']]

    def evidence(self):
        with self.lock:
            return [{**record, 'returncode': process.poll()}
                    for record, process in zip(self.records, self.processes)]


class DialogAutomation:
    """Explicit local file destinations and recorded messages; never infer Yes."""
    def __init__(self):
        self.messages = []
        self.open_paths = []
        self.save_paths = []

    def open(self, *_args, **_kwargs):
        require(self.open_paths, 'Unexpected open-file dialog.')
        return str(self.open_paths.pop(0)), ''

    def save(self, *_args, **_kwargs):
        require(self.save_paths, 'Unexpected save-file dialog.')
        return str(self.save_paths.pop(0)), ''

    def message(self, kind, _parent, title, text, *_args, **_kwargs):
        self.messages.append({'kind': kind, 'title': str(title), 'text': str(text)})
        from PySide6.QtWidgets import QMessageBox
        return QMessageBox.StandardButton.Ok

    def question(self, _parent, title, text, *_args, **_kwargs):
        self.messages.append({'kind': 'unexpected_question', 'title': str(title), 'text': str(text)})
        from PySide6.QtWidgets import QMessageBox
        return QMessageBox.StandardButton.Cancel

    def require_no_warnings(self, since=0):
        unexpected = [item for item in self.messages[since:]
                      if item['kind'] in ('warning', 'unexpected_question')]
        require(not unexpected, f'Unexpected UI diagnostics: {unexpected}')


def media_evidence(path):
    from lumen.media import ffprobe_binary
    command = [ffprobe_binary(), '-v', 'error', '-count_frames', '-show_entries',
               'stream=codec_type,codec_name,width,height,nb_read_frames,avg_frame_rate,color_range,color_space,color_transfer,color_primaries',
               '-of', 'json', '-protocol_whitelist', 'file,pipe', str(path)]
    result = subprocess.run(command, check=True, capture_output=True, text=True, timeout=120)
    return {'file': Path(path).name, 'sha256': sha256(path),
            'streams': json.loads(result.stdout)['streams']}


def run_workflow(args, report):
    # This runner is a separate process: changes cannot affect an open editor.
    os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
    from PySide6 import __version__ as qt_binding_version
    from PySide6.QtCore import QSettings, QStandardPaths, QUrl
    from PySide6.QtWidgets import QApplication, QFileDialog, QMessageBox
    from lumen.component_setup import load_preferences
    from lumen.model import Editor, Project
    from lumen.subtitles import read_srt
    from lumen.ui import MainWindow
    from lumen.ui_component_setup import ComponentSetupDialog, operational_settings

    folder = args.output_dir
    app = QApplication.instance() or QApplication([])
    QSettings.setDefaultFormat(QSettings.Format.IniFormat)
    for scope in (QSettings.Scope.UserScope, QSettings.Scope.SystemScope):
        QSettings.setPath(QSettings.Format.IniFormat, scope, str(folder / 'settings'))
    workspace = QSettings(str(folder / 'settings' / 'workspace.ini'), QSettings.Format.IniFormat)
    shortcuts = QSettings(str(folder / 'settings' / 'shortcuts.ini'), QSettings.Format.IniFormat)
    dialogs = DialogAutomation()
    recorder = ProcessRecorder(args.runtime, folder)
    report.update(qt_binding=qt_binding_version, qt_platform=app.platformName(),
                  ui_language=args.ui_language,
                  native_windows_macos_validated=False,
                  recognition_accuracy_evaluated=False,
                  automation_boundaries=['local file chooser results', 'message-box capture',
                                         'isolated application config path', 'transparent subprocess observation'])
    original_location = QStandardPaths.writableLocation
    def location(kind):
        if kind == QStandardPaths.StandardLocation.AppConfigLocation:
            return str(folder / 'settings')
        return original_location(kind)

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
            window.editor = Editor(Project(name='Actual ASR setup acceptance', width=640, height=360))
            window.refresh()
            window.resize(1440, 1000)
            window.show()
            app.processEvents()
            report['stage'] = 'import synthetic speech fixture'
            window.import_paths([str(args.fixture)])
            wait_until(app, lambda: not window.jobs, args.timeout, report['stage'])
            dialogs.require_no_warnings()
            require(len(window.project.media) == 1 and window.project.media[0].has_audio,
                    'The explicit synthetic fixture must contain audio.')
            # Audio-only fixtures use the audio track; the renderer supplies a
            # black canvas. A video fixture retains its actual synthetic pixels.
            window.track_choice.setCurrentIndex(0 if window.project.media[0].has_video else 2)
            window.bin.setCurrentRow(0)
            window.add_selected_media()
            require(window.selected_clip() is not None, 'Fixture was not added to the timeline.')
            window.start_spin.setValue(args.timeline_start)
            window.apply_timing()
            report['clip_before_asr'] = window.selected_clip().to_dict() if hasattr(window.selected_clip(), 'to_dict') else asdict(window.selected_clip())
            report['stage'] = 'component setup diagnostics and path application'
            before_setup = copy.deepcopy(window.project.to_dict())
            revision = window.revision
            preferences = folder / 'settings' / 'component-setup.json'
            setup = ComponentSetupDialog(window, path=preferences)
            setup.fields['asr_runtime'].setText(str(args.runtime))
            setup.fields['asr_model'].setText(str(args.model))
            setup.hashes.setChecked(True)
            setup.show()
            setup.check_button.click()
            wait_until(app, lambda: not window.jobs, args.timeout, report['stage'])
            require(setup._report is not None, f'Diagnostics failed: {setup.status.text()}')
            diagnostic = asdict(setup._report)
            require(not diagnostic['inference_tested'], 'Metadata diagnostics incorrectly claimed inference.')
            asr_checks = {item['key']: item for item in diagnostic['checks'] if item['key'] in ('asr_runtime', 'asr_model')}
            require(all(item['status'] == 'metadata_available' for item in asr_checks.values()) and len(asr_checks) == 2,
                    f'ASR file diagnostics did not pass: {asr_checks}')
            require(not recorder.runtime_processes(), 'Setup diagnostics unexpectedly launched the runtime.')
            setup.apply_button.click()
            require(load_preferences(preferences)['asr_model'] == str(args.model), 'Model path was not persisted.')
            require(operational_settings(window)['asr_runtime'] == str(args.runtime)
                    and operational_settings(window)['asr_model'] == str(args.model), 'Operational ASR paths were not applied.')
            require(window.project.to_dict() == before_setup and window.revision == revision,
                    'Component setup changed the timeline or undo revision.')
            app.processEvents()
            require(setup.grab().save(str(folder / '01-component-setup.png')), 'Cannot save setup screenshot.')
            report['diagnostics'] = diagnostic
            report['setup_applied_without_project_edit'] = True
            setup.reject()
            window.asr_language.setText(args.language)
            window.asr_source.setCurrentIndex(0)
            window.side_tabs.setCurrentIndex(1)
            before_recognition = copy.deepcopy(window.project.to_dict())

            def apply_model(model):
                dialog = ComponentSetupDialog(window, path=preferences)
                dialog.fields['asr_model'].setText(str(model))
                dialog.apply_button.click()
                require(window.asr_model.text() == str(model), 'Setup did not apply the requested model path.')
                dialog.reject()

            if args.exercise_interruptions:
                report['stage'] = 'real runtime rejects invalid model'
                bad_model = folder / 'deliberately-invalid-model.bin'
                bad_model.write_bytes(b'CutCat acceptance: deliberately invalid Whisper model\n')
                apply_model(bad_model)
                mark = len(dialogs.messages)
                processes_before = len(recorder.runtime_processes())
                window.asr_button.click()
                wait_until(app, lambda: not window.jobs, args.timeout, report['stage'])
                errors = dialogs.messages[mark:]
                native_errors = recorder.runtime_processes()[processes_before:]
                require(len(native_errors) == 1 and native_errors[0].poll() not in (0, None),
                        'Invalid-model check did not reach and fail in the actual runtime.')
                require(any(item['kind'] == 'warning' for item in errors), 'Runtime failure did not reach the UI.')
                require(window.project.to_dict() == before_recognition and window.asr_job is None,
                        'Runtime failure changed project data or retained a task.')
                report['invalid_model'] = {'passed': True, 'ui_messages': errors,
                                           'returncode': native_errors[0].returncode}
                apply_model(args.model)
                report['stage'] = 'cancel actual runtime and reject duplicate start'
                mark = len(dialogs.messages)
                processes_before = len(recorder.runtime_processes())
                window.asr_button.click()
                job = window.asr_job
                require(job is not None, 'Cancellation test failed to start ASR task.')
                window.transcribe_selected()
                require(window.asr_job is job and len(window.jobs) == 1, 'Repeated start created another ASR task.')
                wait_until(app, lambda: len(recorder.runtime_processes()) > processes_before or not window.jobs,
                           args.timeout, 'actual whisper.cpp process to launch')
                active = recorder.runtime_processes()[processes_before:]
                require(len(active) == 1 and active[0].poll() is None,
                        'Runtime completed before cancellation could be observed; cancellation is not verified.')
                began = time.monotonic()
                window.cancel_jobs()
                wait_until(app, lambda: not window.jobs, min(args.timeout, 30), 'ASR cancellation cleanup')
                require(active[0].poll() is not None, 'Canceled runtime was not reaped.')
                require(window.project.to_dict() == before_recognition and window.asr_job is None,
                        'Cancellation changed project data or retained a task.')
                dialogs.require_no_warnings(mark)
                report['cancellation'] = {'passed': True, 'runtime_pid': active[0].pid,
                                          'runtime_returncode': active[0].returncode,
                                          'cleanup_seconds': time.monotonic() - began,
                                          'duplicate_start_ignored': True}
            else:
                report['invalid_model'] = {'tested': False}
                report['cancellation'] = {'tested': False}

            report['stage'] = 'actual ASR through existing Qt task'
            mark = len(dialogs.messages)
            processes_before = len(recorder.runtime_processes())
            began = time.monotonic()
            window.asr_button.click()
            require(window.asr_job is not None, 'Generate captions did not start the existing ASR task.')
            wait_until(app, lambda: not window.jobs, args.timeout, report['stage'])
            dialogs.require_no_warnings(mark)
            successful = recorder.runtime_processes()[processes_before:]
            require(len(successful) == 1 and successful[0].poll() == 0, 'Actual runtime did not finish successfully.')
            require(window.project.captions, 'Real recognition produced no editable caption cues.')
            require(window.caption_list.count() == len(window.project.captions), 'Recognized cues did not reach the Qt caption list.')
            report['recognition'] = {'elapsed_seconds': time.monotonic() - began,
                                     'captions': [asdict(cue) for cue in window.project.captions]}
            recognized = copy.deepcopy(window.project.to_dict())
            window.undo()
            require(window.project.to_dict() == before_recognition, 'Recognition import is not one undoable edit.')
            window.redo()
            require(window.project.to_dict() == recognized, 'Recognition redo did not preserve IDs/timing/text.')
            window.select_caption(window.project.captions[0].id)
            app.processEvents()
            require(window.grab().save(str(folder / '02-recognized-captions.png')), 'Cannot save recognized caption screenshot.')
            report['stage'] = 'editable captions and exact undo/redo'
            original = copy.deepcopy(window.project.captions[0])
            edited_text = reviewed_caption_text(original.text)
            window.caption_text.setPlainText(edited_text)
            window.update_caption()
            edited = copy.deepcopy(window.project.to_dict())
            require(edited != recognized and window.project.captions[0].text.endswith('[UI edit test]'),
                    'Qt caption text editor did not apply the edit.')
            window.undo()
            require(window.project.to_dict() == recognized, 'Caption edit undo lost recognized data.')
            window.redo()
            require(window.project.to_dict() == edited, 'Caption edit redo changed IDs/timing/text.')
            report['single_edit_undo_redo'] = True
            report['recognition_import_undo_redo'] = True
            report['explicit_caption_edit'] = {
                'before': original.text, 'after': edited_text,
                'description': 'Inserted line breaks and an explicit UI edit test marker through the caption text editor. This is not automatic production wrapping.'}
            window.caption_font.setText(args.font_family)
            window.caption_font_directory.setText(str(args.font_directory) if args.font_directory else '')
            window.caption_size.setValue(22)
            window.caption_margin.setValue(24)
            window.apply_caption_style()
            dialogs.require_no_warnings(mark)

            report['stage'] = 'save reopen and SRT roundtrip'
            project_path = folder / 'recognized-edited.lumen'
            dialogs.save_paths.append(project_path)
            require(window.save_project(), 'UI project save failed.')
            saved = copy.deepcopy(window.project.to_dict())
            # Use a distinct empty editor so equality cannot pass on the old one.
            window.editor = Editor(Project())
            window.refresh()
            dialogs.open_paths.append(project_path)
            window.open_project()
            require(window.project.to_dict() == saved, 'UI reopen changed persisted project content.')
            srt_path = folder / 'recognized-edited.srt'
            dialogs.save_paths.append(srt_path)
            window.export_srt()
            cues = read_srt(srt_path, window.project.fps)
            cue_values = lambda items: [(cue.start, cue.duration, cue.text) for cue in items]
            require(cue_values(cues) == cue_values(window.project.captions), 'SRT roundtrip changed cue timings or text.')
            report['save_reopen_exact'] = True
            report['srt_roundtrip_exact'] = True
            window.select_caption(window.project.captions[0].id)
            app.processEvents()
            require(window.grab().save(str(folder / '03-reopened-edited-captions.png')), 'Cannot save reopened screenshot.')

            report['stage'] = 'actual preview and export from shared graph'
            mark = len(dialogs.messages)
            window.build_preview()
            require(window.preview_job is not None, 'UI preview task did not start.')
            wait_until(app, lambda: not window.jobs, args.timeout, 'actual preview render')
            dialogs.require_no_warnings(mark)
            preview = window.preview_paths.get((window.revision, window.preview_mode))
            require(preview is not None and Path(preview).is_file(), 'UI preview did not produce a current cache.')
            preview_path = folder / 'preview.mp4'
            shutil.copyfile(preview, preview_path)
            # Observe a decoded frame inside a real cue, rather than capturing
            # the intentionally blank lead-in at timeline frame zero.
            cue = window.project.captions[0]
            capture_frame = cue.start + cue.duration // 2
            capture_us = int(capture_frame / window.project.fps * 1_000_000)
            decoded_times = []
            def decoded(frame):
                if frame.isValid():
                    decoded_times.append(frame.startTime())
            window.video.videoSink().videoFrameChanged.connect(decoded)
            window.seek_frame(capture_frame)
            window.player.play()
            playback_deadline = time.monotonic() + 15
            while (window.video.image.isNull() or not decoded_times
                   or decoded_times[-1] < capture_us) and time.monotonic() < playback_deadline:
                app.processEvents()
                time.sleep(.02)
            window.player.pause()
            window.video.videoSink().videoFrameChanged.disconnect(decoded)
            observed_us = decoded_times[-1] if decoded_times else -1
            report['qt_preview_decoded'] = not window.video.image.isNull() and observed_us >= 0
            report['qt_preview_frame_start_us'] = observed_us
            report['qt_preview_frame_within_caption'] = (
                cue.start / window.project.fps <= observed_us / 1_000_000 < cue.end / window.project.fps)
            if report['qt_preview_decoded']:
                require(window.grab().save(str(folder / '04-preview.png')), 'Cannot save actual preview screenshot.')
            export_path = folder / 'export.mp4'
            dialogs.save_paths.append(export_path)
            window.export_dialog()
            require(window.export_job is not None, 'UI export task did not start.')
            wait_until(app, lambda: not window.jobs, args.timeout, 'actual captioned export')
            dialogs.require_no_warnings(mark)
            require(export_path.is_file(), 'UI export did not create an output.')
            require(len(recorder.graphs) == 2 and recorder.graphs[0]['sha256'] == recorder.graphs[1]['sha256'],
                    'Actual preview/export invocations did not use the same normalized filter graph.')
            require(all(graph['caption_drawtext_count'] == len(window.project.captions) for graph in recorder.graphs),
                    'Preview/export graph does not contain every editable caption cue.')
            report['shared_graph'] = copy.deepcopy(recorder.graphs)
            report['outputs'] = [media_evidence(path) for path in (preview_path, export_path)]
            for output in report['outputs']:
                video = next(stream for stream in output['streams'] if stream['codec_type'] == 'video')
                require((video['width'], video['height']) == (640, 360)
                        and int(video['nb_read_frames']) == window.project.duration_frames,
                        f'Unexpected output geometry or frame count: {output}')
                require(any(stream['codec_type'] == 'audio' for stream in output['streams']), 'Output has no audio stream.')
            require(window.project.to_dict() == saved, 'Rendering changed the project.')
            require(not dialogs.open_paths and not dialogs.save_paths, 'A requested file operation did not occur.')
            report['final_captions'] = [asdict(cue) for cue in window.project.captions]
            report['frame_count'] = window.project.duration_frames
            report['stage'] = 'complete'
        finally:
            # Preserve all observed failures and wait for native cancellation,
            # including when an earlier assertion fails during an active job.
            if window is not None:
                window.cancel_jobs()
                for job in list(window.jobs):
                    require(job.wait(30000), 'Background task did not stop during runner cleanup.')
                app.processEvents()
                window.player.stop()
                window.player.setSource(QUrl())
                window.dirty = False
                window.closing = True
                window.close()
                app.processEvents()
            report['messages'] = dialogs.messages
            report['processes'] = recorder.evidence()


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--runtime', type=local_file, required=True)
    parser.add_argument('--model', type=local_file, required=True)
    parser.add_argument('--fixture', type=local_file, required=True,
                        help='Existing synthetic speech fixture only; no user media.')
    parser.add_argument('--output-dir', type=Path, required=True, help='Must not already exist.')
    parser.add_argument('--language', default='en')
    parser.add_argument('--ui-language', choices=('en', 'zh-CN'), default='en',
                        help='Interface locale; independent of ASR recognition language.')
    parser.add_argument('--font-family', default='DejaVu Sans')
    parser.add_argument('--font-directory', type=Path)
    parser.add_argument('--timeline-start', type=int, default=15, help='Nonnegative frame offset; default tests nonzero mapping.')
    parser.add_argument('--timeout', type=float, default=600, help='Seconds per actual task.')
    parser.add_argument('--exercise-interruptions', action='store_true',
                        help='Require real invalid-model failure and cancellation after native runtime launch, then retry.')
    args = parser.parse_args(argv)
    if args.timeout <= 0 or not args.timeout < float('inf') or args.timeline_start < 0:
        parser.error('timeout must be positive and finite; timeline-start must be nonnegative.')
    args.output_dir = args.output_dir.expanduser().resolve()
    if args.output_dir.exists():
        parser.error('output-dir already exists; choose a new directory to preserve evidence.')
    if args.font_directory is not None:
        args.font_directory = args.font_directory.expanduser().resolve(strict=True)
        if not args.font_directory.is_dir():
            parser.error('font-directory must be an existing local folder.')
    return args


def main(argv=None):
    args = parse_args(argv)
    args.output_dir.mkdir(parents=True, exist_ok=False)
    report = {'status': 'running', 'stage': 'input verification',
              'platform': platform.platform(), 'python': platform.python_version(),
              'synthetic_fixture_required': True, 'downloads_performed': False,
              'requested_language': args.language, 'inputs': {}}
    began = time.monotonic()
    try:
        for name, path in [('runtime', args.runtime), ('model', args.model), ('fixture', args.fixture)]:
            report['inputs'][name] = {'path': str(path), 'sha256': sha256(path)}
        root = Path(__file__).resolve().parents[1]
        report['source_sha256'] = {name: sha256(root / name) for name in (
            'scripts/verify_asr_setup_workflow.py', 'lumen/asr.py', 'lumen/ui.py',
            'lumen/ui_captions.py', 'lumen/ui_component_setup.py', 'lumen/render.py')}
        run_workflow(args, report)
        report['status'] = 'passed'
    except Exception as exc:
        report.update(status='failed', error=f'{type(exc).__name__}: {exc}')
    # Verify source immutability on failed/partial runs too, when readable.
    try:
        report['inputs_unchanged'] = len(report['inputs']) == 3 and all(
            sha256(item['path']) == item['sha256'] for item in report['inputs'].values())
        if not report['inputs_unchanged']:
            report.update(status='failed', input_verification_error='An input changed or could not be hashed before acceptance.')
    except OSError as exc:
        report.update(status='failed', inputs_unchanged=False, input_verification_error=str(exc))
    report['elapsed_seconds'] = time.monotonic() - began
    evidence = args.output_dir / 'acceptance.json'
    evidence.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str) + '\n', encoding='utf-8')
    print(json.dumps({'status': report['status'], 'stage': report['stage'],
                      'evidence': str(evidence), 'error': report.get('error')}, ensure_ascii=False, indent=2))
    return 0 if report['status'] == 'passed' else 1


if __name__ == '__main__':
    raise SystemExit(main())
