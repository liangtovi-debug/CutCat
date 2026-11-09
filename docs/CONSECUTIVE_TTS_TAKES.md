# Consecutive subtitle takes and extended voice checks

## Saved-take recovery after a lane collision

A generated take remains a complete WAV. If its full duration overlaps another
clip, insertion is refused without deleting it. You can choose the other audio
lane, or explicitly move/trim/delete the blocking clip and retry the same saved
take. Trimming an earlier clip is an ordinary, undoable timeline edit; the source
WAV and its immutable synthesis provenance remain intact.

Retry after a timeline edit now revalidates the original Editor session, frame
rate, exact caption ID/text/language/start/duration, take metadata and WAV SHA-256.
Changed/deleted captions, a different/opened project, changed FPS or modified
media cannot silently reuse stale assumptions. Missing or modified WAVs remain
pending so the original file can be restored and checked again. A project edit
during synthesis still prevents automatic insertion. Modal confirmation also
rechecks revision, pending-take identity and file bytes before insertion.

A take longer than its caption still requires explicit permission to extend past
that caption. Captions do not move or extend automatically. No audio is silently
truncated, sped up or shifted to fit. Overlap checks use the complete take's
ceil-rounded timeline duration and half-open clip boundaries.

## Local verification scope

The reproducible extended runner is `scripts/verify_tts_extended_workflow.py`.
It requires an already installed audited runtime and the existing official full
precision Kokoro v1.1 pack. It performs no downloads. Run inside the development
network-denial harness, with a fresh output directory. The source/evidence bundle
contains no model, runtime, dictionaries or font binaries.

Seven additional canonical IDs are sampled: 1 (`af_sol`), 10 (`zf_008`),
30 (`zf_047`), 57 (`zf_099`), 59 (`zm_010`), 80 (`zm_054`), 102 (`zm_100`).
These deliberately sample early/middle/late Chinese entries and the remaining
English-origin entry. They are not statistical quality coverage or invented voice
personalities. Together with the prior four IDs, this is 11 of103 catalog entries,
not a claim that all voices have been listened to or validated for every language.

Original test narration covers a longer Mandarin paragraph and consecutive
caption takes. Reports retain actual generated durations, complete text,
provenance, worker counts, cancellation/error boundaries where tested, explicit
trim/undo/save/relocation and decoded preview/export timing. Exact terminal counts
and measured results belong to the accompanying validation evidence.

All new validation is local Linux source/Qt-offscreen. There is no physical audio
device, native Windows/macOS acceptance or subjective listening-quality claim.
The prior model redistribution and unrestricted Windows runtime telemetry gates
remain; see [runtime audit](LOCAL_TTS_REVALIDATION_AUDIT.md).

## Measured actual acceptance

The frozen extended runner completed successfully using 12 real synthesis workers:
nine auditions and three saved takes. The long Mandarin sample produced
46.425917 seconds of mono 24 kHz PCM. All seven additional catalog IDs produced
non-silent complete output containers; this does not alone prove spoken accuracy.

The real collision/trim/retry sequence inserted the identical pending take without
another worker. A separate alternate take exercised decline/accept on a free lane.
The final two-take project reopened after folder relocation and rendered 183 video
frames. Preview/export used matching graph hashes and identical decoded PCM in
this fixture. Original WAV hashes and provenance stayed unchanged through explicit
trim and undo/redo. The first take is deliberately trimmed by the test, so the
short exported timeline is an editing acceptance fixture, not a narration-quality
showcase. The separate long untrimmed WAV is retained for listening.

This is a per-caption workflow, not a new automatic batch-generation command.
File verification currently hashes synchronously; a large WAV or slow local disk
can briefly block the interface. Native responsiveness remains to be measured.

A secondary local whisper-small transcription reached the long sample's final
sentence. Raw comparison had 24 edits/200 characters including punctuation;
Unicode-normalized punctuation/space removal gave 7 edits/182 characters (3.85%).
Examples include 平凡→频繁, 脚步→脚不 and 河岸→和岸. These can originate in synthesis
or recognition; this single machine cross-check is not a human quality score.
Reference text was not supplied as an inference prompt. Raw output and the
normalization method are retained in evidence.
