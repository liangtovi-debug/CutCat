"""Measure the unchanged decoded-preview/reference path, not a GPU claim."""
import os,sys,time,json,resource
from pathlib import Path
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
ROOT=Path(__file__).resolve().parents[1]
BASE=Path(os.environ.get('LUMEN_COLOR_BENCHMARK_OUTPUT', Path.cwd()/'.color-performance-benchmark')).resolve()
(BASE/'evidence').mkdir(parents=True,exist_ok=True)
sys.path.insert(0,str(ROOT))
import numpy as np
from PySide6.QtCore import QSize
from PySide6.QtGui import QImage
from PySide6.QtMultimedia import QVideoFrame,QVideoFrameFormat as F
from PySide6.QtWidgets import QApplication
from lumen.preview_color import preview_frame_image
from lumen.ui_color_reference import ReferenceStill,FrameProvenance,VideoViewer
from lumen.color_scopes import analyze_qimage
app=QApplication([])
def stat(xs):return dict(samples_ms=xs,median_ms=float(np.median(xs)),p95_ms=float(np.percentile(xs,95)))
results=[]
for w,h in [(1920,1080),(3840,2160)]:
 fmt=F(QSize(w,h),F.PixelFormat.Format_YUV420P);fmt.setColorSpace(F.ColorSpace.ColorSpace_BT709);fmt.setColorRange(F.ColorRange.ColorRange_Video);fmt.setColorTransfer(F.ColorTransfer.ColorTransfer_BT709)
 frame=QVideoFrame(fmt);assert frame.map(QVideoFrame.MapMode.WriteOnly)
 for plane,rows,cols,code in [(0,h,w,120),(1,h//2,w//2,90),(2,h//2,w//2,170)]:
  dest=np.ndarray((rows,cols),np.uint8,buffer=frame.bits(plane),strides=(frame.bytesPerLine(plane),1));dest[:]=code
 frame.unmap()
 prov=FrameProvenance(0,33333,0,0,'synthetic-local-benchmark','graded',1,())
 image=preview_frame_image(frame)
 viewer=VideoViewer();viewer.resize(1280,720);viewer.set_image(image,prov);viewer.reference=ReferenceStill.capture(image,prov);viewer.show();app.processEvents()
 result={'width':w,'height':h,'viewer_size':[1280,720],'frames':60,'warmup':5}
 for label,fn in [('mapped_yuv709_conversion',lambda:preview_frame_image(frame)),('reference_capture',lambda:ReferenceStill.capture(image,prov)),('sampled_scopes',lambda:analyze_qimage(image))]:
  for _ in range(5):fn()
  values=[]
  for _ in range(60):t=time.perf_counter();fn();values.append((time.perf_counter()-t)*1000)
  result[label]=stat(values)
 for mode in ['off','side_by_side','wipe']:
  viewer.set_comparison_mode(mode);app.processEvents()
  for _ in range(5):viewer.grab()
  values=[]
  for _ in range(60):t=time.perf_counter();viewer.grab();values.append((time.perf_counter()-t)*1000)
  result['paint_'+mode]=stat(values)
 result['process_peak_rss_mib']=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss/1024
 viewer.close();results.append(result)
(BASE/'evidence/viewer-benchmark.json').write_text(json.dumps({'note':'Unchanged code diagnostic. Offscreen CPU timings; scopes retain pre-existing disclosed 256px sampling; render frames remain full resolution. Reference paint measured at 1280x720 UI viewport. Not native GPU/playback certification.','results':results},indent=2))
print(json.dumps(results))
