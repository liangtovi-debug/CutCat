"""Missing fixtures keep reference identity and complete persistence semantics."""
from copy import deepcopy
from pathlib import Path
from uuid import uuid4

from lumen.model import Clip, Grade, Media, Overlay, Project
from lumen.project_recovery import RecoveryStore
from native_path_fixture import bind_missing_project_paths


def test_aliases_distinct_basenames_and_all_nonpath_fields_survive(tmp_path):
    project = Project(
        media=[Media('a', '/missing/a/source.mov', 'A', 20),
               Media('b', '/missing/b/source.mov', 'B', 20)],
        clips=[Clip('a', 'a', 0, 0, 0, 10, grade=Grade(lut_path='/missing/look.cube')),
               Clip('b', 'b', 1, 0, 0, 10, grade=Grade(lut_path='/missing/look.cube'))],
        overlays=[Overlay('a', '/missing/sticker.png', 'a' * 64, 0, 10),
                  Overlay('b', '/missing/sticker.png', 'a' * 64, 10, 10)])
    project.caption_style.font_directory = '/missing/fonts'
    before = deepcopy(project)
    bind_missing_project_paths(project, tmp_path)
    assert project.media[0].path != project.media[1].path
    assert project.clips[0].grade.lut_path == project.clips[1].grade.lut_path
    assert project.overlays[0].asset_path == project.overlays[1].asset_path
    pairs = [(a, b, 'path') for a, b in zip(before.media, project.media)]
    pairs += [(a.grade, b.grade, 'lut_path') for a, b in zip(before.clips, project.clips)]
    pairs += [(a, b, 'asset_path') for a, b in zip(before.overlays, project.overlays)]
    pairs.append((before.caption_style, project.caption_style, 'font_directory'))
    for original, actual, field in pairs:
        path = Path(getattr(actual, field))
        assert path.is_absolute() and str(path.resolve()) == str(path)
        assert tmp_path.resolve() in path.parents and not path.exists()
        setattr(original, field, str(path))
    assert before == project
    saved = tmp_path / 'aliases.lumen'
    project.save(saved)
    assert Project.load(saved) == project
    with RecoveryStore(tmp_path / 'recovery') as store:
        assert store.load(store.write(project, str(uuid4()), saved)) == project


def test_shared_grade_and_independent_grade_keep_the_same_lut_reference(tmp_path):
    shared = Grade(lut_path='/missing/look.cube', exposure=.25)
    independent = Grade(lut_path='/missing/look.cube', exposure=-.5)
    project = Project(
        media=[Media('m', '/missing/source.mov', 'Source', 20, has_audio=True)],
        clips=[Clip('a', 'm', 0, 0, 0, 10, grade=shared),
               Clip('b', 'm', 1, 0, 0, 10, grade=shared),
               Clip('c', 'm', 2, 0, 0, 10, grade=independent)])
    before = deepcopy(project)
    bind_missing_project_paths(project, tmp_path)
    assert project.clips[0].grade is project.clips[1].grade is shared
    assert project.clips[2].grade is independent and independent is not shared
    assert shared.lut_path == independent.lut_path
    for path in (project.media[0].path, shared.lut_path):
        assert str(Path(path).resolve()) == path
        assert tmp_path.resolve() in Path(path).parents and not Path(path).exists()
    before.media[0].path = project.media[0].path
    for clip in before.clips:
        clip.grade.lut_path = shared.lut_path
    assert before == project
    saved = tmp_path / 'shared-grade.lumen'
    project.save(saved)
    assert Project.load(saved) == project
    with RecoveryStore(tmp_path / 'recovery') as store:
        assert store.load(store.write(project, str(uuid4()), saved)) == project
