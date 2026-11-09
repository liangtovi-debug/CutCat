from copy import deepcopy
from media_bins_legacy_fixture import strip_media_bins_fields
from dataclasses import replace
from fractions import Fraction
import sys
import types
import unittest
from unittest.mock import patch

from lumen.model import VERSION, Clip, Editor, Media, Project, VideoTransition
from lumen.clip_composite import ClipComposite
from lumen.clip_motion import ClipMotionKeyframe
from lumen.timing import ClipTimeMap
from lumen.video_transition import plan_video_transitions


def sample():
    return Project(media=[Media('m', '/tmp/example.mp4', 'Example', 20, 1920, 1080, True, True)],
                   clips=[Clip('a', 'm', 0, 10, 30, 60), Clip('b', 'm', 0, 70, 120, 60)])


class VideoTransitionModelTests(unittest.TestCase):
    def setUp(self):
        # Deliberately isolate pure model tests from actual source decode I/O.
        module = types.ModuleType('lumen.video_transition_render')
        module.validate_transition_sources = lambda project: None
        self.stub = patch.dict(sys.modules, {'lumen.video_transition_render': module})
        self.stub.start()
        self.addCleanup(self.stub.stop)

    def editor(self, duration=11, source_path=None):
        project = sample()
        if source_path is not None:
            project.media[0].path = str(source_path)
        editor = Editor(project)
        transition = editor.add_video_transition('a', 'b', duration)
        return editor, transition

    def test_centered_odd_even_windows_and_no_clip_extension(self):
        for duration in (2, 10, 11):
            editor, transition = self.editor(duration)
            plan, = plan_video_transitions(editor.project)
            left = duration // 2
            self.assertEqual((plan.start, plan.end), (70-left, 70+duration-left))
            self.assertEqual((plan.left_source_start, plan.right_source_start), (90-left, 120-left))
            self.assertEqual(editor.project.clips, sample().clips)
            self.assertEqual(editor.project.duration_frames, 130)

    def test_roundtrip_undo_redo_and_noop(self):
        editor, transition = self.editor()
        saved = editor.project.to_dict()
        self.assertEqual(saved['version'], VERSION)
        self.assertEqual(Project.from_dict(saved), editor.project)
        self.assertEqual(editor.revision, 1)
        editor.update_video_transition(transition.id, 11)
        self.assertEqual(editor.revision, 1)
        editor.update_video_transition(transition.id, 12)
        self.assertEqual(editor.revision, 2)
        editor.undo()
        self.assertEqual(editor.project.to_dict(), saved)
        editor.update_video_transition(transition.id, 11)
        self.assertTrue(editor.can_redo)
        editor.redo()
        editor.delete_video_transition(transition.id)
        self.assertFalse(editor.project.video_transitions)
        editor.undo()
        self.assertEqual(editor.project.video_transitions[0].duration_frames, 12)

    def test_decode_failure_is_atomic(self):
        editor = Editor(sample())
        before = editor.project.to_dict()
        with patch('lumen.video_transition_render.validate_transition_sources', side_effect=ValueError('missing handles')):
            with self.assertRaises(ValueError):
                editor.add_video_transition('a', 'b', 10)
        self.assertEqual(editor.project.to_dict(), before)
        self.assertEqual(editor.revision, 0)
        self.assertFalse(editor.can_undo)
        transition = editor.add_video_transition('a', 'b', 10)
        before = editor.project.to_dict()
        with patch('lumen.video_transition_render.validate_transition_sources', side_effect=ValueError('missing handles')):
            with self.assertRaises(ValueError):
                editor.update_video_transition(transition.id, 12)
        self.assertEqual(editor.project.to_dict(), before)
        self.assertEqual(editor.revision, 1)

    def test_edit_guards_atomic_and_remove_first(self):
        operations = [
            lambda e: e.split_clip('a', 40), lambda e: e.trim_clip('a', 31, 59),
            lambda e: e.trim_clip_source('a', 31, 90), lambda e: e.trim_clip_left('a', 1),
            lambda e: e.trim_edge('a', 'left', 1), lambda e: e.roll_cut('a', 71),
            lambda e: e.set_clip_speed('a', 2), lambda e: e.slip_clip('a', 31),
            lambda e: e.delete_clip('a'), lambda e: e.delete_clips(['a', 'b']),
            lambda e: e.ripple_delete_clip('a'), lambda e: e.move_clip('a', 9),
            lambda e: e.update_clip('a', source_in=31, duration=60),
            lambda e: e.update_clip('a', media_id='other'),
            lambda e: e.update_clip('a', composite=ClipComposite(opacity=.5)),
            lambda e: e.update_stabilization('a', enabled=True),
        ]
        for operation in operations:
            editor, transition = self.editor()
            before = editor.project.to_dict()
            with self.assertRaises(ValueError):
                operation(editor)
            self.assertEqual(editor.project.to_dict(), before)
            self.assertEqual(editor.revision, 1)
        editor.delete_video_transition(transition.id)
        editor.slip_clip('a', 31)

    def test_group_move_and_static_grade_allowed(self):
        editor, _ = self.editor()
        editor.move_clips(['a', 'b'], 5)
        self.assertEqual(plan_video_transitions(editor.project)[0].cut, 75)
        editor.update_clip('a', gain_db=-6)
        self.assertEqual(editor.revision, 3)

    def test_strict_schema_and_migrations(self):
        empty = Project().to_dict()
        for version in range(1, 33):
            legacy = {**empty, 'version': version}
            legacy.pop('video_transitions')
            strip_media_bins_fields(legacy)
            legacy.pop('master_gain_db')
            self.assertEqual(Project.from_dict(legacy).video_transitions, [])
            legacy['video_transitions'] = [{'invalid': 'smuggling'}]
            with self.assertRaises(ValueError):
                Project.from_dict(legacy)
        editor, _ = self.editor()
        original = editor.project.to_dict()
        for field, value in [('duration_frames', True), ('duration_frames', 1),
                             ('duration_frames', 2.0), ('kind', 'wipe'), ('id', ''),
                             ('right_clip_id', 'a'), ('bogus', 1)]:
            data = deepcopy(original)
            data['video_transitions'][0][field] = value
            with self.assertRaises(ValueError):
                Project.from_dict(data)
        for value in (None, {}, False):
            data = deepcopy(original)
            data['video_transitions'] = value
            with self.assertRaises(ValueError):
                Project.from_dict(data)
        del original['video_transitions']
        with self.assertRaises(ValueError):
            Project.from_dict(original)

    def test_duplicate_and_overlap(self):
        editor, transition = self.editor()
        before = editor.project.to_dict()
        with self.assertRaises(ValueError):
            editor.add_video_transition('a', 'b', 10)
        self.assertEqual(editor.project.to_dict(), before)
        project = sample()
        project.clips[1].duration = 6
        project.clips.append(Clip('c', 'm', 0, 76, 200, 60))
        project.video_transitions = [VideoTransition('t1', 'a', 'b', 10), VideoTransition('t2', 'b', 'c', 10)]
        with self.assertRaisesRegex(ValueError, 'overlap'):
            project.validate()

    def test_motion_and_window_animation_rejected_even_if_disabled(self):
        from lumen.qualifier import WindowKeyframe
        for field in ('motion', 'window'):
            editor, _ = self.editor()
            before = editor.project.to_dict()
            with self.assertRaises(ValueError):
                if field == 'motion':
                    editor.update_clip('a', motion_keyframes=[ClipMotionKeyframe()])
                else:
                    grade = deepcopy(editor.project.clips[0].grade)
                    grade.enabled = False
                    grade.qualifier.window_keyframes = [WindowKeyframe()]
                    editor.update_clip('a', grade=grade)
            self.assertEqual(editor.project.to_dict(), before)

    def test_current_reader_gate_and_recovery(self):
        from pathlib import Path
        from tempfile import TemporaryDirectory
        from uuid import uuid4
        from lumen.project_recovery import RecoveryStore
        with TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            editor, _ = self.editor(source_path=root / 'example.mp4')
            before = editor.project.to_dict()
            with patch('lumen.model.VERSION', 32):
                with self.assertRaisesRegex(ValueError, 'Unsupported project'):
                    Project.from_dict(before)
            project_path = root / 'transition.lumen'
            editor.project.save(project_path)
            self.assertEqual(Project.load(project_path).to_dict(), before)
            with RecoveryStore(root / 'recovery') as store:
                entry = store.write(editor.project, str(uuid4()), project_path)
                self.assertEqual(store.load(entry).to_dict(), before)

    def test_active_effects_rejected_without_losing_settings(self):
        from lumen.model import ClipEffect
        for kind in ('blur', 'vignette'):
            editor, transition = self.editor()
            before = editor.project.to_dict()
            with self.assertRaisesRegex(ValueError, 'Disable clip effects'):
                editor.add_effect('a', ClipEffect('effect', kind, .5))
            self.assertEqual(editor.project.to_dict(), before)
            editor.add_effect('a', ClipEffect('effect', kind, .5, enabled=False))
            self.assertEqual(editor.project.clips[0].effects[0].amount, .5)
            editor.update_effect('a', 'effect', amount=0, enabled=True)
            editor.project.validate()

    def test_bounds_speed_and_fractional_tail(self):
        for mutate in (lambda p: setattr(p.clips[1], 'source_in', 0),
                       lambda p: setattr(p.clips[0], 'source_in', 540),
                       lambda p: setattr(p.clips[1], 'track', 1),
                       lambda p: setattr(p.clips[1], 'start', 71),
                       lambda p: setattr(p.clips[0], 'time_map', ClipTimeMap(30, Fraction(179, 2))),
                       lambda p: setattr(p.clips[0], 'time_map', ClipTimeMap(30, 150, 2))):
            project = sample()
            mutate(project)
            project.video_transitions = [VideoTransition('t', 'a', 'b', 10)]
            with self.assertRaises(ValueError):
                project.validate()
        editor = Editor(sample())
        with self.assertRaises(ValueError):
            editor.add_video_transition('a', 'b', 122)


if __name__ == '__main__':
    unittest.main()
