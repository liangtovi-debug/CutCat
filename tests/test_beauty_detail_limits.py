"""Counterexamples documenting supplied-mask smoothing limits, not face QA."""
import numpy as np
import pytest
from lumen.beauty_prototype import apply_beauty_prototype


def fixture(freq, amplitude):
    y,x=np.mgrid[:96,:128].astype(np.float32)
    image=np.repeat((.5+amplitude*np.sin(x*freq)*np.cos(y*freq))[...,None],3,axis=2).astype(np.float32)
    skin=np.zeros((96,128),np.float32);skin[8:-8,8:-8]=1
    protection=np.zeros_like(skin);protection[30:40,40:80]=1
    roi=(skin==1)&(protection==0);roi[:12]=False;roi[-12:]=False;roi[:,:12]=False;roi[:,-12:]=False
    return image,skin,protection,roi


def apply(image,skin,protection,detail,strength=.8):
    return apply_beauty_prototype(image,skin,protection,enabled=True,smoothing_strength=strength,detail_protection=detail)


@pytest.mark.parametrize('freq',[.15,.6,1.4],ids=['coarse','medium','fine'])
@pytest.mark.parametrize('amplitude',[.002,.008,.025],ids=['faint','moderate','strong'])
def test_texture_scale_protection_is_a_tradeoff_not_identity(freq,amplitude):
    image,skin,protection,roi=fixture(freq,amplitude)
    before=float(np.sqrt(np.mean((image[roi]-.5)**2)))
    errors=[]
    for detail in [0,.75,1]:
        out=apply(image,skin,protection,detail)
        np.testing.assert_array_equal(out[(skin==0)|(protection>0)],image[(skin==0)|(protection>0)])
        assert np.all(np.isfinite(out))
        errors.append(float(np.sqrt(np.mean((out[roi]-.5)**2))))
    # A bounded fixture-specific retention relation, not a universal theorem.
    assert errors[0] <= errors[1]+1e-7 <= errors[2]+2e-7
    assert errors[2] <= before+1e-7


def test_high_detail_setting_still_removes_real_fine_texture():
    image,skin,protection,roi=fixture(1.4,.025)
    before=float(np.sqrt(np.mean((image[roi]-.5)**2)))
    for detail,minimum_loss in [(.75,.35),(1.,.25)]:
        out=apply(image,skin,protection,detail)
        after=float(np.sqrt(np.mean((out[roi]-.5)**2)))
        assert 1-after/before > minimum_loss


def test_same_pixels_cannot_distinguish_unwanted_noise_from_desired_texture():
    image,skin,protection,_=fixture(1.4,.008)
    desired_texture=image.copy(); unwanted_noise=image.copy()
    np.testing.assert_array_equal(apply(desired_texture,skin,protection,.75),apply(unwanted_noise,skin,protection,.75))


def test_fixed_pixel_radius_does_not_remove_large_spots():
    y,x=np.mgrid[:96,:128].astype(np.float32)
    _,skin,protection,_=fixture(.6,.008)
    corrections=[]
    for radius in [1,2,4,8]:
        spot=.035*np.exp(-((x-64)**2+(y-48)**2)/(2*radius**2))
        image=np.repeat((.5-spot)[...,None],3,axis=2).astype(np.float32)
        out=apply(image,skin,protection,.75)
        corrections.append(float((out[48,64,0]-image[48,64,0])/(.5-image[48,64,0])))
    assert .25<corrections[0]<.32
    assert .06<corrections[1]<.11
    assert corrections[2:]==[0.,0.]


@pytest.mark.parametrize('baseline',[-.2,.05,.2,.5,.9,1.2],ids=['negative','dark','low','middle','bright','overrange'])
def test_encoded_level_offsets_do_not_introduce_whitening(baseline):
    image,skin,protection,_=fixture(.6,.008)
    image+=(baseline-.5)
    out=apply(image,skin,protection,.75)
    # This weak texture remains below the detail floor at these offsets.
    np.testing.assert_array_equal(out,image)


@pytest.mark.parametrize('detail',[0,.75,1])
def test_strength_sweep_scales_correction_without_unintended_threshold_jump(detail):
    image,skin,protection,_=fixture(1.4,.025)
    full=apply(image,skin,protection,detail,1.)
    for strength in [0,.001,.1,.5,.999,1.]:
        out=apply(image,skin,protection,detail,strength)
        np.testing.assert_allclose(out,image+np.float32(strength)*(full-image),atol=6e-8,rtol=0)
