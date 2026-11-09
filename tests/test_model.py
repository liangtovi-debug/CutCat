from contrast_legacy_fixture import strip_contrast_pivot_field
import json
from copy import deepcopy
import os
import shutil
from fractions import Fraction
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from lumen.model import VERSION, Clip, Editor, Grade, Media, Project


def sample():
    # File roundtrips restore canonical, native absolute paths, including on
    # macOS (/tmp -> /private/tmp) and Windows (where /tmp has no drive).
    path = (Path(tempfile.gettempdir()) / 'example.mp4').resolve()
    return Project(media=[Media('m', str(path), 'Example', 10, 1920, 1080, True, True)])


class ModelTests(unittest.TestCase):
    def test_empty(self):
        project = Project()
        project.validate()
        self.assertEqual(project.fps, Fraction(30))
        self.assertEqual(project.duration_frames, 0)
        self.assertEqual(Project.from_dict(project.to_dict()), project)

    def test_append_split_and_source_continuity(self):
        editor = Editor(sample())
        clip = editor.add_clip('m', 0, 10)
        editor.update_clip(clip.id, fade_in=20, fade_out=30, gain_db=-6, grade=Grade(exposure=2))
        left, right = editor.split_clip(clip.id, 110)
        self.assertEqual((left.start, left.source_in, left.duration), (10, 0, 100))
        self.assertEqual((right.start, right.source_in, right.duration), (110, 100, 200))
        self.assertEqual((left.fade_in, left.fade_out, right.fade_in, right.fade_out), (20, 0, 0, 30))
        self.assertEqual(right.gain_db, -6)
        self.assertIsNot(left.grade, right.grade)
        following = editor.add_clip('m', 0)
        self.assertEqual(following.start, 310)
        self.assertEqual(editor.project.duration_frames, 610)

    def test_transaction_rollback_and_history(self):
        editor = Editor(sample())
        a = editor.add_clip('m', 0)
        b = editor.add_clip('m', 0)
        baseline = editor.project.to_dict()
        with self.assertRaises(ValueError):
            editor.move_clip(b.id, 299)
        self.assertEqual(editor.project.to_dict(), baseline)
        self.assertTrue(editor.undo())
        self.assertEqual(len(editor.project.clips), 1)
        self.assertTrue(editor.redo())
        self.assertEqual(editor.project.to_dict(), baseline)
        editor.delete_clip(a.id)
        self.assertTrue(editor.undo())
        editor.move_clip(a.id, 0, 1)
        self.assertFalse(editor.can_redo)
        self.assertFalse(editor.redo())

    def test_rational_source_bounds(self):
        project = sample()
        project.fps_num, project.fps_den = 30000, 1001
        editor = Editor(project)
        clip = editor.add_clip('m', 0)
        self.assertEqual(clip.duration, 299)
        editor.trim_clip(clip.id, 99, 200)
        with self.assertRaises(ValueError):
            editor.trim_clip(clip.id, 100, 200)
        self.assertEqual(editor.project.clips[0].source_in, 99)

    def test_trim_clamps_fades(self):
        editor = Editor(sample())
        clip = editor.add_clip('m', 0)
        editor.update_clip(clip.id, fade_in=200, fade_out=200)
        editor.trim_clip(clip.id, 10, 40)
        self.assertEqual((clip.fade_in, clip.fade_out), (40, 40))

    def test_track_compatibility(self):
        editor = Editor(Project(media=[Media('v', '/tmp/v', 'v', 1), Media('a', '/tmp/a', 'a', 1, has_video=False, has_audio=True)]))
        for media, track in [('a', 0), ('v', 2), ('v', -1), ('v', 4)]:
            with self.subTest(media=media, track=track), self.assertRaises(ValueError):
                editor.add_clip(media, track)
        editor.add_clip('v', 1)
        editor.add_clip('a', 2)
        editor.add_clip('a', 3)

    def test_invalid_clip_data(self):
        editor = Editor(sample())
        clip = editor.add_clip('m', 0)
        for changes in [dict(start=-1), dict(start=1.5), dict(start=True), dict(duration=0), dict(source_in=-1), dict(track=4), dict(grade=Grade(exposure=float('nan'))), dict(gain_db=float('inf')), dict(grade=Grade(saturation=5)), dict(fade_in=301), dict(muted=1), dict(media_id='missing')]:
            baseline = editor.project.to_dict()
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                editor.update_clip(clip.id, **changes)
            self.assertEqual(editor.project.to_dict(), baseline)
        for frame in [0, 300, -1, 0.5, True]:
            with self.subTest(frame=frame), self.assertRaises(ValueError):
                editor.split_clip(clip.id, frame)

    def test_invalid_project_data(self):
        for changes in [dict(width=0), dict(height=-1), dict(fps_den=0), dict(fps_num=True), dict(name=4)]:
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                Project(**changes).validate()
        for duration in [float('nan'), float('inf'), -1, True]:
            with self.subTest(duration=duration), self.assertRaises(ValueError):
                Project(media=[Media('m', 'm', 'm', duration)]).validate()

    def test_duplicates_and_unknown_fields(self):
        project = sample()
        project.media.append(project.media[0])
        with self.assertRaises(ValueError):
            project.validate()
        editor = Editor(sample())
        clip = editor.add_clip('m', 0)
        editor.project.clips.append(Clip(clip.id, 'm', 1, 0, 0, 20))
        with self.assertRaises(ValueError):
            editor.project.validate()
        for update in [dict(version=VERSION + 1), dict(version=True), dict(format='other'), dict(unknown='value'), dict(media={})]:
            payload = sample().to_dict()
            payload.update(update)
            with self.subTest(update=update), self.assertRaises(ValueError):
                Project.from_dict(payload)

    def test_portable_save_and_missing_media(self):
        with tempfile.TemporaryDirectory() as directory:
            directory = Path(directory).resolve()
            media_path = directory / 'media.mp4'
            media_path.touch()
            project = Project(media=[Media('m', str(media_path), 'Media', 1)])
            editor = Editor(project)
            clip = editor.add_clip('m', 0)
            editor.update_clip(clip.id, grade=Grade(lut_path=str(directory / 'look.cube')))
            destination = directory / 'project.lumen'
            project.save(destination)
            raw = json.loads(destination.read_text(encoding='utf-8'))
            self.assertEqual(raw['media'][0]['path'], 'media.mp4')
            self.assertEqual(raw['clips'][0]['grade']['lut_path'], 'look.cube')
            loaded = Project.load(destination)
            self.assertEqual(loaded, project)
            self.assertEqual(loaded.missing_media(), [])
            media_path.unlink()
            self.assertEqual([m.id for m in loaded.missing_media()], ['m'])

    def test_save_failure_preserves_existing_file(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / 'project.lumen'
            target.write_text('existing')
            with patch('lumen.model.os.replace', side_effect=OSError('disk failure')):
                with self.assertRaises(OSError):
                    sample().save(target)
            self.assertEqual(target.read_text(), 'existing')
            self.assertEqual(list(Path(directory).iterdir()), [target])

    def test_json_duplicate_keys_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / 'bad.lumen'
            target.write_text('{"version":1,"version":1}')
            with self.assertRaises(ValueError):
                Project.load(target)

    def test_save_protects_sources_and_aliases(self):
        with tempfile.TemporaryDirectory() as directory:
            directory = Path(directory)
            media = directory / 'source.mp4'
            media.write_bytes(b'original media')
            lut = directory / 'look.cube'
            lut.write_bytes(b'original lut')
            editor = Editor(Project(media=[Media('m', str(media), 'Source', 2)]))
            clip = editor.add_clip('m', 0)
            editor.update_clip(clip.id, grade=Grade(lut_path=str(lut), enabled=False))
            for original in [media, lut]:
                for kind in ['direct', 'symlink', 'hardlink']:
                    target = original if kind == 'direct' else directory / (original.name + '.' + kind)
                    if kind == 'symlink':
                        try:
                            target.symlink_to(original)
                        except (OSError, NotImplementedError):
                            continue
                    elif kind == 'hardlink':
                        try:
                            os.link(original, target)
                        except OSError:
                            continue
                    before = original.read_bytes()
                    with self.subTest(original=original, kind=kind), self.assertRaises(ValueError):
                        editor.project.save(target)
                    self.assertEqual(original.read_bytes(), before)
                    self.assertEqual(target.read_bytes(), before)
            missing = directory / 'missing.mov'
            editor.project.media[0].path = str(missing)
            with self.assertRaises(ValueError):
                editor.project.save(missing)
            self.assertFalse(missing.exists())

    def test_moved_project_directory_and_relinked_source(self):
        with tempfile.TemporaryDirectory() as directory:
            directory = Path(directory).resolve()
            package = directory / 'original'
            package.mkdir()
            (package / 'assets').mkdir()
            source = package / 'assets' / 'source.mp4'
            source.touch()
            editor = Editor(Project(media=[Media('m', str(source), 'Source', 2)]))
            clip = editor.add_clip('m', 0)
            editor.trim_clip(clip.id, 10, 30)
            editor.project.save(package / 'project.lumen')
            moved = directory / 'moved'
            shutil.move(str(package), moved)
            loaded = Project.load(moved / 'project.lumen')
            self.assertEqual(loaded.missing_media(), [])
            self.assertEqual(Path(loaded.media[0].path), moved / 'assets' / 'source.mp4')
            Path(loaded.media[0].path).unlink()
            self.assertEqual(len(loaded.missing_media()), 1)
            replacement = directory / 'replacement.mp4'
            replacement.touch()
            loaded.media[0].path = str(replacement)
            loaded.validate()
            loaded.save(moved / 'relinked.lumen')
            reopened = Project.load(moved / 'relinked.lumen')
            self.assertEqual(reopened.missing_media(), [])
            self.assertEqual(reopened.media[0].path, str(replacement))
            self.assertEqual((reopened.clips[0].source_in, reopened.clips[0].duration), (10, 30))

    def test_frame_rounding_and_nonfinite_values(self):
        project = Project(fps_num=30000, fps_den=1001)
        for duration, expected in [(1.001, 30), (0.033366, 0), (0.033367, 1), (10.01, 300)]:
            with self.subTest(duration=duration):
                self.assertEqual(project.media_frames(Media('m', 'm', 'm', duration)), expected)
        for field, value in [('duration', float('nan')), ('duration', float('inf')), ('duration', 10**400), ('audio_offset', float('nan')), ('audio_offset', float('-inf'))]:
            media = Media('m', 'm', 'm', 1)
            setattr(media, field, value)
            with self.subTest(field=field), self.assertRaises(ValueError):
                Editor(Project(media=[media]))

    def test_snapshots_do_not_alias(self):
        editor = Editor(sample())
        clip = editor.add_clip('m', 0)
        grade = Grade(exposure=1)
        editor.update_clip(clip.id, grade=grade)
        grade.exposure = 3
        self.assertEqual(editor.project.clips[0].grade.exposure, 1)
        editor.undo()
        self.assertEqual(editor.project.clips[0].grade.exposure, 0)
        editor.redo()
        self.assertEqual(editor.project.clips[0].grade.exposure, 1)

    def test_add_media_undo_and_independence(self):
        editor = Editor()
        media = Media('m', 'm', 'm', 2)
        editor.add_media(media)
        media.duration = 0
        self.assertEqual(editor.project.media[0].duration, 2)
        with self.assertRaises(ValueError):
            editor.add_media(Media('m', 'duplicate', 'm', 1))
        editor.undo()
        self.assertEqual(editor.project.media, [])
        self.assertFalse(editor.undo())
        editor.redo()
        self.assertEqual(editor.project.media[0].duration, 2)

    def test_v1_migrates_color_controls_to_identity(self):
        editor = Editor(sample())
        clip = editor.add_clip('m', 0)
        editor.update_clip(clip.id, grade=Grade(exposure=2, saturation=0.5))
        payload = editor.project.to_dict()
        payload['version'] = 1; strip_contrast_pivot_field(payload)
        if payload['version'] < 17:
            for old_clip in payload.get('clips', []):
                old_clip.get('grade', {}).pop('hue_hue_curve', None)
                old_clip.get('grade', {}).pop('hue_sat_curve', None)
                old_clip.get('grade', {}).pop('qualifier', None)
        if payload['version'] < 16:
            for old_clip in payload.get('clips', []): old_clip.pop('transform', None)
        payload['clips'][0].pop('restoration')
        payload['clips'][0].pop('stabilization')
        mapping = payload['clips'][0].pop('time_map')
        payload['clips'][0]['source_in'] = mapping['source_in_num']
        legacy_grade = payload['clips'][0]['grade']
        for name in ('linear_exposure', 'lift', 'gamma', 'gain', 'master_curve', 'red_curve', 'green_curve', 'blue_curve'):
            del legacy_grade[name]
        original = deepcopy(payload)
        loaded = Project.from_dict(payload)
        self.assertEqual(payload, original)
        self.assertEqual(loaded.clips[0].grade, Grade(exposure=2, saturation=0.5))
        self.assertEqual(loaded.to_dict()['version'], VERSION)
        self.assertEqual(Project.from_dict(loaded.to_dict()), loaded)

    def test_v2_color_roundtrip_and_boundaries(self):
        editor = Editor(sample())
        clip = editor.add_clip('m', 0)
        grade = Grade(linear_exposure=-10, lift=[-1, 0.2, 1], gamma=[0.1, 2, 4], gain=[0, 2, 4],
                      master_curve=[[0, -1], [0.4, 0.6], [1, 2]],
                      red_curve=[[0, 0.2], [1, 1.2]], green_curve=[[0, 0], [1, 0.8]],
                      blue_curve=[[0, 0.1], [1, 1]], exposure=3)
        editor.update_clip(clip.id, grade=grade)
        self.assertEqual(Project.from_dict(editor.project.to_dict()), editor.project)
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / 'color.lumen'
            editor.project.save(target)
            self.assertEqual(json.loads(target.read_text(encoding='utf-8'))['version'], VERSION)
            self.assertEqual(Project.load(target), editor.project)
        editor.update_clip(clip.id, grade=Grade(linear_exposure=10, master_curve=[[i / 31, i / 31] for i in range(32)]))

    def test_color_vectors_reject_malformed_and_nonfinite(self):
        editor = Editor(sample())
        clip = editor.add_clip('m', 0)
        for name, outside in [('lift', [-1.01, 1.01]), ('gamma', [0, 4.01]), ('gain', [-0.01, 4.01])]:
            invalid = [None, 'rgb', 0, [], [1, 1], [1, 1, 1, 1], (1, 1, 1),
                       [True, 1, 1], ['1', 1, 1], [[1], 1, 1]]
            invalid += [[value, 1, 1] for value in outside + [float('nan'), float('inf'), float('-inf'), 10**400]]
            for value in invalid:
                baseline = editor.project.to_dict()
                with self.subTest(name=name, value=value), self.assertRaises(ValueError):
                    editor.update_clip(clip.id, grade={name: value})
                self.assertEqual(editor.project.to_dict(), baseline)
        for value in [True, '1', None, -10.01, 10.01, float('nan'), float('inf'), 10**400]:
            with self.subTest(linear_exposure=value), self.assertRaises(ValueError):
                editor.update_clip(clip.id, grade={'linear_exposure': value})

    def test_color_curves_reject_malformed_and_nonfinite(self):
        editor = Editor(sample())
        clip = editor.add_clip('m', 0)
        invalid = [None, 'curve', [], [[0, 0]], [[0, 0], [1, 1, 1]],
                   [(0, 0), (1, 1)], [[0, 0], [0, 1], [1, 1]],
                   [[0, 0], [0.7, 0.5], [0.5, 0.5], [1, 1]],
                   [[0.1, 0], [1, 1]], [[0, 0], [0.9, 1]],
                   [[-0.1, 0], [1, 1]], [[0, 0], [1.1, 1]],
                   [[0, -1.01], [1, 1]], [[0, 0], [1, 2.01]],
                   [[False, 0], [1, 1]], [[0, True], [1, 1]],
                   [[i / 32, i / 32] for i in range(33)]]
        for value in [float('nan'), float('inf'), float('-inf'), '0', None, 10**400]:
            invalid += [[[0, 0], [value, 0.5], [1, 1]], [[0, value], [1, 1]]]
        for name in ('master_curve', 'red_curve', 'green_curve', 'blue_curve'):
            for value in invalid:
                baseline = editor.project.to_dict()
                with self.subTest(name=name, value=value), self.assertRaises(ValueError):
                    editor.update_clip(clip.id, grade={name: value})
                self.assertEqual(editor.project.to_dict(), baseline)

    def test_color_defaults_and_snapshots_are_independent(self):
        first, second = Grade(), Grade()
        first.lift[0] = 0.2
        first.master_curve[0][1] = 0.3
        self.assertEqual(second, Grade())
        self.assertEqual(first.red_curve, [[0, 0], [1, 1]])
        editor = Editor(sample())
        clip = editor.add_clip('m', 0)
        editor.update_clip(clip.id, grade=first)
        first.master_curve[0][1] = 0.6
        first.lift[0] = 0.7
        self.assertEqual(editor.project.clips[0].grade.master_curve[0][1], 0.3)
        self.assertTrue(editor.undo())
        self.assertEqual(editor.project.clips[0].grade, Grade())
        editor.project.clips[0].grade.master_curve[0][1] = 0.9
        self.assertTrue(editor.redo())
        self.assertEqual(editor.project.clips[0].grade.master_curve[0][1], 0.3)
        left, right = editor.split_clip(clip.id, 100)
        left.grade.master_curve[0][1] = 0.4
        left.grade.lift[0] = 0.5
        self.assertEqual(right.grade.master_curve[0][1], 0.3)
        self.assertEqual(right.grade.lift[0], 0.2)
        payload = editor.project.to_dict()
        payload['clips'][0]['grade']['master_curve'][0][1] = 0.8
        self.assertEqual(left.grade.master_curve[0][1], 0.4)

    def test_color_schema_remains_strict(self):
        editor = Editor(sample())
        clip = editor.add_clip('m', 0)
        for version in [1, 2]:
            payload = editor.project.to_dict()
            payload['version'] = version; strip_contrast_pivot_field(payload)
            if payload['version'] < 17:
                for old_clip in payload.get('clips', []):
                    old_clip.get('grade', {}).pop('hue_hue_curve', None)
                    old_clip.get('grade', {}).pop('hue_sat_curve', None)
                    old_clip.get('grade', {}).pop('qualifier', None)
            if payload['version'] < 16:
                for old_clip in payload.get('clips', []): old_clip.pop('transform', None)
            payload['clips'][0].pop('restoration')
            payload['clips'][0].pop('stabilization')
            mapping = payload['clips'][0].pop('time_map')
            payload['clips'][0]['source_in'] = mapping['source_in_num']
            payload['clips'][0]['grade']['unknown'] = 1
            with self.subTest(version=version), self.assertRaises(ValueError):
                Project.from_dict(payload)
        baseline = editor.project.to_dict()
        with self.assertRaises(ValueError):
            editor.update_clip(clip.id, grade={'unknown': 1})
        self.assertEqual(editor.project.to_dict(), baseline)


if __name__ == '__main__':
    unittest.main()
