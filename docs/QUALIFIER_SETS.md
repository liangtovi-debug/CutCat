# Bounded qualifier range sets (project schema 20)

The existing primary H/S/Y range remains the base inclusion. Up to eight
additional inclusions and eight exclusions are independent range records:
`inclusion_ranges` and `exclusion_ranges`. Both default to empty arrays.
Each record contains exactly nine numeric fields: hue center, half-width and
softness, saturation low/high/softness, and encoded Rec.709 luma
low/high/softness. There are no per-range adjustments, windows or enable flags.
Records are immutable typed `QualifierRange` values in memory; containers are
independently copied when constructing settings. Serialized records reject
unknown/missing fields, invalid types, nonfinite values and invalid bounds.

## Evaluation

Each range multiplies its H, HSV S and encoded Rec.709 Y′ weights. Feathers
are exterior linear ramps, with inclusive core plateaus. Hue wraps circularly.
Half-width 0.5 bypasses hue, including for gray; narrower ranges exclude chroma
at or below 1e-6. Extended finite RGB outside [0,1] never enters any range.

The primary and additional inclusion weights combine by maximum, not addition.
The result is multiplied by one minus the maximum exclusion weight. An empty
exclusion set has weight zero. Duplicate ranges and list order do not change
the key. This preserves disjoint colors without filling the gap between them.
Exclusions cannot create a selection. Then, and only then, the existing single
spatial window multiplies the combined key. One shared adjustment follows.
Memory remains bounded by the existing 65,536-pixel blocks and the fixed record
limits; this is not a node graph, multiple corrections or semantic detection.

Empty extra arrays preserve the legacy numerical path exactly. Disabled,
neutral, extended-range and zero-key pixels retain bit-exact bypass behavior.

## Sampling and editing

Replace sampling resets the primary core and clears both extra arrays. Add and
Subtract append an independently sampled range, never expand one interval across
unrelated hues. All three use ±10° hue and ±0.1 S/Y core widths with S/Y clipped
to [0,1]; new records inherit the primary feathers. Existing shared adjustment,
window and enablement remain unchanged. Invalid/gray/transparent/outside samples
are rejected without changing data or consuming capacity. At eight records in
the chosen set, a valid append raises an explicit full-set error.

`clear_qualifier_ranges` removes only extras and retains the primary range.
Removing an exclusion restores any previously selected underlying color.

## Persistence

Schema 20 requires both arrays and exact complete range records. Schema 18
requires exactly its historical no-window qualifier fields. Schema 19 requires
exactly its historical window-capable fields. Both migrate to empty extra
arrays; neither may smuggle even empty future arrays. Older schemas without
qualifiers retain their existing disabled defaults. Older readers reject new
schema versions rather than silently discarding the selection sets. Project
save/load, grade copy/paste, undo/redo and recovery retain the complete records.
