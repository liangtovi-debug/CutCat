"""Bounded 48 kHz stereo finite-signal estimated true peak, before encoding.

ITU-R BS.1770-5 Annex 2 (11/2023), printed pages 18–19:
https://www.itu.int/dms_pubrec/itu-r/rec/bs/R-REC-BS.1770-5-202311-I!!PDF-E.pdf
The published four phases each contain twelve taps. For each phase p,
y[p,n] = sum(h[p,k] * x[n-k], k=0..11). Float64 arithmetic needs neither
integer headroom attenuation nor its compensating gain. Initial and final
signal extension is zero; eleven zero input frames flush the complete tail.

This is a finite-filter, four-times-oversampled estimate, not an ideal
continuous reconstruction or delivery certification. The reported peak is
explicitly max(original sample peak, reconstructed FIR peak): the sample
lower-bound safeguard avoids understating a known original sample because
of the finite interpolator response. Raw FIR peaks remain independently
inspectable. There is no clipping, gain change, peak event/time inference,
or retained program audio. Input is already the renderer's 48 kHz stereo PCM.
"""
from __future__ import annotations

import numpy as np

# Rows transcribed in published table order (tap, phase); exact binary fractions.
_TAPS = np.array([
    [ .0017089843750, -.0291748046875, -.0189208984375, -.0083007812500],
    [ .0109863281250,  .0292968750000,  .0330810546875,  .0148925781250],
    [-.0196533203125, -.0517578125000, -.0582275390625, -.0266113281250],
    [ .0332031250000,  .0891113281250,  .1015625000000,  .0476074218750],
    [-.0594482421875, -.1665039062500, -.2003173828125, -.1022949218750],
    [ .1373291015625,  .4650878906250,  .7797851562500,  .9721679687500],
    [ .9721679687500,  .7797851562500,  .4650878906250,  .1373291015625],
    [-.1022949218750, -.2003173828125, -.1665039062500, -.0594482421875],
    [ .0476074218750,  .1015625000000,  .0891113281250,  .0332031250000],
    [-.0266113281250, -.0582275390625, -.0517578125000, -.0196533203125],
    [ .0148925781250,  .0330810546875,  .0292968750000,  .0109863281250],
    [-.0083007812500, -.0189208984375, -.0291748046875,  .0017089843750],
], dtype=np.float64)
_TAPS.setflags(write=False)
_BLOCK_FRAMES = 8192


class TruePeakEstimator:
    """Single-use stereo meter; finish is idempotent, feeding after it is not."""

    def __init__(self):
        self._history = np.zeros((11, 2), dtype=np.float64)
        self.raw_peaks = np.zeros(2, dtype=np.float64)
        self.sample_peaks = np.zeros(2, dtype=np.float64)
        self._finished = False

    def _filter(self, block):
        values = np.concatenate((self._history, block))
        # A single bounded phase/channel output at a time; no 4x program buffer.
        for channel in range(2):
            for phase in range(4):
                output = np.convolve(values[:, channel], _TAPS[:, phase], mode='valid')
                self.raw_peaks[channel] = max(self.raw_peaks[channel], float(np.max(np.abs(output))))
        self._history[:] = values[-11:]

    def feed(self, pcm):
        if self._finished:
            raise ValueError('Cannot feed a finalized true-peak estimator.')
        pcm = np.asarray(pcm)
        if pcm.ndim != 2 or pcm.shape[1] != 2:
            raise ValueError('True-peak input must be stereo PCM.')
        for start in range(0, len(pcm), _BLOCK_FRAMES):
            block = np.asarray(pcm[start:start + _BLOCK_FRAMES], dtype=np.float64)
            if not np.isfinite(block).all():
                raise ValueError('True-peak input contains non-finite samples.')
            # Actual input is float32; fail closed also for unrepresentable
            # float64 reconstruction rather than publishing an infinite meter.
            if np.max(np.abs(block)) > np.finfo(np.float64).max / 16:
                raise ValueError('True-peak input exceeds safe arithmetic range.')
            self.sample_peaks = np.maximum(self.sample_peaks, np.max(np.abs(block), axis=0))
            self._filter(block)

    def finish(self) -> tuple[float, float]:
        if not self._finished:
            self._filter(np.zeros((11, 2), dtype=np.float64))
            self._finished = True
        return tuple(float(value) for value in np.maximum(self.sample_peaks, self.raw_peaks))
