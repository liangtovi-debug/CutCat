# Local 3D .cube LUT workflow

In the Professional workspace, select a picture clip and open Color. Load a local `.cube` look, adjust **LUT strength** from 0 to 1, or **Remove LUT**. The chooser parses the file before changing the project: malformed/unsupported data produces an error and leaves the previous look/history intact. No download, upload or online LUT service is used.

Supported subset: UTF-8/BOM text `.cube`, one 3D table with size 2–65, optional TITLE and DOMAIN_MIN/DOMAIN_MAX. Red varies fastest in table rows. Trilinear interpolation is used. 1D LUTs, combined shaper/3D files, unknown headers and other file formats are rejected. A camera Log or HDR conversion LUT is not automatically a valid Rec.709 creative look; input color-management support remains the separately documented SDR scope.

The stage is after master/RGB curves in encoded Rec.709 RGB, before final output clipping/quantization. Strength uses `(1-s)*preLUT + s*LUT(preLUT)`. LUT sampling clamps input to the declared table domain; the interpolation result and blend do not add output clipping. At zero, the stage is exactly bypassed and does not read even a missing LUT; at one, the previous full-strength behavior is preserved. This is a creative-look blend, not scene-linear exposure or a camera-input transform.

Old projects without `lut_strength` load with 1.0, preserving prior appearance. Strength/path edits use ordinary undo/redo and project serialization. Paths remain local project-relative references; saving does not embed or redistribute another party's LUT. Keep the referenced file with the project. Active file changes invalidate the render cache by content fingerprint. Source/output overwrite protection still includes a referenced LUT even when its strength is zero.

Preview frame extraction, timeline preview and export share the same evaluator. Tests cover endpoints/midpoint, domain behavior, non-finite/out-of-range strength rejection, old-project default, save/reopen, undo, valid/invalid UI import and decoded preview/export agreement. Native packaged smoke uses an original tiny 3D LUT at strength 0.4; exact completed run evidence is reported separately.
