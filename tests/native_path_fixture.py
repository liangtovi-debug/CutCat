"""Explicit native paths for synthetic, missing project dependencies in tests."""
from pathlib import Path, PurePosixPath


def bind_missing_project_paths(project, root):
    """Call only for synthetic projects, before edits, plans or history snapshots.

    Preserve shared references and distinct files with the same basename. Do not
    use this on real media fixtures or tests of foreign/relative path handling.
    No assets are created, and no expected state or assertion is normalized.
    """
    root = (Path(root) / 'missing assets 素材').resolve()
    references = [(media, 'path') for media in project.media]
    references += [(clip.grade, 'lut_path') for clip in project.clips if clip.grade.lut_path]
    references += [(overlay, 'asset_path') for overlay in project.overlays]
    if project.caption_style.font_directory:
        references.append((project.caption_style, 'font_directory'))
    # Capture original values before writing: multiple clips may own the same
    # Grade object, so a later read could otherwise see an already rebound LUT.
    references = [(owner, field, getattr(owner, field)) for owner, field in references]
    paths = {}
    for owner, field, old in references:
        if old not in paths:
            paths[old] = str(root / str(len(paths)) / PurePosixPath(old).name)
        setattr(owner, field, paths[old])
