"""End-to-end reference project: exact cuts, persisted edits and shared preview."""
from pathlib import Path
import json
import subprocess
import pytest
from lumen.media import probe, ffmpeg_available, ffmpeg_binary, ffprobe_binary
from lumen.model import Project, Editor, Grade
from lumen.render import RenderEngine

pytestmark = pytest.mark.skipif(not ffmpeg_available(), reason='FFmpeg and ffprobe required')


def run(*args):
    return subprocess.run([str(x) for x in args], capture_output=True, check=True)


def frame(path, n):
    return run(ffmpeg_binary(), '-v', 'error', '-i', path, '-vf', f'select=eq(n\\,{n})', '-frames:v', '1', '-pix_fmt', 'rgb24', '-f', 'rawvideo', 'pipe:1').stdout


def test_saved_project_render_matches_and_frame_count(tmp_path):
    src = tmp_path / 'mixed-rate source.mp4'
    run(ffmpeg_binary(), '-v', 'error', '-f', 'lavfi', '-i', 'testsrc2=s=64x48:r=24:d=3', '-f', 'lavfi', '-i', 'sine=frequency=550:sample_rate=44100:duration=3', '-c:v', 'libx264', '-pix_fmt', 'yuv420p', '-c:a', 'aac', src)
    media = probe(src)
    ed = Editor(Project(width=64, height=48, fps_num=30000, fps_den=1001))
    ed.add_media(media)
    clip = ed.add_clip(media.id, 0)
    ed.trim_clip(clip.id, 7, 60)
    left, right = ed.split_clip(clip.id, 23)
    ed.update_clip(right.id, grade=Grade(contrast=1.1, saturation=.7, linear_exposure=.3, gamma=[1.03, 1, .98], master_curve=[[0, 0], [.5, .56], [1, 1]]), gain_db=-6, fade_out=5)
    original = ed.project.to_dict()
    path = tmp_path / 'edit.lumen'; ed.project.save(path)
    loaded = Project.load(path)
    assert loaded.duration_frames == 60
    assert loaded.to_dict() == original
    engine = RenderEngine()
    try:
        first = engine.render(ed.project, tmp_path / 'before.mp4')
        second = engine.render(loaded, tmp_path / 'after.mp4')
        info = json.loads(run(ffprobe_binary(), '-v', 'error', '-select_streams', 'v:0', '-count_frames', '-show_entries', 'stream=nb_read_frames,r_frame_rate,duration', '-of', 'json', second).stdout)['streams'][0]
        assert int(info['nb_read_frames']) == 60
        assert info['r_frame_rate'] == '30000/1001'
        assert abs(float(info['duration']) - 60 * 1001 / 30000) < .00001
        preview = engine.preview(loaded)
        for index in [0, 22, 23, 59]:
            assert frame(first, index) == frame(second, index)
            a,b = frame(first,index), frame(preview,index)
            assert len(a) == len(b) == 64*48*3
            assert sum(abs(x-y) for x,y in zip(a,b))/len(a) < 7
    finally:
        engine.close()
