# Source-audio separation schema40

Every serialized Clip now requires `source_audio_enabled` (strict boolean) and
`audio_mix_anchor` (`null` or exactly `{track, start, clip_id}`). An anchor has
strict integer track0..3 and start0..2^53−1, plus a nonempty NUL-free string ID.
`AudioMixAnchor` is an immutable in-memory value. The ID records a historical
summation slot, **not a live reference**: deleting the source does not invalidate
it or delete/re-enable anything else.

Versions1–39 reject either new field, including default true/null. Their migrated
values are true/null. All former strict schema fences remain in place. An actual
accepted schema39 serializer fixture and frozen schema39 mixer are kept under
`tests/fixtures/schema39-before-source-audio-separation.json` and
`tests/schema39_audio_mix_fixture.py`; they are not synthesized by downgrading a
schema40 output. Existing schema37/38 fixtures are unchanged.

## Compiler compatibility

Picture layers and FFmpeg input numbering still traverse `(track,start,id)`.
Only final `amix` labels are ordered by `(effective_anchor,current_start,id)`,
where the effective anchor is the stored historical value or the current
`(track,start,id)` for ordinary clips. No-anchor projects keep exactly the old
ordering. A source-audio handoff disables the original contribution and puts its
new independent audio contribution in the same summation slot. Silence remains
the first, duration-defining contribution, with master gain after the same sum.

The shared potential-audibility predicate requires media audio, structural
emission enabled and user mute off. Existing timing may additionally produce a
fully silent interval. Rendering, retime availability, analysis and preservation
proofs use the same structural semantics. Analysis fingerprints include both
fields and a named mix-order algorithm; generated-media provenance checking
remains in force even for muted/suppressed generated clips.

## Editing fences

Generic `update_clip` and generic callbacks cannot change surviving clips'
structural fields or append special-state clips. The exact private separation
transaction validates source video/audio presence, explicit A0/A1, both locks,
no source group or transition endpoint, fresh ID, original gate still enabled,
and byte-for-byte dataclass equality of the expected independent audio Clip.
It may change only the source emission gate and append that approved Clip.
Original source timing and Media are shared; no extraction or endpoint rebase.
The new audio Clip keeps gain/mute/fades and complete time map, with default
picture fields. One undo restores the original project.

Copy keeps a disabled picture disabled, but resets an audio anchor to null:
pasted sound is a new contribution, not another handoff. The private copy append
primitive also rejects supplied historical anchors. Independent move, trim,
slip and speed changes retain gate/anchor. First version conservatively rejects
Split and Insert/Overwrite cuts of special-state clips; whole eligible Insert
translations preserve them. No linked editing, automatic regrouping, standalone
restore or reattach is implied. User unmute never enables structurally disabled
sound. Delete on either side leaves the other side unchanged.
