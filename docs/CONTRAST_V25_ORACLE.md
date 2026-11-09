# Schema-25 contrast compatibility

The immutable `tests/fixtures/contrast_v25_golden.npz` is a historical Linux
NumPy 2.2.4 capture. Its SHA-256, original ZIP SHA-256, four original module
identities, scenario records, synthetic cube and input float32 bytes are checked.
The old ZIP is not committed to the source tree. In this private repository,
provision it with:

```
python scripts/fetch_contrast_v25_archive.py --output build/private-v25.zip
```

Alternatively set `LUMEN_V25_ARCHIVE` to an already verified local ZIP. Missing
or altered source is a test error, never a skip or a fallback to current code.

The compatibility test runs the unchanged v25 capture script against the actual
old source in a fresh `python -I` process. It verifies all loaded `lumen` module
origins and hashes, NumPy binary identity, Python version, effective CPU features
and power dispatch against the current test process. It then uses the captured
old outputs as expectations for the current implementation's real schema-25
migration. All 18 scenarios and 17,442 float32 values must match bit for bit.
The current implementation never supplies expected outputs.

The static golden's outputs remain immutable historical evidence; differences
between native old output and those Linux bytes are recorded diagnostically.
They are not a universal cross-CPU acceptance oracle. This explicitly changes
that test's scope from cross-environment byte identity to same-runtime product
compatibility. No numerical tolerance is added. Existing scalar contrast and
other product assertions remain unchanged. The original standalone comparison
also records historical differences for default and available restricted CPU
configurations; it does not claim output equivalence between CPU modes.

`tests/test_native_contrast_oracle.py` requires missing/changed sources,
changed inputs, runtime or module identities, mixed imports, and a one-bit
product change to fail. Raw old-process logs and old source archives are excluded
from CI artifacts. Full CI provisions the archive before the exact test suite;
the manual Windows diagnostic runs both the isolated comparison and integrated
compatibility contracts within a 20-minute job with no automatic retry.

The two numeric modules have an exact path-scoped pytest startup profile. Only
when every requested module is one of those two does the suite avoid caption,
font and UI-language initialization. Pillow, Qt and UI localization imports are
rejected at session start and finish in that profile. Any mixed selection or
whole-suite collection retains the original caption and GUI initialization.
This permits the manual numeric diagnostic to install only NumPy and pytest.
The diagnostic separately records the original caption initializer's outcome;
that probe is evidence, not a caption capability test. Collection and shard
pytest logs are retained, while temporary old-source process logs remain excluded.
