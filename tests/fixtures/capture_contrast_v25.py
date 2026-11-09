"""Capture an actual unchanged schema-25 production oracle in a fresh process.

Run with: python capture_contrast_v25.py PATH_TO_UNMODIFIED_V25_REPO OUTPUT.npz
Never run this against the implementation under test. Source hashes are stored
with the generated fixture so compatibility is not inferred from duplicated math.
"""
from dataclasses import asdict, fields
from pathlib import Path
import hashlib
import json
import sys
import tempfile


def main():
    repository = Path(sys.argv[1]).resolve()
    output = Path(sys.argv[2]).resolve()
    sys.path.insert(0, str(repository))
    import numpy as np
    import lumen.color as color
    from lumen.model import Grade, VERSION
    from lumen.qualifier import QualifierSettings, QualifierRange
    assert VERSION == 25
    assert 'contrast_pivot' not in {field.name for field in fields(Grade)}
    assert Path(color.__file__).resolve() == repository/'lumen/color.py'
    rgb = np.random.default_rng(26025).uniform(-.3, 1.4, (17, 19, 3)).astype(np.float32)
    rgb[0] = np.linspace(-.5, 2, 57, dtype=np.float32).reshape(19, 3)
    rgb[1, :5] = [[0, 0, 0], [1, 1, 1], [.5, .5, .5], [-.1, .18, 1.1], [.081, .018, .409]]
    q = QualifierSettings(enabled=True, hue_center=.97, hue_width=.12,
        hue_softness=.18, saturation_low=.15, saturation_high=.8,
        saturation_softness_low=.1, saturation_softness_high=.2,
        luma_low=.15, luma_high=.8, luma_softness_low=.12, luma_softness_high=.18,
        exposure=.3, saturation=.8, hue_shift=.04, matte_black=.08,
        matte_white=.92, matte_morphology=-.2, matte_smoothing=.3,
        window_shape='ellipse', window_width=1.2, window_height=.8,
        inclusion_ranges=[QualifierRange(hue_enabled=False, luma_low=.2, luma_high=.5)],
        exclusion_ranges=[QualifierRange(hue_center=.5, saturation_enabled=False)])
    rich = dict(linear_exposure=.12, exposure=-.05, lift=[-.01,.015,0],
        gamma=[1.03,.98,1.04], gain=[1.02,.97,1.01], saturation=.87,
        temperature=.08, tint=-.04, shadows=.03, highlights=-.02,
        master_curve=[[0.,-.03],[.3,.32],[.72,.68],[1.,1.04]],
        red_curve=[[0.,0.],[.4,.45],[1.,1.]],
        green_curve=[[0.,.01],[.6,.57],[1.,.98]],
        blue_curve=[[0.,-.01],[.5,.52],[1.,1.01]],
        hue_hue_curve=[[0.,.015],[.4,-.025],[.8,.03],[1.,.015]],
        hue_sat_curve=[[0.,.95],[.35,1.08],[.7,.88],[1.,.95]], qualifier=q)
    grades = [Grade(contrast=value) for value in (0.,.4,1.,1.0000001,1.7,3.)]
    grades += [Grade(**rich, contrast=value, input_encoding=encoding)
        for encoding in ('rec709','srgb','linear_rec709') for value in (.5,1.,1.6)]
    cube = 'LUT_3D_SIZE 2\n'+'\n'.join(
        f'{.1+.8*b} {-.1+1.2*r} {.05+.9*g}'
        for b in (0,1) for g in (0,1) for r in (0,1))+'\n'
    with tempfile.TemporaryDirectory() as directory:
        path=Path(directory)/'original-synthetic.cube'; path.write_text(cube)
        grades += [Grade(**rich,contrast=1.4,lut_path=str(path),lut_strength=strength)
                   for strength in (0.,.37,1.)]
        results=np.stack([color.apply_grade(rgb,g) for g in grades])
        records=[]
        for g in grades:
            record=asdict(g)
            if record['lut_path']: record['lut_path']='__fixture_cube__'
            records.append(record)
    manifest={'schema':VERSION,'numpy':np.__version__, 'source':'unchanged v25 production snapshot',
        'source_sha256':{name:hashlib.sha256((repository/'lumen'/name).read_bytes()).hexdigest()
        for name in ('model.py','color.py','qualifier.py','selective_color.py')}}
    np.savez_compressed(output,rgb=rgb,outputs=results,grades=json.dumps(records),
                        cube=cube,manifest=json.dumps(manifest,sort_keys=True))
    print(json.dumps({'fixture':str(output),'cases':len(records),'manifest':manifest},indent=2))


if __name__ == '__main__': main()
