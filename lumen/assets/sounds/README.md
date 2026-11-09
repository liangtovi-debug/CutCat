# CutCat original sound starter pack

24 offline-ready, original procedural effects. No downloads, recordings,
third-party samples, voices, music excerpts, or AI model assets are used.
The atmospheric effects are synthetic textures, not field recordings.

| Category | Count | Sound design |
| --- | ---: | --- |
| UI | 5 | Short decaying tones, noise clicks, a pitch pop, and a two-note switch |
| Transitions | 5 | Shaped noise sweeps, rising/falling chirps, reverse swell, sparkle notes |
| Impacts | 5 | Low sine thuds, inharmonic wood knock, modulated digital hit, decaying bounces |
| Notifications | 5 | Harmonic chimes, a rising success phrase, two-note alert, inharmonic glass bell |
| Atmosphere | 4 | Filtered-noise breeze/rain/room textures and a slowly modulated space drone |

All original files are 48 kHz, mono, signed 16-bit little-endian PCM WAV. Durations range
from 0.09 to 8 seconds. Each file reserves at least 6 dB of headroom, with
quieter UI and atmospheric sounds. Files are not loudness-normalized against
one another: adjust clip gain to suit the mix. Combining multiple sounds can
still overload a mix, and digital headroom is not a guarantee of safe listening
volume. Start preview playback at a comfortable device volume.

Oscillators, chirps, harmonic/inharmonic partials, seeded PCG64 noise, boxcar
filters, attack/decay envelopes, and short echoes are implemented in
`scripts/create_original_sounds.py`. Noise is seeded independently from each
sound's stable ID. DC is removed before global edge shaping; squared-sine
fades make both boundary samples zero. Atmosphere clips fade in and out and
are **not advertised as seamless loops**. Assets contain no embedded user data.

## Regenerate and verify

From the repository root, using the project's pinned Python/NumPy environment:

```sh
python scripts/create_original_sounds.py
python -m pytest tests/test_sound_assets.py
```

To generate elsewhere without changing the bundled files:

```sh
python scripts/create_original_sounds.py --output /path/to/output
```

`catalog.json` version 1 records bilingual names/tags, category, basename,
SHA-256 of the complete WAV, decoded sample rate/count, measured peak dBFS,
origin, and license. Duration is exactly sample_count / sample_rate.
The generator uses no clock, filesystem-order input, network, or global random
state. Tests regenerate the entire pack and compare every byte to the checked-in
assets using the project's pinned environment.

See `LICENSE.txt`: original pack assets are offered under CC0-1.0. This does not
change the application's license or grant rights in unrelated material.

## Separate third-party collection

53 external CC0 sounds are described in `THIRD_PARTY_README.md` and loaded from
`third_party_catalog.json`. They are not covered by this original-pack provenance
claim. The original 24 WAVs, `catalog.json` and `LICENSE.txt` remain unchanged.
