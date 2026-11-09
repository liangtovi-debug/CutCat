# Native high-bit sticker review evidence

The independent dimension review uses synthetic local images and the actual cached BGRA stream through the production filters. It verifies all-plane transparent identity, untouched support, analytic Rec.709 code-value alpha blending, odd/even edges and 21 dimensions including width two and maximum model dimensions. Small ProRes exports are decoded separately. The JSON pins the reviewed production module SHA-256; it is not a native Windows/macOS result or full-suite count.

From the repository root with its dependencies and FFmpeg installed:

```sh
PYTHONPATH=.:tests QT_QPA_PLATFORM=offscreen python docs/evidence/highbit-stickers/verify_dimensions.py
```

The script writes dimension_review.json beside itself. The narrow canvas correction temporarily pads two rightmost pixels before area reduction and crops afterward, preventing FFmpeg's width-two special case from selecting only one chroma sample.

The acceptance PDF records final full-suite and re-extracted-source results separately. FFmpeg version here identifies this local review environment only; runtime capability probing remains required on each export.
