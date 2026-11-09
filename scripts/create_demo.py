"""Create synthetic, freely reproducible media and an edited demo project."""
from __future__ import annotations
import argparse
from pathlib import Path
import subprocess
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from lumen.media import probe, ffmpeg_binary
from lumen.model import Project, Clip, Grade
from lumen.render import RenderEngine


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('directory', type=Path, help='New directory; existing directories are never overwritten')
    parser.add_argument('--render', action='store_true')
    args = parser.parse_args()
    directory = args.directory.resolve(); directory.mkdir(parents=True, exist_ok=False)
    def ff(*values):
        subprocess.run([ffmpeg_binary(), '-v', 'error', '-nostdin', '-n', *map(str, values)], check=True)
    ff('-f', 'lavfi', '-i', 'testsrc2=s=640x360:r=30:d=6', '-f', 'lavfi', '-i', 'sine=frequency=330:sample_rate=48000:duration=6', '-c:v', 'libx264', '-pix_fmt', 'yuv420p', '-c:a', 'aac', directory / 'motion.mp4')
    ff('-f', 'lavfi', '-i', 'smptebars=s=640x360:r=24:d=4', '-vf', 'scale=in_color_matrix=bt601:out_color_matrix=bt709,format=yuv420p', '-colorspace', 'bt709', '-color_primaries', 'bt709', '-color_trc', 'bt709', '-c:v', 'libx264', '-pix_fmt', 'yuv420p', directory / 'chart.mp4')
    ff('-f', 'lavfi', '-i', 'sine=frequency=220:sample_rate=48000:duration=8', directory / 'music.wav')
    a,b,c = [probe(directory / name) for name in ('motion.mp4', 'chart.mp4', 'music.wav')]
    project = Project(name='First cut · real media demo', width=640, height=360, media=[a,b,c], clips=[
        Clip('motion-1', a.id, 0, 0, 0, 60, gain_db=-9, fade_in=8),
        Clip('motion-2', a.id, 0, 60, 60, 90, grade=Grade(saturation=.55,contrast=1.08), gain_db=-9),
        Clip('motion-3', a.id, 0, 165, 15, 45, grade=Grade(temperature=.2), gain_db=-9, fade_out=12),
        Clip('chart', b.id, 1, 90, 12, 30, grade=Grade(exposure=-.2)),
        Clip('music', c.id, 2, 0, 0, 210, gain_db=-15, fade_in=30, fade_out=30),
    ])
    project.save(directory / 'first-cut.lumen')
    if args.render:
        engine = RenderEngine()
        try: engine.render(project, directory / 'first-cut.mp4')
        finally: engine.close()
    print(directory / 'first-cut.lumen')


if __name__ == '__main__': main()
