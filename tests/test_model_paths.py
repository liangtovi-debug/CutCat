"""Project paths retain their targets across native aliases and package moves."""
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path

import pytest

from lumen.model import (
    Caption, CaptionStyle, Clip, ClipEffect, Grade, Media, Overlay,
    OverlayKeyframe, Project,
)


def project_at(root, overlay_digest):
    return Project(
        name='Portable 世界',
        media=[Media('media', str(root / 'assets' / 'source.mp4'), 'Source',
                     2, 1920, 1080, True, True, color_space='bt709')],
        clips=[Clip('clip', 'media', 0, 5, 10, 30, gain_db=-4,
                    grade=Grade(lut_path=str(root / 'assets' / 'look.cube'),
                                linear_exposure=1.5, lift=[.1, -.1, .2]),
                    effects=[ClipEffect('vignette', 'vignette', .25)])],
        overlays=[Overlay('overlay', str(root / 'assets' / '世界.png'),
                          overlay_digest, 6, 20, z_order=-3,
                          keyframes=[OverlayKeyframe(0, 10, -20, 2, .5),
                                     OverlayKeyframe(19, 20, 10, 1, 1)])],
        captions=[Caption('caption', 7, 15, 'Hello 世界')],
        caption_style=CaptionStyle(font_directory=str(root / 'fonts')),
    )


@pytest.mark.parametrize('through_alias', [False, True])
def test_canonical_roundtrip_and_moved_assets_preserve_all_data(tmp_path, through_alias):
    package = tmp_path.resolve() / 'original'
    package.mkdir()
    (package / 'assets').mkdir()
    (package / 'fonts').mkdir()
    # Actual bytes make source protection meaningful without requiring decoders.
    inputs = {
        'assets/source.mp4': b'Original video source',
        'assets/look.cube': b'Original color lookup table',
        'assets/世界.png': b'Original overlay image',
        'fonts/example.ttf': b'Original caption font',
    }
    for relative, content in inputs.items():
        (package / relative).write_bytes(content)
    base = package
    if through_alias:
        base = package.parent / 'directory-alias'
        try:
            base.symlink_to(package, target_is_directory=True)
        except (OSError, NotImplementedError) as exc:
            pytest.skip(f'Directory symlinks unavailable: {exc}')
        assert base != base.resolve()  # Reproduce /var and /tmp aliases anywhere.

    digest = hashlib.sha256(inputs['assets/世界.png']).hexdigest()
    project = project_at(base, digest)
    original = deepcopy(project)
    destination = base / 'project.lumen'
    project.save(destination)
    assert project == original  # Save must not rewrite the live project's paths.
    data = json.loads(destination.read_text(encoding='utf-8'))
    assert data['media'][0]['path'] == 'assets/source.mp4'
    assert data['clips'][0]['grade']['lut_path'] == 'assets/look.cube'
    assert data['overlays'][0]['asset_path'] == 'assets/世界.png'
    assert data['caption_style']['font_directory'] == 'fonts'
    expected = project_at(package, digest)
    restored = Project.load(destination)
    assert restored == expected
    assert restored.to_dict() == expected.to_dict()
    assert Project.load(package / 'project.lumen') == expected
    assert restored.missing_media() == []

    for relative, content in inputs.items():
        with pytest.raises(ValueError, match='overwrite'):
            project.save(base / relative)
        assert (package / relative).read_bytes() == content

    if through_alias:
        base.unlink()
    moved = package.with_name('moved')
    package.rename(moved)
    reopened = Project.load(moved / 'project.lumen')
    expected_moved = project_at(moved, digest)
    assert reopened == expected_moved
    assert reopened.to_dict() == expected_moved.to_dict()
    assert reopened.missing_media() == []
    assert not package.exists()
    for relative, content in inputs.items():
        assert (moved / relative).read_bytes() == content
    (moved / 'assets' / 'source.mp4').unlink()
    assert [media.id for media in reopened.missing_media()] == ['media']


@pytest.mark.skipif(os.name != 'nt', reason='Requires native Windows drive semantics')
def test_windows_other_drive_paths_remain_absolute(tmp_path):
    tmp_path = tmp_path.resolve()
    other_drive = 'Y' if tmp_path.drive.upper() == 'Z:' else 'Z'
    root = Path(f'{other_drive}:\\lumen-path-tests').resolve()
    assert root.drive.upper() != tmp_path.drive.upper()
    project = project_at(root, 'a' * 64)
    original = deepcopy(project)
    destination = tmp_path / 'project.lumen'
    project.save(destination)
    data = json.loads(destination.read_text(encoding='utf-8'))
    assert data['media'][0]['path'] == project.media[0].path
    assert data['clips'][0]['grade']['lut_path'] == project.clips[0].grade.lut_path
    assert data['overlays'][0]['asset_path'] == project.overlays[0].asset_path
    assert data['caption_style']['font_directory'] == project.caption_style.font_directory
    assert project == original
    assert Project.load(destination) == original
