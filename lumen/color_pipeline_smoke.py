"""Bounded current-schema packaged acceptance on original synthetic media only.

These checks run inside the actual executable via --lumen-self-test. They do
not download media, infer camera identity, certify Apple/Resolve matching, or
establish monitor accuracy. Pixel expectations are independently calculated;
metadata alone is never accepted as evidence of picture precision.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
import hashlib
import json
from pathlib import Path
import subprocess
import time

import numpy as np

PROFILE = 'prores_422_hq_10bit'


def _require(condition, message):
    if not condition:
        raise RuntimeError('Frozen color acceptance: ' + message)


def _ff(*args, data=None):
    from .media import ffmpeg_binary
    return subprocess.run([ffmpeg_binary(), '-hide_banner', '-v', 'error',
        '-nostdin', '-n', '-threads', '1', *map(str, args)], input=data,
        capture_output=True, check=True, timeout=60).stdout


def _rgb16_source(path, pictures, *, apple_log=False):
    """Lossless, tagged source whose decoded codes must equal authored codes."""
    pictures = np.asarray(pictures, dtype='<u2')
    count, height, width, _ = pictures.shape
    planar = np.moveaxis(pictures[..., [1, 2, 0]], -1, 1).copy()
    tags = ['-colorspace', 'rgb', '-color_range', 'pc', '-color_primaries',
            'bt2020' if apple_log else 'bt709']
    if not apple_log:
        tags += ['-color_trc', 'bt709']
    _ff('-f', 'rawvideo', '-pix_fmt', 'gbrp16le', '-s', f'{width}x{height}',
        '-r', '24', *tags, '-i', 'pipe:0', '-frames:v', count, '-c:v', 'ffv1',
        '-level', '3', '-pix_fmt', 'gbrp16le', '-threads', '1', *tags, path,
        data=planar.tobytes())
    actual = _decode_rgb16(path, count, width, height)
    _require(np.array_equal(actual, pictures), 'lossless RGB16 fixture changed authored codes')
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _decode_rgb16(path, count, width, height):
    raw = _ff('-protocol_whitelist', 'file,pipe', '-i', path, '-map', '0:V:0',
              '-an', '-frames:v', count + 1, '-pix_fmt', 'rgb48le',
              '-threads', '1', '-f', 'rawvideo', 'pipe:1')
    _require(len(raw) == count * width * height * 6, 'RGB16 decoded frame count/size mismatch')
    return np.frombuffer(raw, '<u2').reshape(count, height, width, 3)


def _decode_yuv10(path, count, width, height):
    raw = _ff('-protocol_whitelist', 'file,pipe', '-i', path, '-map', '0:V:0',
              '-an', '-frames:v', count + 1, '-pix_fmt', 'yuv422p10le',
              '-threads', '1', '-f', 'rawvideo', 'pipe:1')
    pixels = width * height
    _require(len(raw) == count * pixels * 4, 'ProRes decoded frame count/size mismatch')
    values = np.frombuffer(raw, '<u2').reshape(count, pixels * 2)
    return (values[:, :pixels].reshape(count, height, width),
            values[:, pixels:3 * pixels // 2].reshape(count, height, width // 2),
            values[:, 3 * pixels // 2:].reshape(count, height, width // 2))


def _delivery_metadata(path):
    from .media import ffprobe_binary
    result = subprocess.run([ffprobe_binary(), '-v', 'error', '-select_streams', 'v:0',
        '-show_streams', '-read_intervals', '%+#1', '-show_frames', '-of', 'json', str(path)],
        capture_output=True, text=True, check=True, timeout=30)
    data = json.loads(result.stdout)
    stream, frame = data['streams'][0], data['frames'][0]
    _require(stream.get('codec_name') == 'prores' and stream.get('profile') == 'HQ'
             and stream.get('pix_fmt') == frame.get('pix_fmt') == 'yuv422p10le',
             'delivery is not actual decoded ProRes 422 HQ 10-bit')
    _require(all(stream.get(key) == 'bt709' for key in
                 ('color_space', 'color_transfer', 'color_primaries'))
             and frame.get('color_range') == 'tv', 'delivery lost SDR Rec.709 limited-range tags')
    return {key: stream.get(key) for key in ('codec_name', 'profile', 'pix_fmt',
            'color_space', 'color_transfer', 'color_primaries')}


def _animated_fixture(root):
    from .media import probe
    from .model import Clip, Grade, Project
    from .qualifier import QualifierRange, QualifierSettings, WindowKeyframe

    width, height, count = 192, 128, 6
    image = np.empty((height, width, 3), dtype='<u2')
    for index, value in enumerate((.3, .5, .65, .85)):
        image[index * 32:(index + 1) * 32] = round(value * 65535)
    pictures = np.repeat(image[None], count, axis=0)
    source = root / 'original-qualifier-rgb16.mkv'
    digest = _rgb16_source(source, pictures)
    media = probe(source)
    settings = QualifierSettings(enabled=True, hue_enabled=False, saturation_enabled=False,
        luma_low=.4, luma_high=.6, luma_softness_low=.2, luma_softness_high=.1,
        inclusion_ranges=[QualifierRange(hue_enabled=False, saturation_enabled=False,
            luma_low=.8, luma_high=.9, luma_softness=0)],
        exclusion_ranges=[QualifierRange(hue_enabled=False, saturation_enabled=False,
            luma_low=.45, luma_high=.55, luma_softness=0)],
        exposure=-.5, window_shape='rectangle', window_feather=0,
        window_keyframes=[WindowKeyframe(-2, .10, .5, .3, 2, 'hold'),
            WindowKeyframe(1, .25, .5, .3, 2), WindowKeyframe(4, .75, .5, .3, 2, 'hold'),
            WindowKeyframe(8, .9, .5, .3, 2)])
    project = Project(name='Original schema-30 animated qualifier acceptance',
        width=width, height=height, fps_num=24, media=[media],
        clips=[Clip('animated-qualifier', media.id, 0, 0, 0, count, grade=Grade(qualifier=settings))])
    # Independent neutral-luma set and analytic rectangle oracles. This covers
    # disabled hue/saturation gates, asymmetric exterior feathers, add/subtract
    # ranges, hidden endpoint keys, outgoing Hold, and Linear interpolation.
    neutral = image[..., 0].astype(float) / 65535
    base = np.where(neutral < .4, np.clip(1 - (.4 - neutral) / .2, 0, 1),
                    np.where(neutral > .6, np.clip(1 - (neutral - .6) / .1, 0, 1), 1.))
    strength = np.maximum(base, ((neutral >= .8) & (neutral <= .9)).astype(float))
    strength *= ~((neutral >= .45) & (neutral <= .55))
    xx = (np.arange(width) + .5) / width
    expected = []
    for center in (.10, .25, .25 + .5 / 3, .25 + 1 / 3, .75, .75):
        mask = strength * (abs(xx - center) <= .15)[None, :]
        expected.append(image / 65535 * (1 + mask[..., None] * (2 ** -.5 - 1)))
    return project, np.asarray(expected), source, digest


def _animation_checks(engine, root):
    from .color_picker_source import capture_picker_snapshot
    from .frame_grade_preview import capture_grade_preview_snapshot
    from .model import Editor, Project, VERSION

    project, expected, source, digest = _animated_fixture(root)
    # Stable artifact name records the schema-30 animation feature, not the
    # current serializer version; the project always uses VERSION below.
    path = root / 'schema30-animated-project.lumen'
    project.save(path)
    _require(project.to_dict()['version'] == VERSION, 'unexpected project schema')
    restored = Project.load(path)
    _require(restored.to_dict() == project.to_dict(), 'schema-30 qualifier/keys did not round-trip')
    project = restored
    before = deepcopy(project.to_dict())
    clip = project.clips[0]
    for frame in (0, 2, 4, 5):
        snapshot = capture_grade_preview_snapshot(project, clip.id, frame)
        error = float(abs(snapshot.frame - expected[frame]).max())
        _require(error < 3e-7, f'animated RGB16 frame preview differs from analytic oracle: {error}')
    picker = capture_picker_snapshot(project, clip.id, 2, stage='qualifier')
    _require(abs(picker.frame[8, 80, 0] - .3) < 1 / 65535,
             'qualifier picker sampled corrected instead of pre-qualifier pixels')
    stage = engine._graded_segment(project.media[0], clip, project, None)
    actual = _decode_rgb16(stage, 6, project.width, project.height)
    max_error = float(abs(actual.astype(float) - np.rint(expected * 65535)).max())
    _require(max_error <= 2, 'animated export stage disagrees with independent RGB16 reference')
    editor = Editor(deepcopy(project))
    editor.split_clip(clip.id, 3)
    split = np.concatenate([_decode_rgb16(engine._graded_segment(
        editor.project.media[0], part, editor.project, None), part.duration,
        project.width, project.height) for part in editor.project.clips])
    _require(np.array_equal(actual, split), 'split rebasing changed animated window pixels')
    _require(editor.undo() and editor.project.to_dict() == before, 'animated split undo lost keys')
    preview = engine.preview(project)
    _require(preview == engine.preview(project), 'animated preview did not reuse its cache')
    altered = deepcopy(project)
    altered.clips[0].grade.qualifier.window_keyframes[1] = replace(
        altered.clips[0].grade.qualifier.window_keyframes[1], center_x=.4)
    _require(engine.preview(altered) != preview, 'key edits reused a stale animated preview')
    _require(project.to_dict() == before and hashlib.sha256(source.read_bytes()).hexdigest() == digest,
             'animation smoke mutated its baseline or source')
    return project, {'schema': VERSION, 'frames': 6, 'rgb16_max_code_error': max_error,
        'qualifier_channel_switches_asymmetric_feathers_and_sets': True,
        'linear_hold_and_hidden_keys': True, 'picker_pre_qualifier': True,
        'preview_cache_reused_and_key_edit_invalidated': True,
        'split_pixels_identical_and_undo_exact': True,
        'project_file': path.name, 'source_sha256': digest, 'source_unchanged': True}


def _apple_log_checks(engine, root):
    from .color_picker_source import capture_picker_snapshot
    from .media import MediaError, probe
    from .model import Clip, Grade, Project

    width, height, count = 96, 64, 3
    # Independent forward anchors, then decode the QUANTIZED authored codes.
    linear = np.array([.001, .18, 1., 4.])
    encoded = np.where(linear < .01, 47.28711236 * (linear + .05641088) ** 2,
                       .08550479 * np.log2(linear + .00964052) + .69336945)
    codes = np.rint(encoded * 65535).astype('<u2')
    image = np.broadcast_to(np.repeat(codes, width // 4)[None, :, None],
                            (height, width, 3)).copy()
    source = root / 'original-experimental-apple-log-rgb16.mkv'
    digest = _rgb16_source(source, np.repeat(image[None], count, axis=0), apple_log=True)
    try:
        probe(source)
    except MediaError:
        pass
    else:
        raise RuntimeError('Frozen color acceptance: Apple Log was silently autodetected')
    media = probe(source, input_encoding='apple_log_bt2020')
    project = Project(width=width, height=height, fps_num=24, media=[media], clips=[
        Clip('explicit-log', media.id, 0, 0, 0, count, grade=Grade(input_encoding='apple_log_bt2020'))])
    project.save(root / 'experimental-apple-log-project.lumen')
    project = Project.load(root / 'experimental-apple-log-project.lumen')
    p = codes.astype(float) / 65535
    decoded = np.where(p < 47.28711236 * (.01 + .05641088) ** 2,
        np.sqrt(p / 47.28711236) - .05641088, 2 ** ((p - .69336945) / .08550479) - .00964052)
    shoulder = np.where(decoded <= .18, decoded, .18 + .82 * (1 - np.exp(-(decoded - .18) / .82)))
    reference = np.where(shoulder < .018, 4.5 * shoulder, 1.099 * shoulder ** .45 - .099)
    snapshot = capture_picker_snapshot(project, 'explicit-log', 0, stage='qualifier')
    samples = snapshot.frame[32, [12, 36, 60, 84]]
    _require(float(abs(samples - reference[:, None]).max()) < 3e-6,
             'experimental Apple Log neutral shoulder/normalization is incorrect')
    stage = engine._graded_segment(media, project.clips[0], project, None)
    actual = _decode_rgb16(stage, count, width, height)
    error = float(abs(actual[0, 32, [12, 36, 60, 84]] - np.rint(reference[:, None] * 65535)).max())
    _require(error <= 2, 'Apple Log RGB16 export differs from independent neutral oracle')
    project.clips[0].grade.enabled = False
    project.clips[0].grade.exposure = 2
    bypass = engine._graded_segment(media, project.clips[0], project, None)
    _require(np.array_equal(actual, _decode_rgb16(bypass, count, width, height)),
             'grade bypass disabled mandatory Apple Log input normalization')
    output = engine.render(project, root / 'experimental-apple-log-normalized.mov', export_profile=PROFILE)
    metadata = _delivery_metadata(output)
    y, _, _ = _decode_yuv10(output, count, width, height)
    _require(float(abs(y[0, 32, [12, 36, 60, 84]].astype(float) - (64 + 876 * reference)).max()) < 3,
             'Apple Log SDR delivery changed the expected neutral output')
    _require(hashlib.sha256(source.read_bytes()).hexdigest() == digest, 'Apple Log source changed')
    return {'input_encoding': 'apple_log_bt2020', 'synthetic_rgb16_only': True,
        'camera_footage_validated': False, 'apple_log_2_supported': False,
        'apple_lut_or_resolve_matching_claimed': False, 'explicit_import_required': True,
        'independent_neutral_oracle_max_code_error': error, 'normalization_survives_grade_bypass': True,
        'metadata': metadata, 'source_sha256': digest, 'source_unchanged': True,
        'rendered_file': output.name}


def _highbit_checks(engine, root, caption_style):
    from PySide6.QtGui import QImage
    from .media import probe
    from .model import Caption, Clip, Overlay, OverlayKeyframe, Project
    from .sticker_assets import load_sticker

    width, height, count = 1024, 160, 3
    image = np.broadcast_to(np.rint(np.linspace(0, 65535, width))[None, :, None],
                            (height, width, 3)).astype('<u2').copy()
    image[48:96, :width // 2] = 32778
    image[48:96, width // 2:] = 32958
    source = root / 'original-highbit-ramp.mkv'
    digest = _rgb16_source(source, np.repeat(image[None], count, axis=0))
    media = probe(source)
    project = Project(width=width, height=height, fps_num=24, media=[media],
        clips=[Clip('precision', media.id, 0, 0, 0, count)])
    project.caption_style = replace(caption_style, font_size=28, margin_bottom=8,
                                   outline_width=0, safe_margin_percent=2)
    baseline = engine.render(project, root / 'highbit-baseline.mov', export_profile=PROFILE)
    pixels = np.full((16, 16, 4), (255, 0, 0, 128), dtype=np.uint8)
    art = QImage(pixels.data, 16, 16, 64, QImage.Format.Format_RGBA8888)
    art_path = root / 'original-translucent-red.png'
    _require(art.save(str(art_path), 'PNG'), 'could not save original sticker fixture')
    asset = load_sticker(art_path)
    project.overlays = [Overlay('original-red', str(art_path), asset.digest, 1, 1,
                               keyframes=[OverlayKeyframe(0, 32, 104, 1, 1)])]
    project.captions = [Caption('precision-cue', 1, 1, 'TEN BIT', 'en')]
    project.save(root / 'highbit-caption-sticker-project.lumen')
    output = engine.render(project, root / 'highbit-caption-sticker.mov', export_profile=PROFILE)
    metadata = _delivery_metadata(output)
    before = _decode_yuv10(baseline, count, width, height)
    after = _decode_yuv10(output, count, width, height)
    for pristine, composited in zip(before, after):
        _require(np.array_equal(pristine[[0, 2]], composited[[0, 2]]),
                 'caption/sticker frame gates changed inactive pictures')
        _require(np.array_equal(pristine[:, :80], composited[:, :80]),
                 'caption/sticker chain changed untouched high-bit picture pixels')
    y = after[0]
    tones = int(np.unique(y[1, 16]).size)
    _require(tones > 650, 'caption/sticker chain lost the high-bit ramp precision')
    delta = float(y[1, 64:80, 576:960].mean() - y[1, 64:80, 64:448].mean())
    _require(delta >= 1.75, 'sub-eight-bit patch contrast was lost behind captions/stickers')
    _require(np.count_nonzero(y[1, 104:120, 32:48] != before[0][1, 104:120, 32:48]) > 100,
             'the translucent static sticker was not actually composited')
    _require(np.count_nonzero(y[1, 120:] != before[0][1, 120:]) > 100,
             'the high-bit caption was not actually drawn')
    # A deliberately eight-bit source is a negative control, not a second
    # high-bit claim. Dithering/ten-bit tags cannot recreate these broad patches.
    coarse = (np.rint(image.astype(float) / 257) * 257).astype('<u2')
    coarse_path = root / 'original-eight-bit-negative-control.mkv'
    _rgb16_source(coarse_path, np.repeat(coarse[None], count, axis=0))
    control = deepcopy(project)
    control.media = [probe(coarse_path)]
    control.clips[0].media_id = control.media[0].id
    coarse_output = engine.render(control, root / 'highbit-eight-bit-control.mov', export_profile=PROFILE)
    coarse_y = _decode_yuv10(coarse_output, count, width, height)[0]
    coarse_delta = float(coarse_y[1, 64:80, 576:960].mean() - coarse_y[1, 64:80, 64:448].mean())
    _require(abs(coarse_delta) <= .5, 'eight-bit negative control unexpectedly retained patch separation')
    _require(hashlib.sha256(source.read_bytes()).hexdigest() == digest, 'high-bit source changed')
    return {'frames': count, 'metadata': metadata, 'caption_and_sticker_visible': True,
        'inactive_frames_and_untouched_pixels_identical': True, 'distinct_ramp_luma_codes': tones,
        'sub_eight_bit_patch_delta': delta, 'eight_bit_negative_control_patch_delta': coarse_delta,
        'source_sha256': digest, 'source_unchanged': True, 'rendered_file': output.name,
        'picture_precision': '10-bit SDR; glyph/sticker coverage remains 8-bit',
        'hdr_or_linear_light_compositing_claimed': False}


def run_color_pipeline_smoke(engine, folder, caption_style):
    started = time.monotonic()
    root = Path(folder) / 'color-pipeline'
    root.mkdir(exist_ok=False)
    project, animation = _animation_checks(engine, root)
    apple = _apple_log_checks(engine, root)
    highbit = _highbit_checks(engine, root, caption_style)
    return project, {'synthetic_media_only': True, 'downloads_or_user_media_used': False,
        'animation': animation, 'apple_log_experimental': apple, 'highbit_caption_sticker_chain': highbit,
        'elapsed_seconds': round(time.monotonic() - started, 3)}


def capture_color_pipeline_ui(window, project, folder, screenshots):
    """Real decoder jobs and Qt controls, with isolated editing state restored.

    Keyboard delivery here is QApplication.sendEvent, not a physical keyboard
    or OS input-method claim. No QtTest dependency is needed in the package.
    """
    from PySide6.QtCore import QEvent, Qt
    from PySide6.QtGui import QKeyEvent
    from PySide6.QtWidgets import QApplication
    from .model import Editor

    app = QApplication.instance()
    original_editor = window.editor
    original_clip_ids = window.selected_clip_ids
    saved = (window.revision, window.dirty, window.selection, window.active_selection_type,
             window.timeline.playhead, window.ui_language)
    dialogs = []

    def wait_for(predicate, detail):
        deadline = time.monotonic() + 20
        while not predicate() and time.monotonic() < deadline:
            app.processEvents(); time.sleep(.01)
        _require(predicate(), detail)

    def loaded(dialog):
        _require(dialog is not None, 'the actual inspector could not open its color dialog')
        dialogs.append(dialog)
        wait_for(lambda: dialog.snapshot is not None, 'color dialog did not decode its real source frame')
        wait_for(lambda: not window.jobs, 'color dialog decoder job did not finish')
        _require(not dialog.canvas.image.isNull(), 'color dialog rendered no visible pixels')
        return dialog

    def save(dialog, filename):
        for _ in range(5):
            app.processEvents()
        _require(dialog.grab().save(str(Path(folder) / filename)), 'color dialog screenshot failed')
        screenshots.append(filename)

    def state():
        return deepcopy((window.project.to_dict(), window.revision, window.dirty,
            [item.to_dict() for item in window.editor._undo],
            [item.to_dict() for item in window.editor._redo], window.timeline.playhead))

    def key(widget, kind, value):
        QApplication.sendEvent(widget, QKeyEvent(kind, value, Qt.KeyboardModifier.NoModifier))
        app.processEvents()

    try:
        _require(not window.jobs, 'pre-existing work is running before isolated color UI acceptance')
        window.editor = Editor(deepcopy(project)); window.revision += 1; window.dirty = False
        window.timeline.playhead = 0
        window.refresh(); window.select_clip(project.clips[0].id)
        baseline = state()
        for locale in ('zh-CN', 'en'):
            window.set_ui_language(locale, persist=False)
            qualifier = loaded(window.open_qualifier())
            qualifier.resize(960, 940)
            _require(not qualifier.channel_checks['hue'].isChecked()
                     and not qualifier.channel_checks['saturation'].isChecked(),
                     'packaged qualifier channel switches lost their saved state')
            _require(not qualifier.controls['window_center_x'].isEnabled(),
                     'static qualifier allowed editing animated window geometry')
            save(qualifier, f'frozen-ui-{locale}-qualifier-schema30.png')
            qualifier.controls['exposure'].setValue(-.25)
            _require(state() == baseline, 'qualifier staging changed the project before Apply')
            qualifier.reject()
            _require(state() == baseline and qualifier.snapshot is None,
                     'qualifier Cancel changed the project or retained its frame')

            animation = loaded(window.open_window_animation())
            animation.resize(840, 940)
            _require(window.open_window_animation() is animation,
                     'reopening a visible animation dialog created another owner')
            _require(animation.windowTitle() == ('窗口动画 · 关键帧草稿' if locale == 'zh-CN'
                                                   else 'Window animation · staged keys'),
                     'packaged animation dialog translation is missing')
            _require(animation.key_timeline.frames == (1, 4),
                     'keyframe timeline lost visible keys or exposed hidden handles')
            save(animation, f'frozen-ui-{locale}-window-keyframes.png')
            animation.reject()
            _require(state() == baseline and animation.snapshot is None,
                     'animation Cancel changed the project or retained its decoded frame')

        animation = loaded(window.open_window_animation())
        animation.select_key(1)
        _require(animation.snapshot is None and not window.jobs,
                 'frame navigation decoded automatically instead of waiting for Refresh')
        # A key press is a draft gesture; Escape must cancel without saving it.
        timeline = animation.key_timeline
        QApplication.setActiveWindow(animation)
        timeline.setFocus(Qt.FocusReason.OtherFocusReason); app.processEvents()
        key(timeline, QEvent.Type.KeyPress, Qt.Key.Key_Right)
        _require(timeline.gesture_active and timeline.preview_frame == 2
                 and [entry.frame for entry in animation.settings.window_keyframes] == [-2, 1, 4, 8],
                 'key press committed timing before release')
        key(timeline, QEvent.Type.KeyPress, Qt.Key.Key_Escape)
        key(timeline, QEvent.Type.KeyRelease, Qt.Key.Key_Right)
        _require(not timeline.gesture_active
                 and [entry.frame for entry in animation.settings.window_keyframes] == [-2, 1, 4, 8],
                 'Escape failed to cancel the key timing gesture')
        key(timeline, QEvent.Type.KeyPress, Qt.Key.Key_Right)
        key(timeline, QEvent.Type.KeyRelease, Qt.Key.Key_Right)
        _require([entry.frame for entry in animation.settings.window_keyframes] == [-2, 2, 4, 8]
                 and animation.local_frame == 2 and not window.jobs and state() == baseline,
                 'key release did not stage exactly one clip-local frame move')
        collision = deepcopy(animation.settings)
        animation.key_time.setValue(4); animation.move_button.click()
        _require(animation.settings == collision, 'numeric retime overwrote an occupied key')
        animation.refresh_button.click()
        wait_for(lambda: animation.snapshot is not None, 'explicit Refresh failed after a staged key move')
        wait_for(lambda: not window.jobs, 'animation Refresh decoder did not finish')
        pending = deepcopy(animation.settings)
        animation.apply_button.click(); app.processEvents()
        _require(window.selected_clip().grade.qualifier == pending
                 and window.revision == baseline[1] + 1 and len(window.editor._undo) == 1,
                 'Apply did not commit the staged key edit as exactly one undo transaction')
        _require(animation.snapshot is None, 'Apply retained a closed animation frame')
        applied = deepcopy(window.project.to_dict())
        path = Path(folder) / 'color-pipeline' / 'ui-edited-window-project.lumen'
        window.project.save(path)
        from .model import Project
        _require(Project.load(path).to_dict() == applied, 'UI-edited animation did not persist')
        window.undo()
        _require(window.project.to_dict() == baseline[0] and not window.editor._undo,
                 'Undo did not restore the exact pre-key-edit project')
        window.redo()
        _require(window.project.to_dict() == applied and len(window.editor._undo) == 1,
                 'Redo did not restore the exact staged-key result')
        unchanged = loaded(window.open_window_animation())
        unchanged.apply_button.click(); app.processEvents()
        _require(window.project.to_dict() == applied and len(window.editor._undo) == 1,
                 'unchanged Apply added another undo transaction')
        stale = loaded(window.open_window_animation())
        stale.controls['window_width'].setValue(50); stale.add_button.click()
        window.timeline.playhead = 1
        stale.apply_settings()
        _require(window.project.to_dict() == applied and len(window.editor._undo) == 1
                 and stale.snapshot is None and not stale.apply_button.isEnabled(),
                 'stale dialog applied pending keys or retained old pixels')
        return {'locales': ['zh-CN', 'en'], 'real_rgb16_decoder_jobs': True,
            'qualifier_saved_channels_and_animated_geometry_guard': True,
            'cancel_and_reopen_preserve_project': True, 'visible_dialog_reused': True,
            'keyboard_timing_commit_and_escape_cancel': True, 'numeric_collision_rejected': True,
            'hidden_keys_preserved': True, 'manual_refresh_only': True,
            'apply_once_undo_redo_and_persistence': True, 'unchanged_apply_noop': True,
            'stale_context_cannot_apply': True, 'physical_keyboard_tested': False}
    finally:
        for dialog in dialogs:
            dialog.reject(); dialog.deleteLater()
        wait_for(lambda: not window.jobs, 'color UI decoder jobs did not stop during cleanup')
        window.editor = original_editor
        window._selection_editor_token = (id(original_editor), original_editor.session_id)
        (window.revision, window.dirty, window.selection, window.active_selection_type,
         window.timeline.playhead, language) = saved
        window.set_selected_clip_ids(original_clip_ids, anchor=window.selection, keep_anchor=True)
        window.refresh(); window.set_ui_language(language, persist=False)
        app.processEvents()
