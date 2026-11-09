import sys,json,argparse
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
parser=argparse.ArgumentParser(description='Synthetic detail-scale experiment; no face inference')
parser.add_argument('--output',type=Path,required=True)
args=parser.parse_args()
import numpy as np
from lumen.beauty_prototype import apply_beauty_prototype as apply
y,x=np.mgrid[:96,:128].astype(np.float32);mask=np.zeros((96,128),np.float32);mask[8:-8,8:-8]=1;pro=np.zeros_like(mask);pro[30:40,40:80]=1
records=[]
for freq in [.15,.6,1.4]:
 for amp in [.002,.008,.025]:
  texture=amp*np.sin(x*freq)*np.cos(y*freq)
  image=np.repeat((.5+texture)[...,None],3,axis=2).astype(np.float32)
  roi=(mask==1)&(pro==0);roi[:12]=False;roi[-12:]=False;roi[:,:12]=False;roi[:,-12:]=False
  for detail in [0,.75,1]:
   out=apply(image,mask,pro,enabled=True,smoothing_strength=.8,detail_protection=detail)
   records.append({'freq':freq,'amplitude':amp,'detail':detail,'texture_rms_before':float(np.sqrt(np.mean((image[roi]-.5)**2))),'texture_rms_after':float(np.sqrt(np.mean((out[roi]-.5)**2))),'max_delta':float(np.max(np.abs(out-image)))})
for radius in [1,2,4,8]:
 spot=.035*np.exp(-((x-64)**2+(y-48)**2)/(2*radius**2));im=np.repeat((.5-spot)[...,None],3,axis=2).astype(np.float32);out=apply(im,mask,pro,enabled=True,smoothing_strength=.8,detail_protection=.75)
 records.append({'spot_radius':radius,'center_reduction':float((out[48,64,0]-im[48,64,0])/(.5-im[48,64,0]))})
print(json.dumps(records,indent=2));args.output.write_text(json.dumps(records,indent=2)+'\n',encoding='utf-8')
