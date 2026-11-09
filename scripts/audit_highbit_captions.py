#!/usr/bin/env python3
"""Independent actual-binary drawtext audit. Writes evidence only, no repo edits."""
import argparse, hashlib, json, os, pathlib, shutil, subprocess, sys
import numpy as np
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--output', default='highbit-caption-audit')
parser.add_argument('--font', default='/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf')
args = parser.parse_args()
OUT = pathlib.Path(args.output).resolve()
OUT.mkdir(parents=True, exist_ok=True)
SOURCE = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SOURCE))
from lumen.media import ffmpeg_binary
FFMPEG = shutil.which(ffmpeg_binary())
FONT = str(pathlib.Path(args.font).resolve(strict=True))
W,H=1024,128
results={
 'binary':FFMPEG,
 'binary_sha256':hashlib.sha256(pathlib.Path(FFMPEG).read_bytes()).hexdigest(),
 'version':subprocess.check_output([FFMPEG,'-version'],text=True).splitlines()[0],
 'font':FONT,
 'font_sha256':hashlib.sha256(pathlib.Path(FONT).read_bytes()).hexdigest(),
 'dimensions':[W,H],
 'color_domain':'Nonlinear (transfer-encoded) BT.709 Y\u2032CbCr; limited range. Not linear-light compositing or HDR.',
}
OUT.joinpath('ffmpeg-version.txt').write_bytes(subprocess.check_output([FFMPEG,'-version']))
Y=np.tile(np.arange(W,dtype=np.uint16),(H,1))
U=np.tile(np.arange(W//2,dtype=np.uint16),(H,1)) + ((np.arange(H,dtype=np.uint16)%2)*512)[:,None]
V=1023-U
ramp=np.concatenate([Y.ravel(),U.ravel(),V.ravel()]).astype('<u2')
black=np.concatenate([np.full(W*H,64,np.uint16),np.full(W*H,512,np.uint16)]).astype('<u2')
OUT.joinpath('ramp-yuv422p10le.raw').write_bytes(ramp.tobytes())
params='setparams=range=limited:colorspace=bt709:color_trc=bt709:color_primaries=bt709'
draw=f'drawtext=fontfile={FONT}:text=10BIT RAMP:fontsize=32:fontcolor=white:borderw=2:bordercolor=black:x=32:y=80'

def run(name, vf, raw=ramp, frames=1, allow_auto=False):
    cmd=[FFMPEG,'-hide_banner','-loglevel','verbose','-nostdin','-y','-filter_threads','1']
    if not allow_auto: cmd += ['-noauto_conversion_filters']
    cmd += ['-f','rawvideo','-pixel_format','yuv422p10le','-video_size',f'{W}x{H}','-framerate','30','-i','pipe:0',
            '-vf',vf,'-frames:v',str(frames),'-pix_fmt','yuv422p10le','-f','rawvideo','pipe:1']
    proc=subprocess.run(cmd,input=raw.tobytes()*frames,capture_output=True,timeout=30)
    OUT.joinpath(name+'.log').write_bytes(proc.stderr)
    OUT.joinpath(name+'.command.json').write_text(json.dumps(cmd,indent=2)+'\n')
    if proc.returncode: raise RuntimeError(name+': '+proc.stderr.decode()[-2000:])
    OUT.joinpath(name+'.raw').write_bytes(proc.stdout)
    return np.frombuffer(proc.stdout,dtype='<u2').reshape(frames,-1), proc.stderr.decode()

def planes(flat):
    return [flat[:W*H].reshape(H,W),flat[W*H:W*H*3//2].reshape(H,W//2),flat[W*H*3//2:].reshape(H,W//2)]

def outside_stats(flat):
    stats=[]
    for src,dst,label in zip(planes(ramp),planes(flat),'YUV'):
        delta=dst[:64].astype(np.int32)-src[:64].astype(np.int32)
        stats.append({'plane':label,'outside_tested_samples':int(delta.size),'changed_samples':int(np.count_nonzero(delta)),
                      'maximum_absolute_error':int(np.abs(delta).max()),'unique_top64':int(np.unique(dst[:64]).size),
                      'source_unique_top64':int(np.unique(src[:64]).size),'low_two_bits_nonzero':int(np.count_nonzero(dst[:64]%4))})
    return stats

out,log=run('native-gated-10bit',f'format=yuv422p10le,{params},{draw}:enable=eq(n\\,1),format=yuv422p10le',frames=3)
results['native']={'automatic_converters_in_log':'auto_scale' in log,
                   'disabled_frames_byte_exact':bool(np.array_equal(out[0],ramp) and np.array_equal(out[2],ramp)),
                   'active_frame_changed_samples':int(np.count_nonzero(out[1]!=ramp)),
                   'active_frame_outside_caption':outside_stats(out[1])}
forced,log=run('forced-8bit-negative-control',f'{params},format=yuv422p,{draw},format=yuv422p10le',allow_auto=True)
results['forced_8bit_negative_control']={'automatic_converters_in_log':'auto_scale' in log,'outside_caption':outside_stats(forced[0])}

results['colors']={}
solid=f'drawtext=fontfile={FONT}:text=HHHH:fontsize=80:x=16:y=16:fontcolor='
for label,color,pre,post in [
 ('red_unset','0xFF0000','',''),('red_before709','0xFF0000',params+',',''),
 ('red_after709','0xFF0000','',','+params),('red_before601','0xFF0000','setparams=range=limited:colorspace=smpte170m,',''),
 ('green709','0x00FF00',params+',',''),('blue709','0x0000FF',params+',',''),
 ('white709','0xFFFFFF',params+',',''),('gray128709','0x808080',params+',',''),
 ('white_half709','white@0.5',params+',',''),('white_half_linear_tag','white@0.5',params.replace('color_trc=bt709','color_trc=linear')+',',''),
]:
    data,log=run(label,pre+solid+color+post,raw=black)
    y,u,v=planes(data[0]); yy,xx=np.unravel_index(np.argmax(y),y.shape)
    results['colors'][label]={'solid_yuv':[int(y[yy,xx]),int(u[yy,xx//2]),int(v[yy,xx//2])],
                              'pixel':[int(xx),int(yy)],'max_luma':int(y.max()),
                              'sha256':hashlib.sha256(data[0].tobytes()).hexdigest()}

# Native 16-bit blending source formula, independently evaluated for opaque white.
# ff_draw_color scales 8-bit normalized limited range by 1023, not bit-shifting
# legal 8-bit endpoints by two. ff_blend_mask alpha for opaque 255 -> 256,
# coverage 255 -> 65280. Thus opaque white over 64 is 939, not 943 or 940.
white_native=int((235/255)*1023+.5)
alpha=((0x101*255+2)>>8)*255
opaque_predicted=((0x10001-alpha)*64+alpha*white_native)>>16
half_alpha=((0x101*127+2)>>8)*255
half_predicted=((0x10001-half_alpha)*64+half_alpha*white_native)>>16
results['blend_math']={'native_opaque_white_source':white_native,'opaque_coverage_weight':alpha/65536,
 'opaque_white_over_64_predicted':opaque_predicted,'opaque_white_over_64_observed':results['colors']['white709']['max_luma'],
 'half_white_over_64_predicted':half_predicted,'half_white_over_64_observed':results['colors']['white_half709']['max_luma'],
 'transfer_tag_changes_pixels':results['colors']['white_half709']['sha256']!=results['colors']['white_half_linear_tag']['sha256']}

formats=['yuv420p','yuv422p','yuv444p','yuv420p9le','yuv422p9le','yuv444p9le','yuv420p10le','yuv422p10le','yuv444p10le',
         'yuv422p12le','yuv444p12le','yuv422p14le','yuv444p14le','yuv422p16le','yuv444p16le',
         'gbrp10le','gbrp12le','gbrp16le','gbrap16le','rgb24','rgb48le','rgba64le','gray10le','gray16le',
         'yuv422p10be','gbrp16be','nv12','p010le','yuyv422']
results['representative_native_formats']={}
for fmt in formats:
    cmd=[FFMPEG,'-hide_banner','-loglevel','verbose','-nostdin','-noauto_conversion_filters','-filter_threads','1',
         '-f','lavfi','-i',f'nullsrc=s=64x64,format={fmt}','-vf',f'drawtext=fontfile={FONT}:text=T:fontsize=24,format={fmt}',
         '-frames:v','1','-f','null','-']
    proc=subprocess.run(cmd,capture_output=True,timeout=30)
    OUT.joinpath('format-'+fmt+'.log').write_bytes(proc.stderr)
    results['representative_native_formats'][fmt]=proc.returncode==0

OUT.joinpath('results.json').write_text(json.dumps(results,indent=2)+'\n')
print(json.dumps(results,indent=2))
