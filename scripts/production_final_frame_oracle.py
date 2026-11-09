"""Current full graph versus still integration, on the unchanged production tools.

This is not an independent historical-code oracle. In Windows acceptance both
paths exercise FFmpeg 9; exact FFmpeg 7 code regression is a separate check.
"""
from copy import deepcopy
import os
from pathlib import Path

from scripts.production_range_oracle import full_av


def compare_frames(project, directory, frames=None):
    from lumen.final_frame_preview import capture_final_frame_snapshot
    before = deepcopy(project.to_dict())
    environment = dict(os.environ)
    frames = list(range(project.duration_frames) if frames is None else frames)
    assert frames and all(type(f) is int and 0 <= f < project.duration_frames for f in frames)
    # Full, unwindowed graph; never obtain expected bytes from the still backend.
    raw, _pcm = full_av(project, Path(directory) / 'full')
    evidence = os.environ.get('RANGE_ORACLE_EVIDENCE')
    if evidence:
        import shutil
        import uuid
        destination = Path(evidence)/'production-final-frame'
        destination.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(Path(directory)/'full/runtime.json', destination/(uuid.uuid4().hex + '.json'))
    size = project.width * project.height * 3 // 2
    for frame in frames:
        result = capture_final_frame_snapshot(project, frame, timeout=120)
        assert (result.width, result.height) == (project.width, project.height)
        assert len(result.yuv420p) == size
        assert len(result.rgb_bytes) == project.width * project.height * 3
        assert result.yuv420p == raw[frame*size:(frame+1)*size], f'production still YUV mismatch frame={frame}'
    assert project.to_dict() == before
    assert dict(os.environ) == environment
