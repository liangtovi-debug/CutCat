"""Deterministic roll-edit property matrix; pure model/planning, no media I/O.

Run from the repository root: PYTHONPATH=. python scripts/verify_roll_properties.py
This is an additional check, not 240 pytest tests or decoded-media acceptance.
"""
from copy import deepcopy
from fractions import Fraction as F
from lumen.model import Clip, Editor, Media, Project
from lumen.timing import ClipTimeMap, duration_frames, sample_boundary
from lumen.audio_timing import compile_audio_timing
rates = (F(1,4), F(1,2), F(2,3), F(1), F(3,2), F(2), F(4))
checked=0
for lr in rates:
  for rr in rates:
    lm=ClipTimeMap(F(2,3), F(88,3), lr)
    rm=ClipTimeMap(F(1001,7), F(1253,7), rr)
    left=Clip('left','m',0,7,lm.source_in,duration_frames(lm),time_map=lm)
    right=Clip('right','m',0,left.end,rm.source_in,duration_frames(rm),time_map=rm)
    p=Project(media=[Media('m','/tmp/nonexistent.mp4','m',1000,64,48,True,True)],clips=[right,left])
    for cut in (left.start+1, left.end-1, left.end, left.end+1, right.end-1):
      if not left.start<cut<right.end: continue
      e=Editor(deepcopy(p)); before=deepcopy(e.project)
      try: a,b=e.preview_roll_cut('left',cut)
      except ValueError: continue
      assert e.project==before and not e.can_undo
      delta=cut-left.end
      assert a.start==left.start and a.end==b.start==cut and b.end==right.end
      assert a.duration==left.duration+delta and b.duration==right.duration-delta
      for old,new in ((left,a),(right,b)):
        assert old.duration*old.speed-(old.source_out-old.source_in)==new.duration*new.speed-(new.source_out-new.source_in)
      for fps in (F(24),F(30),F(24000,1001),F(30000,1001)):
        ap,bp=compile_audio_timing(a,fps),compile_audio_timing(b,fps)
        assert ap.start_sample+ap.sample_count==bp.start_sample
        assert ap.sample_count+bp.sample_count==sample_boundary(right.end,fps)-sample_boundary(left.start,fps)
      e.roll_cut('left',cut); after=deepcopy(e.project)
      if delta:
        assert e.undo() and e.project==before
        assert e.redo() and e.project==after
      assert Project.from_dict(e.project.to_dict())==e.project
      checked+=1
print(f'Independent in-memory roll properties passed: {checked} valid combinations, four FPS audio-budget checks each; no file modifications.')
